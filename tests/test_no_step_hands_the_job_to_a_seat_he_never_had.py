r"""剩下那四步派工，一步都不准退回一個他沒有排的位子。

## 2026-08-26 修掉的那兩處，還有四處是一樣的

計畫序列那兩步（`run_codex_plan` / `run_claude_plan_review`）2026-08-26 換成
`required_seat_with_duty()`，排不到就丟 `NoSeatWithDuty`、收成一個講得出理由
的結局。同一天沒有動到的還有四處，全部都是

    seat = self.seat_with_duty(<某一種職責>, fallback=ROLE_AUTHOR/ROLE_REVIEWER)

**退回一個陣容裡不存在的名字，下游只講得出「沒有那一格」**：

    NotImplementedError: 這個專案的陣容裡沒有「author」這一格，但工作流要
    派工給它。目前排的是：發想、規劃、ui設計、執行、審閱。請到陣容頁重新編組。

而那句話**把他送去改一個沒有壞的東西**——他排的那幾格一點問題都沒有，
真正缺的是某一種職責。

## 四處各自的收場不一樣，這一份檔案守的就是「挑對那一種」

| 那一步 | 排不到的時候 | 收場 |
|---|---|---|
| 他答完問題續跑（`run_claude_with_user_answer`） | 這一次派工還沒發生 | 停下來：`NO_WORKING_SEAT` |
| 決策關卡（`run_codex_decision_gate`） | 那一步本來就可以不做 | 跳過、直接問他，時間軸留一筆 |
| 記憶草稿（`run_codex_memory_update`） | 這一輪跑完了、只差這一步 | `MEMORY_NO_SEAT_FOR_DUTY` |
| 審記憶草稿（`run_claude_memory_review`） | 同上 | `MEMORY_NO_SEAT_FOR_DUTY` |

**挑錯的代價是實打實的**：把「跑完了、只差記憶」報成「這一輪沒有開始」，
他會整輪重跑一次——再花一整輪的額度，停在同一個地方，看到同一句話。

## 改壞的樣子

他在對話框裡打完答案按下送出（**整條線上他最不預期出錯的一步**），而這個
專案的陣容裡沒有任何一格的職責是「執行」：畫面紅燈，加一坨

    NotImplementedError: 這個專案的陣容裡沒有「author」這一格⋯⋯

## 使用者現在會看到什麼

一個 `partial` 的收場「沒有人做事」，底下寫著

> 這個專案的陣容裡沒有任何一格的職責是「執行」，所以沒有人會產出東西。
> 請到陣容頁把其中一格的職責改成執行。

**而且一次工都沒有派出去**——他的答案沒有被送給一個他沒有授權的位子。

## 為什麼底下有幾條是直接呼叫引擎，不是跑一整輪

決策關卡與維護共同記憶那三支，**上游今天已經先擋住了**（各自的呼叫端會先問
「有沒有那一格」，排不到就走另一條路）。也就是說那三支裡新加的那道門今天
走不到——而「走不到」不等於「可以留著」：2026-08-25 那個洞就是「今天到不了、
明天多一個呼叫端就到得了」。

所以那三條直接叫那一支，**專門走那一條分支**。不這樣寫的話，把
`required_seat_with_duty` 換回 `fallback=ROLE_X` 的那個破壞會永遠是綠的，
而配方會變成一條沒有牙齒的配方。
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
    DUTY_APPROVE,
    DUTY_PLAN,
    DUTY_REVIEW,
    DUTY_WORK,
    AgentSlot,
    Lineup,
)
from app.core.orchestrator import NoSeatWithDuty, Orchestrator  # noqa: E402
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.i18n import t  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    calls_after_intake,
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

# 一格「執行」都沒有的陣容。**這份陣容沒有任何一格是錯的**——他就是這樣排的。
NOBODY_DOES_THE_WORK = [
    AgentSlot("發想", "codex", duty=DUTY_PLAN),
    AgentSlot("審閱", "codex", duty=DUTY_APPROVE),
]

# 一格「複查」都沒有的陣容（2026-08-26 實機那五格的形狀）。
NOBODY_REVIEWS = [
    AgentSlot("發想", "codex", duty=DUTY_PLAN),
    AgentSlot("執行", "claude", duty=DUTY_WORK),
    AgentSlot("審閱", "codex", duty=DUTY_APPROVE),
]

EVERY_SEAT_SAYS = json.dumps(
    {
        "result": "做完了",
        "status": "PASS",
        "summary": "看過了",
        "issues": [],
        "instructions": [],
        "question": "",
        "memory_update": "## NEXT_STEPS.md\n下一步照舊。",
    },
    ensure_ascii=False,
)


def _project(tmp_path: Path, slots) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 做一件事\n", encoding="utf-8")
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    SessionRegistry(project).save_lineup(Lineup.of(slots))
    return project


def _orchestrator(tmp_path: Path, monkeypatch, slots):
    """一個**一次工都派不出去**的引擎：假的 CLI 腳本是空的。

    腳本空的意思是「多派一次工就當場 AssertionError」——所以「沒有派工」
    是被量出來的，不是靠事後數 `calls` 數出來的。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = _project(tmp_path, slots)
    runner_class = scripted_cli_runner_class([], auto=False)
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)
    orchestrator = Orchestrator(
        project,
        run_id="a-run",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
        lineup=Lineup.of(slots),
    )
    return orchestrator, runner_class


def _run(tmp_path: Path, monkeypatch, slots, **worker_kwargs):
    """跑一輪，回 (收場, traceback, 派工次數)。

    腳本刻意**比這條路用得到的長**：多派一次工不會撞到「腳本用完了」，
    所以「一次都沒多派」是被斷言量出來的，不是被腳本長度擋出來的。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = _project(tmp_path, slots)

    runner_class = scripted_cli_runner_class(
        [cli_result(EVERY_SEAT_SAYS) for _ in range(20)]
    )
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=True,
            auto_session_bootstrap=True,
        ),
        **worker_kwargs,
    )
    completed: list = []
    failed: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed, runner_class.calls


# ---------------------------------------------------------------------------
# 一、他剛打完答案按下送出
# ---------------------------------------------------------------------------


def test_an_answer_with_nobody_to_do_the_work_stops_instead_of_a_traceback(
    tmp_path, monkeypatch
) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：他在對話框打完答案按下送出，畫面變成紅燈加一坨
    `NotImplementedError: 這個專案的陣容裡沒有「author」這一格`——而他剛剛
    才打完字，這是整條線上他最不預期出錯的一步。

    現在他看到的是一個 `partial` 的收場「沒有人做事」。
    """
    completed, failed, calls = _run(
        tmp_path,
        monkeypatch,
        NOBODY_DOES_THE_WORK,
        user_question="要不要把舊的設定檔一起刪掉？",
        user_answer="不要。",
    )
    assert not failed, (
        "他打完答案送出之後丟了 traceback：\n"
        + "\n".join(str(f)[-500:] for f in failed)
    )
    assert completed, "既沒有失敗也沒有完成——那一輪不知道去哪裡了"
    result = completed[-1]
    assert result["workflow_status"] == "NO_WORKING_SEAT", result
    assert outcomes.describe(result).style == "partial", result
    # 而且他的答案沒有被送出去。**這一行今天有兩道門擋著**（這裡一道、
    # `_run_role_prompt` 的寫入權限一道），所以它不是這條測試的牙齒，
    # 是那件事的紀錄：他答的那句話沒有進到任何一次付費呼叫裡。
    assert calls_after_intake(calls) == [], (
        "沒有一格會做事，卻還是派了工出去："
        + str([c["arguments"][:2] for c in calls_after_intake(calls)])
    )


def test_that_ending_tells_him_which_seat_to_change(
    tmp_path, monkeypatch
) -> None:
    """那句話要講「缺的是『執行』這一種職責」，不是一個他沒排過的名字。

    收成「這一輪沒有開始」（`NO_SEAT_FOR_DUTY`）也會有一句話、也是
    `partial`——但那一句講的是**整條序列**派不出工，它會叫他去換序列，
    而換過去照樣沒有人接他的答案。
    """
    completed, _, _ = _run(
        tmp_path,
        monkeypatch,
        NOBODY_DOES_THE_WORK,
        user_question="要不要把舊的設定檔一起刪掉？",
        user_answer="不要。",
    )
    result = completed[-1]
    assert result["workflow_status"] != "NO_SEAT_FOR_DUTY", result
    said = outcomes.describe(result).stage
    assert said == t("lineup.no_worker"), said
    assert "執行" in said, said
    assert "陣容頁" in said, said
    # 一個他的陣容裡沒有的出廠名字，不准出現在他要讀的那句話裡。
    assert "author" not in said, said


def test_a_line_with_a_working_seat_still_resumes(
    tmp_path, monkeypatch
) -> None:
    """**不准擋過頭。**

    一條什麼都拒絕的門，跟一個永遠說可以的門一樣沒用。排了執行的陣容，
    他的答案要照樣送出去——而「照樣送出去」量的是**真的派了工**。
    """
    with_worker = list(NOBODY_DOES_THE_WORK) + [
        AgentSlot("執行", "claude", duty=DUTY_WORK)
    ]
    completed, failed, calls = _run(
        tmp_path,
        monkeypatch,
        with_worker,
        user_question="要不要把舊的設定檔一起刪掉？",
        user_answer="不要。",
    )
    assert not failed, failed
    assert completed[-1]["workflow_status"] != "NO_WORKING_SEAT", completed[-1]
    assert calls_after_intake(calls), "排了執行的陣容，他的答案一次工都沒派出去"


def test_the_engine_itself_refuses_the_factory_name_on_that_path(
    tmp_path, monkeypatch
) -> None:
    """上面那條走的是呼叫端；**這一條走的是引擎自己那道門**。

    呼叫端（`workflow_worker`）今天會先擋下來，所以這一支裡新加的那道門
    走不到。走不到不等於可以留著：明天多一個呼叫端忘了先問，退回
    `author` 的那一行就會把他的答案送給一個他沒有排的位子。

    改壞的樣子：這裡不是 `NoSeatWithDuty`，而是下游那句
    「這個專案的陣容裡沒有「author」這一格」。
    """
    orchestrator, runner_class = _orchestrator(
        tmp_path, monkeypatch, NOBODY_DOES_THE_WORK
    )
    with pytest.raises(NoSeatWithDuty) as raised:
        orchestrator.run_claude_with_user_answer(
            question="要不要把舊的設定檔一起刪掉？",
            answer="不要。",
            answered_by="user",
            role="",
        )
    assert raised.value.duty == DUTY_WORK
    assert raised.value.seats == ("發想", "審閱")
    # 而且是在派工**之前**停的：一毛額度都沒花。
    assert runner_class.calls == [], "已經派了工才停"


# ---------------------------------------------------------------------------
# 二、決策關卡：那一步本來就可以不做
# ---------------------------------------------------------------------------


def test_the_skipped_gate_leaves_a_line_in_the_timeline(
    tmp_path, monkeypatch
) -> None:
    """跳過的那一步**要留痕**。

    這是三種收場裡唯一「本來就可以不做」的那一種，而沒有留痕的跳過事後
    跟「有跑過而且通過」長得一模一樣。`tools/inspect_run.py` 讀的就是這條
    時間軸——他事後問「那一題到底有沒有人先替我看過」，答案只能從這裡來。

    改壞的樣子：紀錄裡什麼都沒有，而畫面上那一題就這樣跳出來問他。
    """
    orchestrator, _ = _orchestrator(tmp_path, monkeypatch, NOBODY_REVIEWS)
    decision = orchestrator.resolve_claude_question_with_codex(
        "要不要把舊的設定檔一起刪掉？"
    )
    assert decision["action"] == "ASK_USER", decision

    events = orchestrator.timeline.load_events(run_id=orchestrator.run_id)
    kinds = [event.event_type for event in events]
    assert "decision_gate_no_seat" in kinds, f"跳過的那一步沒有留痕：{kinds}"
    # **不准寫成「送過去了」**——那正是這條要防的假訊號。
    assert "decision_gate_requested" not in kinds, kinds


def test_the_gate_itself_refuses_the_factory_name(
    tmp_path, monkeypatch
) -> None:
    """決策關卡那一支自己那道門。**專門走那一條分支。**

    呼叫端（`resolve_claude_question_with_codex`）今天會先擋下來，所以這一
    支裡的那道門走不到。走不到不等於可以留著。

    改壞的樣子：退回 `reviewer`，於是第五次派工之後
    `NotImplementedError: 這個專案的陣容裡沒有「reviewer」這一格`——
    而他問的那一題連看都沒看到。
    """
    orchestrator, runner_class = _orchestrator(
        tmp_path, monkeypatch, NOBODY_REVIEWS
    )
    with pytest.raises(NoSeatWithDuty) as raised:
        orchestrator.run_codex_decision_gate("要不要把舊的設定檔一起刪掉？")
    assert raised.value.duty == DUTY_REVIEW
    assert runner_class.calls == [], "已經派了工才停"


# ---------------------------------------------------------------------------
# 三、維護共同記憶那兩支：跑完了，只差這一步
# ---------------------------------------------------------------------------


def test_the_memory_draft_refuses_the_factory_name(
    tmp_path, monkeypatch
) -> None:
    """擬記憶草稿那一支自己那道門。

    呼叫端（`maintain_memory_after_pass`）今天會先擋下來並收成
    `MEMORY_NO_SEAT_FOR_DUTY`（「跑完了，但共同記憶沒有人維護」）。
    這一條走的是那道門後面那一層。

    改壞的樣子：退回 `reviewer`，而這一步跑在整輪的最後——五次派工的額度
    全部花完之後，畫面在最後一秒變成紅燈加一坨 traceback。
    """
    orchestrator, runner_class = _orchestrator(
        tmp_path, monkeypatch, NOBODY_REVIEWS
    )
    done = cli_result("做完了")
    with pytest.raises(NoSeatWithDuty) as raised:
        orchestrator.run_codex_memory_update(
            claude_result=done,
            codex_review=done,
            git_diff="diff",
            changed_files=["a.py"],
        )
    assert raised.value.duty == DUTY_REVIEW
    assert runner_class.calls == [], "已經派了工才停"


def test_the_memory_review_refuses_the_factory_name(
    tmp_path, monkeypatch
) -> None:
    """審記憶草稿那一支自己那道門。**缺的是另一種職責。**

    只修草稿那一支的話，traceback 往後挪二十行、訊息裡的名字從「reviewer」
    換成「author」，畫面一樣紅。

    這個產品不會把一份沒有人看過的東西寫進八份共同記憶——其中兩份是
    `SECURITY_MODEL.md` 與 `OPEN_QUESTIONS.md`。
    """
    orchestrator, runner_class = _orchestrator(
        tmp_path, monkeypatch, NOBODY_DOES_THE_WORK
    )
    with pytest.raises(NoSeatWithDuty) as raised:
        orchestrator.run_claude_memory_review("## NEXT_STEPS.md\n草稿內容\n")
    assert raised.value.duty == DUTY_WORK
    assert runner_class.calls == [], "已經派了工才停"


def test_a_line_with_both_seats_is_not_stopped(
    tmp_path, monkeypatch
) -> None:
    """**不准擋過頭。**兩種職責都排了的陣容，那兩支都要派得出工。

    一條什麼都拒絕的門，跟一個永遠說可以的門一樣沒用。這裡量的是「不丟
    例外而且真的送出去了」——「不丟例外」自己一個不夠：什麼都不做也不會
    丟例外。

    （整輪跑完之後那一步照不照跑，守在
    `test_the_way_out_it_offers_does_not_end_in_the_same_traceback.py`。）
    """
    both = list(NOBODY_REVIEWS) + [
        AgentSlot("複查那一格", "codex", duty=DUTY_REVIEW)
    ]
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = _project(tmp_path, both)
    runner_class = scripted_cli_runner_class(
        [cli_result(EVERY_SEAT_SAYS) for _ in range(4)], auto=False
    )
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)
    orchestrator = Orchestrator(
        project,
        run_id="a-run",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
        lineup=Lineup.of(both),
    )
    done = cli_result("做完了")

    orchestrator.run_codex_memory_update(
        claude_result=done,
        codex_review=done,
        git_diff="diff",
        changed_files=["a.py"],
    )
    orchestrator.run_claude_memory_review("## NEXT_STEPS.md\n草稿內容\n")
    orchestrator.run_codex_decision_gate("要不要把舊的設定檔一起刪掉？")

    assert len(runner_class.calls) == 3, (
        f"三步只派出去 {len(runner_class.calls)} 次工"
    )
    # 而且派給的是他排的那幾格，不是出廠名字。
    for call in runner_class.calls:
        assert "author" not in call["arguments"], call["arguments"]
        assert "reviewer" not in call["arguments"], call["arguments"]
