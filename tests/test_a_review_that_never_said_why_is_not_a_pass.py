"""複查沒有真的同意的時候，鏈子上不准有一個說著「我同意」的章。

2026-08-24 五節點測試前掃假綠燈掃到兩條，同一支檔案、同一個病：
**一個沒有人真的說過的同意，被寫成一個讀起來跟真的一模一樣的章。**

1. 複查回 `PASS` 而 `summary` 是空的 → `_why_it_passed()` 補一句
   「複查回報 PASS，但沒有寫理由。」當理由。那一句剛好夠下一層放行
   （`RunStamper._put` 擋的是**空的**理由），於是一個本來蓋不出來的審查章
   成立，拍板檢查從「缺【複查】的審查章」翻成「章齊了」。
2. 複查回 `FIX` 而 `issues` 一條都沒有 → 被讀成「全是供參考級」升級成
   `PASS`，而它那句反對的話（「不能 PASS：⋯」）被當成**憑什麼同意**寫進
   審查章，還餵給核准那一格去審閱。

**兩條使用者看到的都是薄荷色的「工作流程已完成」。**沒有任何一個字說過
「複查其實沒有同意」——第 2 條連那句反對的話都被搬到了「同意的理由」欄位。

## 這幾條擋得住什麼、擋不住什麼

**擋得住**：複查說可以卻沒說憑什麼、或者說不可以卻沒列任何一條，被算成
一次通過並且留下一個審查章。

**擋不住**：複查寫了理由，而那句理由是敷衍的（「看過了」）。那一題沒有
機制——機器分不出「一句真的判斷」跟「一句話」，而**假裝分得出來的檢查會
在畫面上長得跟真的守衛一模一樣**。守得住的只有「**有沒有寫**」。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import outcomes  # noqa: E402
from app.core.lineup import (  # noqa: E402
    DUTY_REVIEW,
    DUTY_WORK,
    AgentSlot,
    Lineup,
)
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.approval_chain import (  # noqa: E402
    SUBJECT_WORK,
    StampKind,
    StampLog,
)
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    calls_after_intake,
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

SEATS = [
    AgentSlot("複查", "codex", duty=DUTY_REVIEW),
    AgentSlot("實作", "claude", duty=DUTY_WORK),
]

# 2026-08-24 實跑那一輪複查真的寫出來的那句話。**它是反對**，
# 而修之前它出現在章的「憑什麼同意」那一欄。
OBJECTION = "不能 PASS：這份修改動到了規格外的檔案，而且沒有負向驗證"


def review(status: str, summary: str, issues: list | None = None) -> str:
    return json.dumps(
        {
            "status": status,
            "summary": summary,
            "issues": list(issues or []),
            "instructions": ["把規格外的檔案還原，並補負向驗證"],
            "target": "實作",
            "question": "",
        },
        ensure_ascii=False,
    )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 做一個錯題整理小工具\n", encoding="utf-8"
    )
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    SessionRegistry(project).save_lineup(Lineup.of(SEATS))
    return project


def _run(project: Path, monkeypatch, script):
    QApplication.instance() or QApplication([])
    runner_class = scripted_cli_runner_class(script)
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)
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
    return completed, failed, calls_after_intake(runner_class.calls)


REVIEWING_SEAT = SEATS[0].role


def _review_stamps(project: Path, run_id: str) -> list:
    """**複查那一格**對「這次做出來的東西」蓋的審查章。

    三個條件缺一不可，而且每一個都擋掉一種會讓斷言問錯問題的章：

    * `subject == work`——訂驗收標準那一格蓋的也是審查章，只是蓋在
      `acceptance` 上。
    * `kind == REVIEW`——執行與驗證那兩個是機器章。
    * `role == 複查`——**這一條是實測補上去的**。核准那一格
      （`RunStamper.approved`）蓋出來的章 `subject`／`kind` 跟複查的一模一樣，
      少了這一條，一個「複查沒蓋章、核准蓋了」的執行會被讀成「複查蓋了章」，
      而錯誤訊息還會理直氣壯地印出那個章。名字會撞，位子不會。
    """
    return [
        stamp
        for stamp in StampLog(project).read(run_id).stamps
        if stamp.subject == SUBJECT_WORK
        and stamp.kind is StampKind.REVIEW
        and stamp.role == REVIEWING_SEAT
    ]


# --- 一、說可以卻沒說憑什麼 -------------------------------------------------


def test_a_pass_with_no_reason_leaves_no_review_stamp(
    tmp_path: Path, monkeypatch
) -> None:
    """複查說可以而沒有寫理由的時候，**鏈子上不准多一個審查章**。

    多出來的那一個章長這樣：「【複查】實作　審查章　憑什麼：複查回報
    PASS，但沒有寫理由。」——那句話是引擎自己補的，複查一個字都沒寫。
    """
    project = _project(tmp_path)

    completed, failed, _calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(review("PASS", "")),
        ],
    )

    assert failed == [], failed
    assert completed, "這一輪沒有跑完，底下的斷言會是恆真的"
    stamps = _review_stamps(project, completed[0]["run_id"])
    assert not stamps, (
        "複查一個字都沒寫，鏈子上卻有一個審查章："
        + "、".join(f"【{s.role}】憑什麼：{s.reasoning}" for s in stamps)
    )


def test_a_pass_with_no_reason_does_not_finish_the_run(
    tmp_path: Path, monkeypatch
) -> None:
    """而且**不准收在「工作流程已完成」**。

    只做「不蓋章」那一半的話，畫面會說「還缺一個章」；只做「不算完成」那
    一半的話，章還在。兩件事要一起，不然使用者拿到的是一句他修不了的話。
    """
    project = _project(tmp_path)

    completed, failed, _calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(review("PASS", "   ")),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] != "COMPLETED", (
        "複查說可以卻沒說憑什麼，而畫面說「工作流程已完成」"
    )
    assert outcomes.describe(completed[0]).group != outcomes.GROUP_DONE, (
        "收場被歸進「跑完了，沒有人在等你」那一組"
    )


def test_the_run_says_out_loud_that_the_review_never_said_why(
    tmp_path: Path, monkeypatch
) -> None:
    """**要講出來是哪一半沒有。**

    靜靜少一個章的話，使用者看到的是「還缺一個章」——他不會知道是複查說
    可以卻沒說憑什麼，而那正是他唯一處理得了的那半句（叫複查重講一次）。
    比照沒有複查席那一條（`no_reviewing_seat`），時間軸留一筆。
    """
    project = _project(tmp_path)

    completed, failed, _calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(review("PASS", "")),
        ],
    )

    assert failed == [], failed
    events = [
        json.loads(line)
        for line in (
            project
            / ".aicc"
            / "runs"
            / completed[0]["run_id"]
            / "timeline.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    said = [e for e in events if e["event_type"] == "review_pass_without_reason"]
    assert said, (
        "整輪執行沒有任何一筆說過「複查回了 PASS 但沒寫理由」："
        + "、".join(sorted({e["event_type"] for e in events}))
    )


# --- 二、說不可以卻一條都沒列 -----------------------------------------------


def test_a_fix_with_nothing_listed_goes_back_to_be_redone(
    tmp_path: Path, monkeypatch
) -> None:
    """複查說「不能 PASS」而一條意見都沒列的時候，**要回頭重做**。

    以前這一句反對會被讀成「全是供參考級」（`issues` 空的時候「有沒有須處理
    的」也是空的），於是整件事一次都不重跑就收在「工作流程已完成」。

    腳本的長度就是斷言：六次代表真的退回去重做過一輪。
    """
    project = _project(tmp_path)

    completed, failed, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(review("FIX", OBJECTION)),
            cli_result('{"result": "改好了"}'),
            cli_result("1 passed"),
            cli_result(review("PASS", "規格外的檔案還原了，負向驗證也補了")),
        ],
    )

    assert failed == [], failed
    assert len(calls) == 6, (
        "複查說「不能 PASS」，而整件事一次都沒有重跑："
        f"{[c['arguments'][0] for c in calls]}"
    )
    assert completed[0]["workflow_status"] == "COMPLETED"


def test_an_objection_never_becomes_the_reason_a_stamp_says_yes(
    tmp_path: Path, monkeypatch
) -> None:
    """**那句反對的話不准出現在「憑什麼同意」那一欄。**

    事後翻章的人看到的是：【複查】的審查章，憑什麼——「不能 PASS：這份修改
    動到了規格外的檔案⋯」。核准那一格拿到的鏈子摘要裡也是這一句
    （`describe_chain()` 印的就是這個欄位）——它讀到的是「複查同意的理由是
    『不能 PASS』」，然後在上面蓋章。
    """
    project = _project(tmp_path)

    completed, failed, _calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(review("FIX", OBJECTION)),
            cli_result('{"result": "改好了"}'),
            cli_result("1 passed"),
            cli_result(review("PASS", "規格外的檔案還原了，負向驗證也補了")),
        ],
    )

    assert failed == [], failed
    reasons = [s.reasoning for s in _review_stamps(project, completed[0]["run_id"])]
    # **先確認真的有章可以問。**底下那一句是「不在裡面」——一張空的清單
    # 讓它恆真，而讓清單變空的方式多得是（複查那一段整個不蓋章、章掛到
    # 別的角色底下、鏈子讀不出來）。少了這一行，這條測試會在「審查章全部
    # 消失」的那一天照樣印綠燈，而它守的正是那個欄位。
    assert reasons, (
        "這一輪一個【複查】的審查章都沒有，底下那一句「反對不在裡面」"
        "是恆真的——去看是誰把章弄不見了"
    )
    assert OBJECTION not in reasons, (
        "一句反對的話被寫成「憑什麼同意」：" + "、".join(reasons)
    )
