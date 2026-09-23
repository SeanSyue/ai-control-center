"""搬到新電腦之後，帶過去的東西還是不是原來那個（ROADMAP P5）。

這個檔案是查「新電腦接手 wizard 該說什麼」查出來的。原本想寫的第一句是
「到了新機要先重建索引」，而查下去發現**那句話本身是一個 bug**：索引本來
就應該跟著走，而且檔案確實在壓縮檔裡，只是裡面是空的。

兩件事都屬於同一種：**東西看起來搬過去了，其實沒有。**

  1. `index.sqlite` 是 WAL 模式，剛寫進去的東西還在 `index.sqlite-wal`。
     而 `-wal` 被 `EXCLUDED_NAMES` 刻意排除（它是暫存檔）。所以匯出打包的是
     一個結構完整、內容是空的索引：檔案在、專案開得起來、文件全不見了。
     沒有任何一步失敗過。

  2. `sessions.json` 裡有兩種東西，只有一種可攜。session id 是舊機 CLI 的
     本機紀錄，跟著搬過去的話，新機第一次執行會去 resume 一個這裡不存在的
     對話——不會爆掉，只會得到一個對自己「正在延續」的東西毫無記憶的模型。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.lineup import Lineup  # noqa: E402
from app.services.project_knowledge import ProjectKnowledgeStore  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from app.services.workspace_portability import (  # noqa: E402
    WorkspacePortabilityService,
)


def _old_machine(tmp_path: Path) -> Path:
    project = tmp_path / "old" / "案子"
    project.mkdir(parents=True)
    ProjectRegistry.initialize_project(project)
    (project / "note.md").write_text(
        "# 開戶\n需要護照與地址證明。\n", encoding="utf-8"
    )
    ProjectKnowledgeStore(project).rebuild_project_index()
    return project


def _move(tmp_path: Path, project: Path) -> Path:
    """Export then import into a different directory, as a new machine would."""
    service = WorkspacePortabilityService()
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    exported = service.export_project(project, out)
    assert exported.success, exported.errors
    landing = tmp_path / "new"
    landing.mkdir(exist_ok=True)
    imported = service.import_project(Path(exported.archive_path), landing)
    assert imported.success, imported.errors
    return Path(imported.project_path)


# --- 索引 -------------------------------------------------------------------


def test_the_documents_are_still_there_on_the_new_machine(tmp_path: Path) -> None:
    """The bug, said the way the user would notice it."""
    old = _old_machine(tmp_path)
    assert ProjectKnowledgeStore(old).list_documents(), "舊機就沒有文件，這條會空轉"

    new = _move(tmp_path, old)

    store = ProjectKnowledgeStore(new)
    assert store.database_path.is_file(), "索引檔根本沒跟過來"
    assert store.list_documents(), "索引檔在，但裡面是空的"
    assert store.search("護照"), "搜尋在新機上什麼都找不到"


def test_an_index_that_cannot_be_folded_up_stops_the_export(
    tmp_path: Path, monkeypatch
) -> None:
    """匯出寧可失敗，也不要交出一個空的索引。

    「打包成功了，但你的文件沒有跟過來」是這整個檔案在防的形狀，所以
    checkpoint 失敗必須是**匯出失敗**，不是一行警告。
    """
    old = _old_machine(tmp_path)
    monkeypatch.setattr(ProjectKnowledgeStore, "checkpoint", lambda self: False)

    out = tmp_path / "out"
    out.mkdir()
    result = WorkspacePortabilityService().export_project(old, out)

    assert not result.success
    assert any("索引" in error for error in result.errors), result.errors


def test_checkpoint_reports_failure_instead_of_raising(tmp_path: Path) -> None:
    # 沒有資料庫的專案不該讓匯出炸掉，但也不能回報成功。
    project = tmp_path / "空專案"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    store = ProjectKnowledgeStore(project)
    # 指到一個不存在的路徑，而不是刪掉真的那個：store 開著連線，Windows 上
    # 刪不掉，而那個 PermissionError 跟這條要驗的事情無關。
    store.database_path = project / ".aicc" / "knowledge" / "不存在.sqlite"

    assert store.checkpoint() is False


def test_checkpoint_leaves_the_data_readable(tmp_path: Path) -> None:
    # 反方向：把 WAL 摺回去不可以是「清掉」。
    old = _old_machine(tmp_path)
    store = ProjectKnowledgeStore(old)
    before = {d.display_name for d in store.list_documents()}

    assert store.checkpoint() is True
    assert {d.display_name for d in store.list_documents()} == before
    assert before


# --- sessions.json ----------------------------------------------------------


def _seed_sessions(project: Path) -> None:
    """Put the old machine's session ids where they actually live.

    第一版只塞了 `selected` 和 `sessions` 兩塊，而**那兩塊是從陣容算出來的**：
    `load()` 會用 `lineup` 重算 `selected`，所以塞在那裡的東西下一次讀就沒了。
    負向驗證因此判「只清 sessions、selected 留著」沒有牙齒——案例太順，
    我把 id 放在一個它本來就不會存活的地方。真正的位置是 `AgentSlot.session_id`。
    """
    registry = SessionRegistry(project)
    payload = registry.load()
    lineup = (
        Lineup.classic()
        .with_planner()
        .with_session("author", "sess-OLD")
        .with_session("reviewer", "cx-OLD")
        .with_identity("author", "資深後端")
    )
    payload["lineup"] = lineup.to_list()
    payload["sessions"] = {
        "claude": [{"session_id": "sess-OLD", "title": "舊機的對話"}],
        "codex": [],
    }
    registry.save(payload)
    # 前提檢查：id 真的存進去了，否則下面每一條都是在驗空氣。
    reloaded = SessionRegistry(project).load_lineup()
    assert reloaded.session_for_role("author") == "sess-OLD"


def test_the_old_machines_session_ids_do_not_travel(tmp_path: Path) -> None:
    old = _old_machine(tmp_path)
    _seed_sessions(old)

    new = _move(tmp_path, old)

    registry = SessionRegistry(new)
    payload = registry.load()
    lineup = registry.load_lineup()

    # 真正存 id 的地方先驗，因為下面兩塊是從它算出來的。
    assert [slot.session_id for slot in lineup] == ["", "", ""], lineup.to_list()
    assert payload["selected"] == {"claude": "", "codex": ""}, payload["selected"]
    assert payload["sessions"] == {"claude": [], "codex": []}, payload["sessions"]
    assert "sess-OLD" not in (new / ".aicc" / "sessions.json").read_text(
        encoding="utf-8"
    )
    # 不是靠檔案沒被打包——它有被打包，而且應該被打包（裡面有陣容）。
    assert (new / ".aicc" / "sessions.json").is_file()


def test_the_line_up_does_travel(tmp_path: Path) -> None:
    """把整個檔案排除掉是另一個修法，而且是比較差的那個。

    陣容是使用者自己排的——誰上場、順序、每一格的 identity——而且完全可攜。
    連它一起丟掉，等於用一個資料遺失換掉另一個。
    """
    old = _old_machine(tmp_path)
    _seed_sessions(old)
    before = SessionRegistry(old).load_lineup()
    assert "planner" in before.roles, "測試資料沒排到規劃者，這條分不出預設值"

    new = _move(tmp_path, old)
    after = SessionRegistry(new).load_lineup()

    assert after.roles == before.roles
    assert [slot.provider for slot in after] == [slot.provider for slot in before]
    # identity 也要活著：清 session 順手把它抹掉，是那種「還是會跑，只是跑的
    # 人變了」的無聲損失。
    assert after.identity_for("author") == "資深後端"


def test_the_stored_project_path_points_at_this_machine(tmp_path: Path) -> None:
    # 舊機的絕對路徑留在檔案裡，會讓之後每一個讀它的人拿到一條不存在的路。
    old = _old_machine(tmp_path)
    _seed_sessions(old)

    new = _move(tmp_path, old)

    stored = json.loads(
        (new / ".aicc" / "sessions.json").read_text(encoding="utf-8")
    )["project_path"]
    assert Path(stored) == new.resolve(), stored
    assert str(old) not in stored
