r"""一條「沒有一格會寫檔案」的線，要**停下來並講出來**，不是丟 traceback。

## 這一條是老闆的問題撞出來的

他 2026-08-25 問：

> 一般使用者像我的話可以自己選擇要不要寫入文件
> 但又有另一個問題　這樣我輸出的東西不寫進文件的話
> 那不就不能繼續傳給下一個 node 了嗎

今天沒有「這一格要不要寫檔」的勾選框——**唯一做得到的，是到陣容頁把那一格
的職責從「執行」改掉**。照著做，然後按下開始執行：

    AttributeError: 'NoneType' object has no attribute 'success'

## 而那個結局早就寫好了，只是到不了

`_no_worker_result()` 整支 docstring 都在解釋這個結局該怎麼收
（「**停下來，不要挑一格代打**」），`app/i18n.py` 有現成的那句話
（「沒有人做事／這條線上沒有一格會寫檔案」），`outcomes` 那張表也有它的列。
**只有從那裡到畫面的那一段是斷的。**

`_build_result()` 裡 `codex_result` 每一處都防了 None（三處），
`claude_result` 一處都沒有，而兩個參數的型別宣告一樣。
**不對稱本身就是那個 bug 的樣子。**

## 2026-08-25 之前，`tests/` 裡沒有任何一條碰過 `NO_WORKING_SEAT`

一個寫得出來、講得出理由、畫面上有字的結局，**從來沒有人跑到過它**。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.lineup import (  # noqa: E402
    DUTY_APPROVE,
    DUTY_PLAN,
    DUTY_REVIEW,
    AgentSlot,
    Lineup,
)
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.i18n import t  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

# 規劃那一格會先跑（它的職責是 plan，而那一步在做事之前）。
# 腳本要餵得夠它跑完，否則假的 CLI 會自己丟 AssertionError——
# **那個例外長得跟產品的當機一模一樣**，而這一整支就是在分辨那件事。
A_PLAN = cli_result("做法：先看一遍，再說。")
PASS_REVIEW = cli_result(
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)

# 他把每一格都排成不會寫檔的那幾種。**這是他今天唯一做得到的
# 「這一格不要動我的檔案」。**
NOBODY_WRITES = [
    AgentSlot("node1", "claude", duty=DUTY_PLAN),
    AgentSlot("node2", "codex", duty=DUTY_REVIEW),
    AgentSlot("node3", "claude", duty=DUTY_APPROVE),
]


def _run(tmp_path: Path, monkeypatch, slots: list[AgentSlot], script=None):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 做一件事\n", encoding="utf-8")
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    SessionRegistry(project).save_lineup(Lineup.of(slots))

    monkeypatch.setattr(
        "app.core.orchestrator.CliRunner",
        scripted_cli_runner_class(list(script or [A_PLAN])),
    )
    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
            max_fix_attempts=1,
        ),
    )
    completed: list = []
    failed: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed


def test_it_stops_instead_of_blowing_up(tmp_path, monkeypatch) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是它 2026-08-25 之前的樣子：`claude_result` 是 None，
    而 `_build_result` 直接對它取 `.success`。
    """
    completed, failed = _run(tmp_path, monkeypatch, NOBODY_WRITES)
    assert not failed, (
        "整輪丟了 traceback 而不是收在一個講得出理由的結局：\n"
        + "\n".join(str(f)[:400] for f in failed)
    )
    assert completed, "既沒有失敗也沒有完成——那一輪不知道去哪裡了"


def test_the_ending_is_the_one_that_was_written_for_this(
    tmp_path, monkeypatch
) -> None:
    """收的是 `NO_WORKING_SEAT`，不是別的結局。

    只斷言「沒有丟例外」的話，收成 `COMPLETED` 也會通過——而那是
    **這個專案最在意的那一種假訊號**：一輪沒有人做事的執行，被記成做完了。
    """
    completed, _ = _run(tmp_path, monkeypatch, NOBODY_WRITES)
    assert completed[-1]["workflow_status"] == "NO_WORKING_SEAT", completed[-1]


def test_the_screen_says_which_way_out(tmp_path, monkeypatch) -> None:
    """畫面上那句話要在，而且要**講得出下一步**。

    「執行失敗」四個字寫進紀錄等於沒寫——他要知道回哪一頁改什麼。
    """
    completed, _ = _run(tmp_path, monkeypatch, NOBODY_WRITES)
    said = completed[-1].get("verification_problem", "")
    assert said == t("lineup.no_worker"), said
    # 講得出**回哪一頁、改什麼**。「執行失敗」四個字寫進紀錄等於沒寫。
    assert "陣容頁" in said, said
    assert "執行" in said, said


def test_it_does_not_pick_one_to_stand_in(tmp_path, monkeypatch) -> None:
    """**不准挑一格代打。**

    挑的那一格會拿到寫入權限，而使用者從來沒有把它排成「執行」——
    他會得到一個他沒有授權的動作，而畫面上看起來一切正常。
    """
    completed, _ = _run(tmp_path, monkeypatch, NOBODY_WRITES)
    assert completed[-1].get("claude_success") is False
    assert not completed[-1].get("claude_output"), (
        "沒有人做事，卻有人交出了東西——有一格被挑去代打了"
    )


def test_a_line_that_does_have_a_worker_is_not_stopped(
    tmp_path, monkeypatch
) -> None:
    """**不准擋過頭。**

    一條什麼都拒絕的門，跟一個永遠說可以的門一樣沒用。這一條確認上面
    那幾條擋的是「真的沒有人做事」，不是「陣容裡有它不認得的名字」。
    """
    from app.core.lineup import DUTY_WORK  # noqa: PLC0415

    with_worker = [
        AgentSlot("node1", "claude", duty=DUTY_PLAN),
        AgentSlot("node2", "claude", duty=DUTY_WORK),
        AgentSlot("node3", "codex", duty=DUTY_REVIEW),
    ]
    completed, _ = _run(
        tmp_path,
        monkeypatch,
        with_worker,
        script=[A_PLAN, A_PLAN, A_PLAN, PASS_REVIEW, PASS_REVIEW],
    )
    assert completed[-1]["workflow_status"] != "NO_WORKING_SEAT", (
        "陣容裡明明有一格會寫檔案，卻被判成沒有人做事"
    )
