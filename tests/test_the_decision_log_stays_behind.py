"""搬機器時決策紀錄不跟著走，而新機要講出這件事。

實測匯出一個用過的專案，包裡沒有 `.aicc/decisions/decision-log.jsonl`
——`decisions` 不在 `ALLOWED_AICC_PREFIXES`，雲端白名單也一樣。兩邊一致，
所以那是**刻意的政策**，而且是好的政策：稽核紀錄只從會驗證它、也會講清楚
界線的那道門出去（決策紀錄頁自己的匯出）。

**這裡修的不是政策，是沒有人講。** 接手清單原本四條（文件／git／LINE／對話），
一句都沒提它。後果是這樣的：

1. 新機的雜湊鏈從 GENESIS 重新開始。
2. 之後從新機匯出給稽核的檔案會顯示「N 筆通過驗證」。
3. 那句話是**真的**，而且看起來完整——它不會說「還有更早的在另一台機器上」。

而那份檔案的用途正是拿給別人看。這是這個專案一直在抓的形狀：一個正確、
可信、而且剛好把最重要的部分漏掉的答案。

作法照 `_documents_step` 的既有辦法：匯出時把筆數記進 manifest，新機拿它比。
**只看「這台現在有幾筆」分不出「沒搬過來」和「本來就沒有」**，而這兩者要講的
話完全相反。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.services.handover import handover_steps
from app.services.long_task import DecisionLogEntry, DecisionLogStore
from app.services.project_registry import ProjectRegistry
from app.services.workspace_portability import WorkspacePortabilityService

ENTRIES = 3


def _project(root: Path, name: str = "old", entries: int = ENTRIES) -> Path:
    project = root / name
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("任務\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)
    store = DecisionLogStore(project)
    for index in range(entries):
        store.append(
            DecisionLogEntry(
                run_id=f"run-{index}", action="ANSWERED",
                question=f"問題 {index}", answer="可以",
                answered_by="user", reason="測試",
            )
        )
    return project


def _round_trip(tmp_path: Path, entries: int = ENTRIES) -> Path:
    """匯出再匯入，回傳新機上的專案路徑。"""
    source = _project(tmp_path, entries=entries)
    out = tmp_path / "out"
    out.mkdir()
    exported = WorkspacePortabilityService().export_project(source, out)
    assert exported.success, exported.errors
    imported = WorkspacePortabilityService().import_project(
        Path(exported.archive_path), tmp_path / "new"
    )
    assert imported.success, imported.errors
    return Path(imported.project_path)


# --- 政策：它真的不跟著走 ----------------------------------------------------


def test_the_decision_log_is_not_in_the_package(tmp_path: Path) -> None:
    source = _project(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    exported = WorkspacePortabilityService().export_project(source, out)

    names = zipfile.ZipFile(exported.archive_path).namelist()
    assert not [n for n in names if "decisions" in n], names
    # 前提檢查：包裡本來就有東西，不是匯出整個失敗。
    assert ".aicc/task.md" in names


def test_the_new_machine_starts_its_own_chain(tmp_path: Path) -> None:
    arrived = _round_trip(tmp_path)

    assert DecisionLogStore(arrived).list_entries() == []


# --- 而新機要講出來 ----------------------------------------------------------


def test_the_checklist_says_the_log_stayed_behind(tmp_path: Path) -> None:
    """這一組存在的理由。"""
    arrived = _round_trip(tmp_path)

    steps = {step.key: step for step in handover_steps(arrived)}

    assert "decisions" in steps, sorted(steps)
    assert str(ENTRIES) in steps["decisions"].detail, steps["decisions"].detail


def test_it_says_where_to_get_the_full_one(tmp_path: Path) -> None:
    # 一句「留在舊機」沒有下一步的話，讀的人只會知道自己少了東西。
    arrived = _round_trip(tmp_path)

    detail = {s.key: s.detail for s in handover_steps(arrived)}["decisions"]

    assert "舊機" in detail
    assert "匯出" in detail


def test_it_warns_that_later_exports_will_look_complete(tmp_path: Path) -> None:
    """最重要的那一句：之後從這台匯出的檔案**不會**說自己少了東西。

    少了這句，這條提醒會被讀成「舊資料沒了，算了」，而真正的風險是
    **拿一份看起來完整的紀錄去給稽核**。
    """
    arrived = _round_trip(tmp_path)

    detail = {s.key: s.detail for s in handover_steps(arrived)}["decisions"]

    assert "不會包含" in detail or "從頭開始" in detail, detail


def test_it_is_not_a_todo(tmp_path: Path) -> None:
    """它是一件已經發生的事，不是待辦。

    標成未完成的話，接手清單永遠收不起來——而一份永遠收不起來的清單，
    使用者會學會整份忽略，包含裡面真的要做的那幾條。
    """
    arrived = _round_trip(tmp_path)

    step = {s.key: s for s in handover_steps(arrived)}["decisions"]

    assert step.done is True


# --- 不要無中生有 ------------------------------------------------------------


def test_a_project_with_no_decisions_says_nothing(tmp_path: Path) -> None:
    # 舊機本來就沒有紀錄，就沒有東西留在那邊。
    arrived = _round_trip(tmp_path, entries=0)

    assert "decisions" not in {step.key for step in handover_steps(arrived)}


def test_an_old_archive_without_the_count_says_nothing(tmp_path: Path) -> None:
    """舊版 AICC 匯出的壓縮檔沒有這個欄位。

    那時要說的是「不知道」，而不是「0 筆」——但「不知道」在這裡沒有可以講的
    內容（講不出數字的提醒只會讓人困惑），所以就不出現。
    **這條釘住的是它不會因為缺欄位而報一個假的 0 或炸掉。**
    """
    arrived = _round_trip(tmp_path)
    marker_path = arrived / ".aicc" / "handover.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    del marker["decision_entries"]
    marker_path.write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")

    steps = handover_steps(arrived)

    assert "decisions" not in {step.key for step in steps}
    # 其餘幾條要照常出現——缺一個欄位不該讓整份清單消失。
    assert steps


def test_a_project_that_did_not_move_has_no_checklist(tmp_path: Path) -> None:
    # 前提檢查：清單只對搬過來的專案出現。
    assert handover_steps(_project(tmp_path, name="local")) == ()


# --- 匯出端真的有記下那個數字 ------------------------------------------------


@pytest.mark.parametrize("count", [1, 4])
def test_the_manifest_records_how_many_were_left_behind(
    tmp_path: Path, count: int
) -> None:
    """數字要跟著舊機變，不是寫死的。

    只驗一個值的話，一個寫死的常數也會通過。
    """
    source = _project(tmp_path, name=f"old{count}", entries=count)
    out = tmp_path / f"out{count}"
    out.mkdir()

    exported = WorkspacePortabilityService().export_project(source, out)

    manifest = json.loads(
        zipfile.ZipFile(exported.archive_path).read("AICC_EXPORT_MANIFEST.json")
    )
    assert manifest["decision_entries"] == count
