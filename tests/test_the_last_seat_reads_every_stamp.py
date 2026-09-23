"""最後那一格看完所有的章，說這一整條鏈站不站得住。

老闆五節點流程的最後一句：**「NODE5 審閱所有簽章」**。

它不做事、不驗證。它回答的是所有機器都回答不了的那個問題：**這些章加起來，
是不是真的說明這件事做完了。**

這一份守五件事：

1. **它拿到的是引擎算好的事實**，不是叫它自己去翻紀錄。自己翻會翻錯，而錯的
   方式看起來完全正常。
2. **它推翻不了機器章。**有紅的就根本不派它——讓它看一眼再說「可以」，等於
   花一次錢買一個蓋不掉事實的意見。
3. **它不重做任何東西。**它改的東西上面沒有任何人的章，而它自己剛蓋章說
   「我看過了」——看的是改之前那一版。
4. **看不懂它的回覆就停下來，不猜。**猜成「同意」會蓋一個沒有人真的說過的章。
5. **沒有那一格的陣容一次呼叫都不多。**加一關而讓沒排那一關的人多付錢，是這個
   專案最不能接受的那種改動。
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
from tests.fakes import SETUP_WITHOUT_APPROVAL  # noqa: E402

PASS_REVIEW = (
    '{"status": "PASS", "summary": "看過了，兩個邊界都有處理", '
    '"issues": [], "instructions": [], "question": ""}'
)
FAIL_REVIEW = (
    '{"status": "FIX", "summary": "第 3 條沒做", "target": "實作", '
    '"issues": ["x"], "instructions": ["y"], "question": ""}'
)

WITH_APPROVER = [
    AgentSlot("複查", "codex", duty=DUTY_REVIEW),
    AgentSlot("實作", "claude", duty=DUTY_WORK),
    AgentSlot("核准", "claude", duty=DUTY_APPROVE),
]
NO_APPROVER = [
    AgentSlot("複查", "codex", duty=DUTY_REVIEW),
    AgentSlot("實作", "claude", duty=DUTY_WORK),
]

OK = cli_result("拍板：可以\n理由：驗收標準、實作、驗證三個章都在同一版上")


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 做一個錯題整理小工具\n", encoding="utf-8"
    )
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    return project


def _seat(project: Path, slots: list[AgentSlot]) -> None:
    SessionRegistry(project).save_lineup(Lineup.of(slots))


def _run(project: Path, monkeypatch, script, own_approval: bool = False):
    """`own_approval=True`＝這一條要**自己**餵驗簽章那一步的回覆。

    2026-08-24 老闆拍板「規定要最後一個 NODE 去驗證」之後，那一步變成每一次
    執行都會跑，所以假 CLI 預設會用現成答案回它（跟需求確認、先交做法同一種
    處理）。**但測那一步本身的幾條不能用現成答案**——現成答案永遠說「可以」，
    而它們斷言的是「回覆看不懂就停」「不同意要指名退給誰」。
    """
    QApplication.instance() or QApplication([])
    runner_class = scripted_cli_runner_class(
        script, auto=SETUP_WITHOUT_APPROVAL if own_approval else True
    )
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
    stages: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.stage_changed.connect(stages.append)
    worker.run()
    QApplication.instance().processEvents()
    # `calls` 不含每一次執行都會跑的前置（需求確認、先交做法）——
    # 見 `fakes.SETUP_STEPS`。把它們算進來，等於要求每一個案例都重述
    # 同一件事，而且下一次再加一個前置就要再改一遍全部。
    return completed, failed, stages, calls_after_intake(runner_class.calls)


def _happy(last=OK):
    return [
        cli_result('{"result": "做完了"}'),
        cli_result("1 passed"),
        cli_result(PASS_REVIEW),
        last,
    ]


# --- 一、它真的跑，而且蓋章 -------------------------------------------------


def test_the_approver_runs_after_the_review_and_stamps(
    tmp_path: Path, monkeypatch
) -> None:
    """複查通過之後才輪到它，而且它的同意要留下一個章。

    沒有章的話，「有人審閱過所有簽章」這件事本身沒有憑據——而那正是它存在的
    意義。
    """
    project = _project(tmp_path)
    _seat(project, WITH_APPROVER)

    completed, failed, stages, calls = _run(project, monkeypatch, _happy())

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert "核准正在審閱所有簽章" in stages

    chain = StampLog(project).read(completed[0]["run_id"])
    mine = [
        s
        for s in chain.stamps
        if s.role == "核准" and s.kind is StampKind.REVIEW
    ]
    assert len(mine) == 1, f"核准那一格的章不是剛好一個：{mine}"
    assert "同一版" in mine[0].reasoning, (
        f"章的理由不是它自己寫的那句話：{mine[0].reasoning!r}"
    )


def test_it_is_shown_the_real_chain_not_asked_to_go_and_read_it(
    tmp_path: Path, monkeypatch
) -> None:
    """它拿到的是引擎算好的事實。

    叫它自己去翻 `.aicc/runs/` 的話它會翻錯，而錯的方式看起來完全正常。
    這裡用複查寫的那句話當哨兵——它必須出現在核准拿到的提示詞裡。
    """
    project = _project(tmp_path)
    _seat(project, WITH_APPROVER)

    _completed, failed, _stages, calls = _run(project, monkeypatch, _happy(), own_approval=True)
    assert failed == [], failed

    sent = calls[3]["input_text"] or ""
    assert "你是此專案的核准者" in sent
    assert "看過了，兩個邊界都有處理" in sent, (
        "核准那一格看不到複查憑什麼同意——它拿到的是一張打勾表，"
        "而它的工作正是判斷那些勾憑什麼成立"
    )
    assert "【engine】驗證" in sent, "驗證那個機器章沒有出現在鏈子裡"
    assert "憑什麼：" in sent
    assert "不得修改任何檔案" in sent, (
        "核准那一格拿到了寫入權限。它改的東西上面沒有任何人的章，"
        "而它自己剛蓋章說看過了——看的是改之前那一版"
    )


# --- 二、它推翻不了機器章 ---------------------------------------------------


def test_a_red_machine_stamp_means_the_approver_is_never_asked(
    tmp_path: Path, monkeypatch
) -> None:
    """有紅的就不派它。

    跑出來是紅的就是紅的，它的意見蓋不掉一次真的失敗。派它去看一眼再說
    「可以」，等於**花一次錢買一個蓋不掉事實的意見**——而那個意見會以一個
    章的形狀留在紀錄裡。
    """
    project = _project(tmp_path)
    _seat(project, WITH_APPROVER)

    # 作者那一步失敗 → 實作那個機器章是紅的。
    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result("", success=False),
        ],
    )

    assert failed == [], failed
    assert len(calls) == 1, "作者失敗之後還繼續派工"
    assert completed[0]["workflow_status"] == "CLAUDE_ERROR"

    chain = StampLog(project).read(completed[0]["run_id"])
    assert not [s for s in chain.stamps if s.role == "核准"], (
        "有紅章的時候還是蓋了一個核准的章"
    )


# --- 三、看不懂就停，不猜 ---------------------------------------------------


@pytest.mark.parametrize(
    "reply, why",
    [
        ("我看了一下，應該沒問題。", "兩種都沒寫"),
        ("拍板：可以", "說可以但沒寫理由"),
        ("拍板：不可以\n理由：怪怪的", "不同意卻沒指名退給誰"),
        ("拍板：不可以\n退給：不存在的那一格\n理由：x", "指名一格不在線上的"),
    ],
)
def test_a_reply_it_cannot_read_stops_the_run(
    tmp_path: Path, monkeypatch, reply: str, why: str
) -> None:
    """猜成「同意」會蓋一個沒有人真的說過的章，而它讀起來跟真的一模一樣。

    「說可以但沒寫理由」也算看不懂：一個沒有理由的「我同意」，事後回溯時
    回答不了最重要的那一題——憑什麼。蓋不出章比蓋一個空洞的章誠實。
    """
    project = _project(tmp_path)
    _seat(project, WITH_APPROVER)

    completed, failed, _stages, _calls = _run(
        project, monkeypatch, _happy(cli_result(reply)),
        own_approval=True,
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "APPROVAL_INVALID", why
    assert completed[0]["test_output"].strip()

    chain = StampLog(project).read(completed[0]["run_id"])
    assert not [s for s in chain.stamps if s.role == "核准"], (
        "看不懂它的回覆，卻還是蓋了一個章"
    )


def test_a_refusal_names_the_seat_and_says_why(
    tmp_path: Path, monkeypatch
) -> None:
    """不同意拍板的時候，要指名退給誰，而且那句話要到得了使用者面前。

    只說「不通過」的話，使用者只知道失敗，不知道誰要做什麼。
    """
    project = _project(tmp_path)
    _seat(project, WITH_APPROVER)

    completed, failed, _stages, _calls = _run(
        project,
        monkeypatch,
        _happy(
            cli_result(
                "拍板：不可以\n退給：實作\n理由：驗證那個章蓋在舊版的交付物上"
            )
        ),
        own_approval=True,
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "APPROVAL_REJECTED"
    note = completed[0]["test_output"]
    assert "實作" in note
    assert "舊版的交付物" in note, f"退回的理由不見了：{note!r}"


# --- 四、沒有那一格就一毛都不多花 -------------------------------------------


def test_the_last_seat_checks_the_chain_even_with_no_approver_seat(
    tmp_path: Path, monkeypatch
) -> None:
    """**沒排核准那一格，最後一格照樣要驗簽章。**

    老闆 2026-08-24 拍板：「反正使用者設定 chain 的最後最後一個 NODE 一定要
    放入確認簽章的 PROMPT，或者**規定要最後一個 NODE 去驗證**。」

    ## 這一條取代了它自己

    它原本叫 `test_a_line_up_without_an_approver_costs_exactly_what_it_did`，
    守的是 2026-08-22 的定案：「沒有核准那一格的陣容完全不跑這一步，
    **一次呼叫都不多**」。

    **那條定案被他本人 2026-08-24 推翻了**，而且是在看過代價之後：沒有排核准
    那一格的陣容，每一次執行會多一次唯讀呼叫（多等一輪、多吃一次額度）。
    換到的是——**跑完之後一定有人說過「這條鏈站得住」**，而不是沒有人說過。

    **舊的那條沒有留著**：一條守著已經被推翻的規則的測試，會在下一個人想把
    行為改對的時候擋住他，而它的訊息會說得像那是規則。
    """
    project = _project(tmp_path)
    _seat(project, NO_APPROVER)

    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
            # 第四次就是驗簽章。**這個剛好夠的長度就是斷言**：假腳本用完會丟
            # AssertionError，所以少一次或多一次都會紅。
            cli_result("拍板：可以\n理由：章都在，而且都蓋在同一版上。"),
        ],
        own_approval=True,
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert len(calls) == 4, f"最後一格沒有被問「這條鏈站不站得住」：{len(calls)}"
    assert "你是此專案的核准者" in (calls[3]["input_text"] or "")


# --- 五、複查的章要蓋在真的複查那一格名下 -----------------------------------


def test_the_review_stamp_carries_the_seat_that_actually_reviewed(
    tmp_path: Path, monkeypatch
) -> None:
    """複查那個章要掛在**使用者取的那個名字**底下。

    寫死 `reviewer` 的話，他把那一格取名「複查」之後，章就掛在一個不存在的
    角色底下——而拍板檢查要的是那一格的章，於是它永遠缺一個，**而且補不出來**。
    """
    project = _project(tmp_path)
    _seat(project, NO_APPROVER)

    completed, failed, _stages, _calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )
    assert failed == [], failed

    chain = StampLog(project).read(completed[0]["run_id"])
    reviews = [
        s
        for s in chain.stamps
        if s.subject == SUBJECT_WORK and s.kind is StampKind.REVIEW
    ]
    assert reviews, "一個複查的章都沒有"
    assert reviews[0].role == "複查", (
        f"複查的章掛在【{reviews[0].role}】底下，而線上那一格叫【複查】"
    )


def test_a_live_red_stamp_keeps_the_approver_out_of_it(
    tmp_path: Path, monkeypatch
) -> None:
    """直接對那一關下手，因為這個情境在完整流程裡走不到。

    紅的機器章出現的時候，執行早就在別的地方停下來了。所以「有紅的就不派它」
    這條守衛，在端到端的測試裡**永遠不會被執行到**——而一條永遠不會被執行到
    的守衛，拿掉跟留著長得一模一樣。

    這裡繞過流程，直接餵它一條有紅章的鏈子。
    """
    from app.core.run_stamps import RunStamper

    project = _project(tmp_path)
    _seat(project, WITH_APPROVER)
    QApplication.instance() or QApplication([])

    runner_class = scripted_cli_runner_class([])
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        run_id="run-red",
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_session_bootstrap=False,
        ),
    )
    from app.core.orchestrator import Orchestrator

    orchestrator = Orchestrator(
        project,
        run_id="run-red",
        settings=worker.settings,
        # 陣容一定要餵進去，否則 `_roles_with(DUTY_APPROVE)` 是空的，
        # 這一關直接跳過——測試會綠，而它什麼都沒測到。
        lineup=SessionRegistry(project).load_lineup(),
    )
    worker.orchestrator = orchestrator

    stamper = RunStamper(project, "run-red")
    stamper.work_done(
        deliverable="這一版的東西",
        succeeded=False,
        evidence="claude 離開碼 1",
    )

    stop = worker._approval_phase(orchestrator, stamper, "這一版的東西")

    assert stop is not None, (
        "鏈子上有一個還算數的紅章，卻照樣派核准那一格去看——"
        "它的意見蓋不掉一次真的跑出來的失敗"
    )
    assert stop["workflow_status"] == "APPROVAL_BLOCKED"
    assert runner_class.calls == [], "派工了，而且是去買一個蓋不掉事實的意見"


def test_a_red_stamp_on_an_older_version_does_not_block(
    tmp_path: Path, monkeypatch
) -> None:
    """修正迴圈跑過的執行，鏈子上一定留著先前那個紅章——**它不算數了**。

    拿全部的章去比的話，每一個修好過的執行都會被擋在最後一關，而擋它的理由
    是一件已經被修掉的事。這一條守的是那個方向。
    """
    from app.core.run_stamps import RunStamper
    from app.core.orchestrator import Orchestrator

    project = _project(tmp_path)
    _seat(project, WITH_APPROVER)
    QApplication.instance() or QApplication([])

    runner_class = scripted_cli_runner_class([OK])
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        run_id="run-fixed",
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_session_bootstrap=False,
        ),
    )
    orchestrator = Orchestrator(
        project,
        run_id="run-fixed",
        settings=worker.settings,
        lineup=SessionRegistry(project).load_lineup(),
    )
    worker.orchestrator = orchestrator

    stamper = RunStamper(project, "run-fixed")
    stamper.work_done(
        deliverable="第一版（壞的）",
        succeeded=False,
        evidence="claude 離開碼 1",
    )
    stamper.work_done(
        deliverable="修好之後那一版", succeeded=True, evidence="claude 離開碼 0"
    )

    stop = worker._approval_phase(orchestrator, stamper, "修好之後那一版")

    assert stop is None, (
        f"修好了卻被擋在最後一關，理由是一件已經被修掉的事：{stop}"
    )
    assert len(runner_class.calls) == 1


# --- 每一格複查都要看，而且都要同意 -----------------------------------------


def test_every_review_seat_gets_to_look(tmp_path: Path, monkeypatch) -> None:
    """排了兩格複查，就要跑兩次。

    2026-08-22 之前引擎只跑最後一格——使用者排了兩格，引擎跑一格，而執行紀錄
    回報一切正常。那是老闆罵過的同一種缺陷：**他排得出來的東西引擎跑不動，
    而且不會有任何一個字說了這件事。**
    """
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot("初審", "codex", duty=DUTY_REVIEW),
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("複審", "codex", duty=DUTY_REVIEW),
        ],
    )

    completed, failed, stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert "初審正在複查" in stages and "複審正在複查" in stages

    chain = StampLog(project).read(completed[0]["run_id"])
    reviewers = {
        s.role
        for s in chain.stamps
        if s.subject == SUBJECT_WORK and s.kind is StampKind.REVIEW
    }
    assert reviewers == {"初審", "複審"}, (
        f"不是每一格複查都留下章：{reviewers}"
    )


def test_one_reviewer_saying_fix_stops_the_rest(
    tmp_path: Path, monkeypatch
) -> None:
    """有一格說要改，就不繼續問下一格。

    一份已經被要求修改的東西，再多幾個意見也不會讓它變成不用改——而那幾次
    意見要等，而且問的是一份即將被改掉的東西。
    """
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot("初審", "codex", duty=DUTY_REVIEW),
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("複審", "codex", duty=DUTY_REVIEW),
        ],
    )

    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(FAIL_REVIEW),      # 初審：要改
            cli_result('{"result": "改好了"}'),
            cli_result("1 passed"),
            cli_result(FAIL_REVIEW),      # 初審：還是要改（到上限）
        ],
    )

    assert failed == [], failed
    # 數的是**複查被派了幾次**。這一輪走完了整個修正迴圈，而每一輪都只該問
    # 初審一格——複審一次都不該被問到，因為那份東西已經被要求修改了。
    looked = [
        c
        for c in calls
        if "Technical Lead" in (c["input_text"] or "")
    ]
    assert len(looked) == 2, (
        f"複查被派了 {len(looked)} 次；兩輪各一次才對，"
        "多出來的那幾次問的是一份即將被改掉的東西"
    )
    # 而且整輪的派工次數剛好是腳本的長度。多問一格複查會把腳本用完，
    # 假的 CLI 會丟 AssertionError——所以這個數字也是一條斷言。
    assert len(calls) == 6, [c["arguments"][0] for c in calls]
