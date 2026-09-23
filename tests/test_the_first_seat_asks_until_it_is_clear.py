"""第一格要跟使用者把需求問清楚，問到清楚為止，然後那份東西就是驗收標準。

老闆 2026-08-22 的五節點流程，第一格寫的是「發想與確認專案資訊，**與使用者
來回確認**後給 NODE2」。在這之前，引擎裡沒有任何一步是「跟使用者確認需求」
——它拿到任務稿就直接派人動手，而做出來不是他要的東西時，沒有任何一份文件
說得出當初講好的是什麼。

這一份守四件事，每一件都是一種**看起來很正常的錯誤**：

1. **它問得出問題，而且問題到得了使用者。**到不了的話，畫面上會是一個沒有
   回答按鈕的等待，使用者只能按停止。
2. **沒有輪數上限。**定案原文：不准用輪數收尾。設了上限，到最後一輪不管
   清楚沒有，它都會為了結束而宣告定案——而後面每一格都會照那份標準做。
   老闆的原話是「誇張來說問到天亮都沒問題」。
3. **答案只被吃掉一次。**同一句答案如果被下游那條「依照使用者答案繼續」
   再用一次，開發工程師會收到一句回答別人問題的話，然後照著它動手。
4. **格式不對就停下來，不猜。**猜成「還要問」會把一坨命令列輸出丟到使用者
   臉上；猜成「定案」會拿一個問句當驗收標準。兩種當下都分不出來。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.lineup import (  # noqa: E402
    DUTY_PLAN,
    DUTY_REVIEW,
    DUTY_WORK,
    AgentSlot,
    Lineup,
)
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.approval_chain import (  # noqa: E402
    SUBJECT_ACCEPTANCE,
    StampLog,
)
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    SETUP_INTENT_ONLY,
    calls_after_intake,
    cli_result,
    declare_verify_command,
    intake_settled,
    scripted_cli_runner_class,
)

PASS_REVIEW = (
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)

# 一個不可能被猜到的句子。它必須從使用者的答案，一路走到執行那一格手上。
SENTINEL = "哨兵-4c19f-只做實體題庫書，不做線上測驗"


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


def _run(project: Path, monkeypatch, script, **worker_kwargs):
    """跑一次工作流程，回傳 (結果, 失敗訊息, 每一次派工)。

    **這裡刻意不用 `with_intake`**：需求確認正是這一份在測的東西，替它補一則
    現成的回答等於把受測的那一步換成一個罐頭。
    """
    QApplication.instance() or QApplication([])
    # 需求確認正是這一份在測的東西，所以那一步不自動回答；「先交做法」
    # 跟這裡要測的事無關，讓假 CLI 自己接掉。
    runner_class = scripted_cli_runner_class(
        script, auto=SETUP_INTENT_ONLY
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
        **worker_kwargs,
    )
    completed: list = []
    failed: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed, calls_after_intake(runner_class.calls)


def _asks(question: str):
    return cli_result(f"需要確認：{question}\n建議答案：我建議先做最小的。")


# --- 一、問得出問題，而且到得了使用者 ---------------------------------------


def test_a_question_from_the_first_seat_reaches_the_user(
    tmp_path: Path, monkeypatch
) -> None:
    """第一格問的問題要走到使用者面前，而且是**它問的那一句**。

    走「等使用者回答」那條既有的路（`review_status == "ASK_USER"`），不另立
    一個新的等待狀態——第二種等待就是第二套要記得同步的畫面，而漏掉的那一次，
    使用者會看到一個沒有回答按鈕的問題。
    """
    project = _project(tmp_path)

    completed, failed, calls = _run(
        project, monkeypatch, [_asks("要存在本機還是雲端？")]
    )

    assert failed == [], failed
    assert len(calls) == 1, "問了一題就該停下來，不該繼續派工"

    result = completed[0]
    assert result["workflow_status"] == "INTAKE_ASKING"
    assert result["review_status"] == "ASK_USER", (
        "沒有走到「等使用者回答」那條路，畫面上不會出現回答按鈕"
    )
    assert "要存在本機還是雲端？" in result["review_question"]
    assert "建議答案" in result["review_question"], (
        "建議答案沒有一起送出去。使用者來找 AI 就是因為還沒想清楚，"
        "把整包退還給他等於沒做事"
    )


def test_nothing_is_dispatched_before_the_bar_is_set(
    tmp_path: Path, monkeypatch
) -> None:
    """問清楚之前，一個人都不准動手。

    這一條守的是順序本身。先派人做、再回頭問「你要的是什麼」，那個問題已經
    沒有意義了——東西做出來了，而且那一次是要付錢的。
    """
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot("發想", "codex", duty=DUTY_PLAN),
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("複查", "codex", duty=DUTY_REVIEW),
        ],
    )

    _completed, failed, calls = _run(project, monkeypatch, [_asks("要幾題？")])

    assert failed == [], failed
    assert len(calls) == 1
    sent = calls[0]["input_text"] or ""
    assert "你是此專案的需求確認者。" in sent
    assert "不得修改任何檔案" in sent, (
        "需求確認那一格拿到了寫入權限。它的工作是問清楚，不是動手"
    )


# --- 二、沒有輪數上限 -------------------------------------------------------


def test_there_is_no_round_cap(tmp_path: Path, monkeypatch) -> None:
    """問到第四輪、第五輪，它照樣可以繼續問。

    刻意跑到一個「如果有上限，多半會設在這裡」的輪數。定案原文不准用輪數
    收尾：設了上限，到最後一輪不管清楚沒有，它都會為了結束而宣告定案。

    每一輪都重開一次 worker，因為真實世界就是這樣——使用者回答之後，畫面會
    用同一個 run_id 重新啟動一次。
    """
    project = _project(tmp_path)
    answers = ["本機", "五題", "不用", "要", "都可以"]

    run_id = ""
    for index, answer in enumerate(answers):
        completed, failed, _calls = _run(
            project,
            monkeypatch,
            [_asks(f"第 {index + 1} 個問題？")],
            run_id=run_id,
            user_answer=answer if index else "",
            user_question="上一題" if index else "",
        )
        assert failed == [], failed
        result = completed[0]
        assert result["review_status"] == "ASK_USER", (
            f"第 {index + 1} 輪就不肯再問了——這正是輪數上限的樣子"
        )
        assert f"第 {index + 1} 個問題？" in result["review_question"]
        run_id = result["run_id"]


def test_the_answer_goes_back_to_the_seat_that_asked(
    tmp_path: Path, monkeypatch
) -> None:
    """第二輪要看得到第一輪問了什麼、使用者答了什麼。

    看不到的話，它每一輪都會從頭問同樣的問題，而使用者會以為自己講的話沒有
    被聽見——**畫面上完全看不出來哪裡壞了**，因為每一輪看起來都很正常。
    """
    project = _project(tmp_path)

    first, failed, _calls = _run(
        project, monkeypatch, [_asks("要做線上測驗嗎？")]
    )
    assert failed == [], failed

    _second, failed, calls = _run(
        project,
        monkeypatch,
        [_asks("那要不要匯出？")],
        run_id=first[0]["run_id"],
        user_question=first[0]["review_question"],
        user_answer=SENTINEL,
    )
    assert failed == [], failed

    sent = calls[0]["input_text"] or ""
    assert "要做線上測驗嗎？" in sent, "第二輪看不到自己上一輪問了什麼"
    assert SENTINEL in sent, "第二輪看不到使用者回答了什麼"


def test_a_pending_question_is_not_asked_again_for_free(
    tmp_path: Path, monkeypatch
) -> None:
    """有問題還掛著、而這一次沒有人帶答案回來——不要再派一次工。

    再派一次會問出一個新的問題，舊的那題就這樣消失了，而且要再付一次錢。
    使用者看到的是「我明明還沒回答，它怎麼換了一題」。
    """
    project = _project(tmp_path)

    first, failed, _calls = _run(project, monkeypatch, [_asks("要存哪裡？")])
    assert failed == [], failed

    second, failed, calls = _run(
        project, monkeypatch, [], run_id=first[0]["run_id"]
    )

    assert failed == [], failed
    assert calls == [], "又派了一次工——舊的那一題被沖掉了，而且多付一次錢"
    assert second[0]["review_status"] == "ASK_USER"
    assert "要存哪裡？" in second[0]["review_question"], (
        "掛著的那一題不見了"
    )


# --- 三、答案只被吃掉一次 ---------------------------------------------------


def test_the_answer_is_not_handed_to_the_worker_as_well(
    tmp_path: Path, monkeypatch
) -> None:
    """回答第一格的那句話，不准也被當成回答開發工程師的話。

    兩條路都讀 `user_answer`。需求確認吃掉之後沒有清空的話，開發工程師會收到
    一句**回答別人問題的話**，被告知「使用者已經回覆」，然後照著它動手——
    而那句話跟它手上的工作沒有關係。
    """
    project = _project(tmp_path)

    first, failed, _calls = _run(project, monkeypatch, [_asks("要存哪裡？")])
    assert failed == [], failed

    _second, failed, calls = _run(
        project,
        monkeypatch,
        [
            intake_settled(),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
        run_id=first[0]["run_id"],
        user_question=first[0]["review_question"],
        user_answer=SENTINEL,
    )
    assert failed == [], failed

    to_the_worker = calls[1]["input_text"] or ""
    assert "你是此專案的開發工程師。" in to_the_worker
    # 「因為需要使用者決策而停止」那句話只出現在 `build_user_answer_prompt`
    # 裡——那是**回答開發工程師自己問的問題**那條路。它出現在這裡，就代表
    # 回答第一格的那句話被下游又吃了一次。
    assert "你先前因為需要使用者決策而停止" not in to_the_worker, (
        "回答第一格的那句話被下游又用了一次。開發工程師會被告知「使用者"
        "已回答」，然後照著一句回答別人問題的話動手"
    )
    assert SENTINEL not in to_the_worker, (
        "使用者回答第一格的原話直接漏進了執行那一格的提示詞"
    )


# --- 四、格式不對就停，不猜 -------------------------------------------------


@pytest.mark.parametrize(
    "reply, why",
    [
        ("我覺得這個需求還可以，就這樣吧。", "兩個開頭都沒寫"),
        (
            "需要確認：要存哪裡？\n需求定案：\n1. 完成狀態：做完。",
            "兩個開頭都寫了",
        ),
    ],
)
def test_a_reply_the_engine_cannot_read_stops_the_run(
    tmp_path: Path, monkeypatch, reply: str, why: str
) -> None:
    """分不出它是在問還是在交件的時候，停下來。

    **不猜。**猜成「還要問」會把一坨命令列輸出丟到使用者臉上；猜成「定案」
    會拿一個問句當驗收標準，而後面每一格都照著它做。兩種在當下都看不出來。
    """
    project = _project(tmp_path)

    completed, failed, calls = _run(project, monkeypatch, [cli_result(reply)])

    assert failed == [], failed
    assert len(calls) == 1, "格式看不懂還繼續派工"
    result = completed[0]
    assert result["workflow_status"] == "INTAKE_INVALID", why
    assert result["review_status"] != "ASK_USER", (
        "把一份看不懂的回覆當成問題丟給使用者，他會看到一坨命令列輸出"
    )
    assert "需求定案" in result["test_output"], (
        "停下來了，但沒有告訴任何人該怎麼做"
    )


# --- 五、訂下來的東西要真的被用到 -------------------------------------------


def test_the_bar_reaches_the_seat_that_does_the_work(
    tmp_path: Path, monkeypatch
) -> None:
    """訂好的驗收標準要出現在執行那一格的提示詞裡，而且帶著駁回的那條路。

    到不了的話，這一整步就只是一次付了錢的對話：問清楚了，然後沒有人照著做。
    """
    project = _project(tmp_path)

    completed, failed, calls = _run(
        project,
        monkeypatch,
        [
            intake_settled(f"1. 完成狀態：{SENTINEL}"),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"

    to_the_worker = calls[1]["input_text"] or ""
    assert SENTINEL in to_the_worker, "驗收標準沒有傳到執行的那一格"
    assert "駁回驗收標準：" in to_the_worker, (
        "執行的那一格不知道自己可以退回這份標準。老闆問的正是這個："
        "做不到的時候，任務不該就這樣失敗"
    )


def test_the_bar_is_stamped_by_the_seat_that_asked(
    tmp_path: Path, monkeypatch
) -> None:
    """訂標準的那一格要留下章，而且理由要講得出憑什麼。

    只記「誰、何時、哪一版」的章，回答不了事後最需要的那一題：憑什麼認為
    問清楚了。這裡要求它寫出來回了幾輪、最後問的是什麼。
    """
    project = _project(tmp_path)

    first, failed, _calls = _run(project, monkeypatch, [_asks("要存哪裡？")])
    assert failed == [], failed

    second, failed, _calls = _run(
        project,
        monkeypatch,
        [
            intake_settled(),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
        run_id=first[0]["run_id"],
        user_question=first[0]["review_question"],
        user_answer=SENTINEL,
    )
    assert failed == [], failed

    chain = StampLog(project).read(second[0]["run_id"])
    bar = [s for s in chain.stamps if s.subject == SUBJECT_ACCEPTANCE]

    assert len(bar) == 1, f"驗收標準的章不是剛好一個：{bar}"
    assert bar[0].role == "reviewer"
    assert SENTINEL in bar[0].reasoning, (
        "章的理由沒有講出憑什麼——使用者最後那一句答案不見了"
    )
    assert bar[0].depends_on == (), (
        "驗收標準的章建立在別的章之上。它是整條鏈的地基，掛在別人底下的話，"
        "駁回之後重訂的標準會掛在舊實作底下——顛倒的"
    )


def test_resuming_does_not_pay_for_the_bar_twice(
    tmp_path: Path, monkeypatch
) -> None:
    """同一次執行，驗收標準只訂一次。

    重訂會再付一次錢，而且很可能拿到一份不一樣的標準——那會讓已經蓋在舊標準
    上的章全部失效，畫面上看起來像是有人偷偷改了規則。
    """
    project = _project(tmp_path)

    first, failed, _calls = _run(
        project,
        monkeypatch,
        [
            intake_settled(),
            cli_result("", success=False),
        ],
    )
    assert failed == [], failed

    _second, failed, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
        run_id=first[0]["run_id"],
    )

    assert failed == [], failed
    assert len(calls) == 3, (
        "續跑時又跑了一次需求確認。同一次執行的驗收標準只准訂一次"
    )


def test_a_line_up_with_only_a_worker_does_not_set_its_own_bar(
    tmp_path: Path, monkeypatch
) -> None:
    """整條線只有執行那一格的時候，沒有人可以訂標準——**而且不硬挑一格**。

    挑到的那一格會拿到「訂標準」的權限，而使用者從來沒有把它排成那個角色。
    那就是【驗收標準由複查角色訂】要防的自己出考卷自己考。

    這裡不擋執行，只是章會誠實地缺一個。
    """
    project = _project(tmp_path)
    _seat(project, [AgentSlot("實作", "claude", duty=DUTY_WORK)])

    completed, failed, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
        ],
    )

    assert failed == [], failed
    sent = calls[0]["input_text"] or ""
    assert "你是此專案的需求確認者。" not in sent, (
        "沒有人可以訂標準的陣容，卻還是有人被派去訂了"
    )

    chain = StampLog(project).read(completed[0]["run_id"])
    assert not [
        s for s in chain.stamps if s.subject == SUBJECT_ACCEPTANCE
    ], "沒有人訂標準，卻蓋出了一個驗收標準的章"


def test_the_bar_stays_the_foundation_even_when_it_is_set_late(
    tmp_path: Path,
) -> None:
    """驗收標準是整條鏈的地基，**什麼時候蓋的都一樣**。

    直接對蓋章員下手，因為要測的情境在流程裡還做不出來：駁回之後標準會被重訂
    一次，而那時候「上一個章」是別人的實作章。順順地接下去的話，新標準會掛在
    舊實作底下——顛倒的，而且從此標準改了實作也不會失效。

    這一條刻意不經過工作流程：經過的話，acceptance 剛好是這一次執行的第一個
    章，`depends_on` 本來就是空的，測不到任何東西。
    """
    from app.core.run_stamps import RunStamper
    from app.services.approval_chain import SUBJECT_WORK

    project = tmp_path / "late"
    (project / ".aicc").mkdir(parents=True)

    stamper = RunStamper(project, "run-late")
    stamper.work_done(
        deliverable="舊的交付物", succeeded=True, evidence="離開碼 0"
    )
    stamper.acceptance_set(
        criteria="重訂之後的標準", role="reviewer", reasoning="做不到，改了"
    )

    chain = StampLog(project).read("run-late")
    bar = [s for s in chain.stamps if s.subject == SUBJECT_ACCEPTANCE]
    work = [s for s in chain.stamps if s.subject == SUBJECT_WORK]

    assert len(bar) == 1 and len(work) == 1
    assert bar[0].depends_on == (), (
        "重訂的驗收標準掛在舊實作的章底下。地基掛在建築物上，"
        f"從此標準改了也不會讓實作失效：{bar[0].depends_on}"
    )


# --- 專案自己那份計畫書 -----------------------------------------------------
#
# 老闆 2026-08-22：「我們的發想角色會把專案跟使用者討論完的計畫書寫成檔案，
# 然後讓後續的 NODE 去看專案目標與對應欄位對吧??」
#
# 在這之前答案是一半：那份東西存在，但存在 `.aicc/runs/<run_id>/`——**下一次
# 執行從零開始問**。《AI 協作手冊》「還沒吸收」那一欄的第一條寫的正是這件事：
# 「每次重新摸專案，前半場都浪費在問背景」。


def test_the_settled_bar_lands_in_the_project_not_just_the_run(
    tmp_path: Path, monkeypatch
) -> None:
    """問清楚的那份東西要寫進**專案**，不是只留在這一次執行的資料夾。

    只留在執行資料夾的話，下一次跑就從零開始問——而使用者要把同樣的背景
    再講一次。
    """
    project = _project(tmp_path)

    completed, failed, _calls = _run(
        project,
        monkeypatch,
        [
            intake_settled("1. 完成狀態：題庫可以匯出成 PDF。"),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    brief = project / ".aicc" / "project-brief.md"
    assert brief.is_file(), (
        "確認完的計畫書沒有留在專案裡——下一次跑會從零開始問"
    )
    assert "匯出成 PDF" in brief.read_text(encoding="utf-8")


def test_the_next_run_starts_from_what_was_already_agreed(
    tmp_path: Path, monkeypatch
) -> None:
    """下一次執行看得到上一次確認過的，而且被告知「只問有什麼不一樣」。

    這一條守的是那句話本身：光把舊的塞進提示詞、卻沒說要拿它做什麼，模型
    照樣會從頭問一遍。
    """
    project = _project(tmp_path)
    (project / ".aicc" / "project-brief.md").write_text(
        "1. 完成狀態：題庫可以匯出成 PDF。", encoding="utf-8"
    )

    _completed, failed, calls = _run(
        project, monkeypatch, [_asks("這次要加什麼？")]
    )

    assert failed == [], failed
    sent = calls[0]["input_text"] or ""
    assert "匯出成 PDF" in sent, "上一次確認過的東西沒有被帶進來"
    assert "不是重新問一遍" in sent, (
        "帶進來了，但沒有說要拿它做什麼——它照樣會從頭問一遍"
    )


def test_a_half_written_brief_cannot_be_left_behind(tmp_path: Path) -> None:
    """寫壞的話，留下的要是完整的舊版，不是半份新的。

    **半份計畫書讀起來跟完整的一模一樣**——而後面每一格會照著那半份做。
    """
    from app.core.orchestrator import Orchestrator

    project = _project(tmp_path)
    orchestrator = Orchestrator(project, run_id="run-brief")
    orchestrator.save_project_brief("第一版：完整的。")

    # 直接對著暫存檔的位置放一個資料夾，讓 replace 失敗。
    (project / ".aicc" / "project-brief.md.tmp").mkdir(exist_ok=True)
    orchestrator.save_project_brief("第二版：寫到一半。")

    assert orchestrator.project_brief() == "第一版：完整的。", (
        "寫失敗之後留下的不是完整的舊版"
    )
