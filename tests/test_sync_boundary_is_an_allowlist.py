"""同步的邊界是白名單，不是掃描器（`SECURITY_MODEL.md`）。

安全模型把這件事講得很清楚：

> 「the secret scanner will catch it」 is a net, not a plan.

計畫是 `EXACT_FILES` ＋ `PREFIX_DIRS` 這份白名單——**沒有列到的東西不會上傳，
跟它的內容無關**。掃描器是那之後的一層網，用來擋住列在名單上、但這一次剛好
夾帶了金鑰的檔案。

而在這之前，只有網有測試（`test_secret_like_file_is_blocked_not_uploaded`），
**沒有任何一條驗白名單本身**。差別在改壞的時候會怎樣：

* 有人把它改成黑名單，或加一條 `.aicc` 的寬前綴 → 決策紀錄、客戶姓名、
  匯入的對話全部開始上雲，而它們沒有一個長得像 API key，掃描器一個都攔不住。
* 那時候唯一會發現的方式，是有人去看 Drive 資料夾。

所以這個檔案大半在測「哪些**不會**上去」。反方向也要有一條，否則「什麼都不
同步」也會全部通過。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.test_cloud_sync import configured_sync, make_project  # noqa: E402

# 安全模型「Banned Or High-Risk Approaches」那一節點名的東西，逐一落成檔案。
# 內容刻意寫成掃描器不會有意見的樣子——要驗的是「沒列到就不上傳」，
# 不是「有金鑰會被攔下來」。那一條已經有自己的測試。
NEVER_SYNCED = {
    ".env": "FEATURE_FLAG=1\n",
    ".venv/pyvenv.cfg": "home = C:/Python\n",
    ".claude/settings.json": '{"theme": "dark"}\n',
    ".codex/config.toml": "model = 'gpt-5'\n",
    ".aicc/runtime/workflow.lock": '{"run_id": "r1"}\n',
    ".aicc/decisions/decisions.jsonl": '{"action": "ASK_USER"}\n',
    ".aicc/knowledge/PITCH.md": "# 對外素材\n",
    ".aicc/knowledge/index.sqlite": "not really sqlite\n",
    ".aicc/imported-context/客戶對話.md": "# 舊對話\n王先生說……\n",
    "客戶清單.txt": "一般文字，沒有金鑰。\n",
}


def _write(project: Path, relative: str, body: str) -> None:
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _sync(tmp_path: Path, monkeypatch):
    service, drive = configured_sync(tmp_path, monkeypatch)
    project = make_project(tmp_path)
    for relative, body in NEVER_SYNCED.items():
        _write(project, relative, body)
    result = service.sync_project(project)
    assert result.success, result.errors
    return result, drive, project


# --- 沒列到的不會上去 --------------------------------------------------------


def test_nothing_outside_the_allowlist_is_uploaded(tmp_path, monkeypatch) -> None:
    result, _, _ = _sync(tmp_path, monkeypatch)

    for relative in NEVER_SYNCED:
        assert relative not in result.uploaded, relative


def test_nothing_outside_the_allowlist_lands_in_the_drive_folder(
    tmp_path, monkeypatch
) -> None:
    """看實際的資料夾，不只看回報。

    「回報裡沒有」跟「檔案不在雲端」是兩件事，而會害到人的是後者。
    """
    result, drive, _ = _sync(tmp_path, monkeypatch)
    remote = drive / "AIControlCenter" / "projects" / result.project_id

    landed = {
        str(path.relative_to(remote)).replace("\\", "/")
        for path in remote.rglob("*")
        if path.is_file()
    }
    for relative in NEVER_SYNCED:
        assert relative not in landed, f"{relative} 上雲了：{sorted(landed)}"


def test_the_decision_log_stays_behind(tmp_path, monkeypatch) -> None:
    """決策紀錄有自己的授權匯出路徑，刻意不隨同步走。

    單獨拉出來寫，因為它跟上面那些不一樣：它不是「危險所以不搬」，
    是**搬它要留下誰授權的紀錄**。混在一堆排除項裡，下一個人只會看到
    「哦，另一個不同步的檔案」，然後在某次「順手把 .aicc 都加進去」時
    把它一起帶上雲。
    """
    result, drive, _ = _sync(tmp_path, monkeypatch)
    remote = drive / "AIControlCenter" / "projects" / result.project_id

    assert not (remote / ".aicc" / "decisions").exists()


def test_the_pitch_is_not_shared_memory(tmp_path, monkeypatch) -> None:
    # 同一個資料夾裡，但它是對外素材，不是共同記憶（ROADMAP P2 更正過一次）。
    result, _, _ = _sync(tmp_path, monkeypatch)

    assert ".aicc/knowledge/PITCH.md" not in result.uploaded


# --- 反方向：該上去的還是要上去 ----------------------------------------------


def test_the_shared_memory_files_do_sync(tmp_path, monkeypatch) -> None:
    """否則「什麼都不同步」也會讓上面每一條通過。"""
    service, drive = configured_sync(tmp_path, monkeypatch)
    project = make_project(tmp_path)
    for name in ("PROJECT_BRIEF.md", "DECISIONS.md", "OPEN_QUESTIONS.md"):
        _write(project, f".aicc/knowledge/{name}", f"# {name}\n")

    result = service.sync_project(project)

    for name in ("PROJECT_BRIEF.md", "DECISIONS.md", "OPEN_QUESTIONS.md"):
        assert f".aicc/knowledge/{name}" in result.uploaded, result.uploaded


def test_a_new_file_under_an_allowed_prefix_does_sync(
    tmp_path, monkeypatch
) -> None:
    # `PREFIX_DIRS` 是刻意的：那幾個資料夾底下的東西整批可攜。
    service, _ = configured_sync(tmp_path, monkeypatch)
    project = make_project(tmp_path)
    _write(project, ".aicc/notes/隨手記.md", "# 筆記\n")

    result = service.sync_project(project)

    assert ".aicc/notes/隨手記.md" in result.uploaded, result.uploaded


# --- 白名單本身的形狀 --------------------------------------------------------


def test_no_allowed_prefix_swallows_the_whole_aicc_folder(tmp_path) -> None:
    """一條 `.aicc` 的前綴就會讓上面所有排除項一次全部失效。

    這是最可能發生的那種改法——「為什麼 notes 可以 runs 可以，
    那整個 .aicc 一起吧」——而它不會讓任何既有測試變紅。
    """
    from app.services.cloud_sync import GoogleDriveFolderSync as CloudSyncService

    for prefix in CloudSyncService.PREFIX_DIRS:
        parts = Path(prefix).parts
        assert parts != (".aicc",), f"{prefix} 會把整個 .aicc 都同步上去"
        assert len(parts) >= 2, prefix

    for relative in CloudSyncService.EXACT_FILES:
        assert Path(relative).name != "*", relative
