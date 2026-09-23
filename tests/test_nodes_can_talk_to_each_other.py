"""一格卡住的時候，可以指名找另一格討論。

老闆 2026-08-22 的五節點流程裡有三處寫著這件事：

> NODE2 依專案資訊撰寫規劃，**丟回 NODE1 確認可行性**
> NODE3 UI 設計，**有問題向 NODE2 討論**
> NODE4 執行程式設計與 UI 串聯，**有問題與 NODE3 討論**

在這之前，引擎裡沒有這條路。每一格只能對著它拿到的文字自己猜——而猜錯要等到
最後複查才會被發現，那時候要重做的不只是它那一格。

這一份守四件事：

1. **問得出去，答得回來，而且答案真的被下一輪看到。**看不到的話，那次討論
   就只是一次付了錢的獨白。
2. **指名一格不存在的名字要停下來，不准替它挑一個。**挑了就變回「問上一關」
   ——那正是【駁回直接送給該負責的人】否決的形狀。
3. **沒有次數上限。**收斂的判準是「同一個問題問第二次還是同一個問題」，
   不是「問幾次就不准再問」。設了次數，最後一輪它會為了結束而假裝問清楚了。
4. **不討論的時候一次呼叫都不多。**這條守的是錢：加了一條路，沒有走那條路
   的專案不該多付任何一毛。
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
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    calls_after_intake,
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

NEWLINE = chr(10)

PASS_REVIEW = (
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)

# 被問的那一格回答的那句話。它必須出現在提問那一格的下一輪提示詞裡。
ANSWER = "哨兵-b71e2-用一個下拉，因為選項會長到七個以上"

FIVE_NODES = [
    AgentSlot("發想", "codex", duty=DUTY_PLAN),
    AgentSlot("規劃", "claude", duty=DUTY_PLAN),
    AgentSlot("實作", "claude", duty=DUTY_WORK),
    AgentSlot("複查", "codex", duty=DUTY_REVIEW),
]


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
        **worker_kwargs,
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


def _asks(who: str, question: str):
    return cli_result(f"討論：{who}\n{question}")


# --- 一、問得出去，答得回來 -------------------------------------------------


def test_a_seat_can_ask_another_and_sees_the_answer_next_round(
    tmp_path: Path, monkeypatch
) -> None:
    """規劃卡住 → 指名問發想 → 拿到答案 → 帶著答案再擬一次。

    答案沒有回到提問那一格的話，那次討論就只是一次付了錢的獨白：它問了、
    別人答了、而它從頭到尾沒看到。
    """
    project = _project(tmp_path)
    _seat(project, FIVE_NODES)

    completed, failed, stages, calls = _run(
        project,
        monkeypatch,
        [
            _asks("發想", "這裡要三個按鈕還是一個下拉？"),    # 規劃：卡住
            cli_result(ANSWER),                             # 發想：回答
            cli_result("規劃的產出：照它說的做。"),           # 規劃：這次寫得出來
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"

    # 被問的那一格拿到的是「討論」的身分，不是被派工。
    consulted = calls[1]["input_text"] or ""
    assert "被另一個節點指名討論" in consulted
    assert "【規劃】問你：" in consulted
    assert "這裡要三個按鈕還是一個下拉？" in consulted
    assert "不得修改任何檔案" in consulted, (
        "被諮詢的那一格拿到了寫入權限——一次討論會變成兩個人同時在改同一批"
        "檔案，而使用者從來沒有把這件事排進流程裡"
    )

    # 而提問的那一格，下一輪要看得到答案。
    again = calls[2]["input_text"] or ""
    assert ANSWER in again, "討論的答案沒有回到提問的那一格手上"
    assert "你問【發想】：" in again

    assert "規劃正在跟發想討論" in stages


def test_the_question_goes_to_the_seat_it_names(
    tmp_path: Path, monkeypatch
) -> None:
    """指名誰就問誰。發想是 codex、實作與規劃是 claude——問錯人這條會紅。"""
    project = _project(tmp_path)
    _seat(project, FIVE_NODES)

    _completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            _asks("發想", "可行嗎？"),
            cli_result(ANSWER),
            cli_result("規劃的產出。"),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert calls[1]["arguments"][0] == "codex", (
        f"指名的是【發想】（codex），實際問了 {calls[1]['arguments'][0]}"
    )


def test_the_seat_is_told_who_it_may_ask(
    tmp_path: Path, monkeypatch
) -> None:
    """名冊要跟著提示詞一起給，而且**不包含它自己**。

    不給名冊，它只能編一個名字，然後那一輪就浪費掉了。
    把自己列進去，它會問自己——答案一定跟它已經想的一樣，而要付兩次錢。
    """
    project = _project(tmp_path)
    _seat(project, FIVE_NODES)

    _completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result("規劃的產出。"),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )
    assert failed == [], failed

    to_planner = calls[0]["input_text"] or ""
    assert "討論：" in to_planner, "沒有告訴它可以找人討論"
    assert "發想" in to_planner and "實作" in to_planner
    assert "工作線上還有這幾格：發想、實作、複查" in to_planner, (
        "名冊裡把它自己也列進去了"
    )


def test_a_line_up_of_one_is_not_told_to_go_ask_somebody(
    tmp_path: Path, monkeypatch
) -> None:
    """只有一格的線上不要寫「你可以找別人討論」。

    寫了它會照做，然後指名一個不存在的節點——那一輪就這樣浪費掉了。
    """
    project = _project(tmp_path)
    _seat(project, [AgentSlot("實作", "claude", duty=DUTY_WORK)])

    _completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
        ],
    )
    assert failed == [], failed
    assert "討論：" not in (calls[0]["input_text"] or "")


# --- 二、指名錯了就停，不猜 -------------------------------------------------


@pytest.mark.parametrize(
    "reply, why",
    [
        ("討論：不存在的那一格\n可行嗎？", "指名一格不在線上的"),
        ("討論：\n可行嗎？", "說要討論卻沒指名"),
        ("討論：發想\n", "指名了卻沒寫問題"),
        ("討論：規劃\n我問我自己", "指名自己"),
    ],
)
def test_an_unusable_consult_request_stops_the_run(
    tmp_path: Path, monkeypatch, reply: str, why: str
) -> None:
    """引擎不會替它挑一個對象。

    挑了就變回「問上一關」，而那正是【駁回直接送給該負責的人】否決的形狀。
    安靜地當成「沒有要討論」也不行——那等於把它的疑問丟掉，而它接下來寫的
    東西建立在一個沒有人回答的假設上。
    """
    project = _project(tmp_path)
    _seat(project, FIVE_NODES)

    completed, failed, _stages, calls = _run(
        project, monkeypatch, [cli_result(reply)]
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "CONSULT_INVALID", why
    assert len(calls) == 1, "看不懂的指名還繼續派工"
    assert completed[0]["test_output"].strip(), (
        "停下來了，但沒有告訴任何人為什麼"
    )


# --- 三、沒有次數上限，但同一題問兩次就是卡住 -------------------------------


def test_there_is_no_cap_on_how_many_times_a_seat_may_ask(
    tmp_path: Path, monkeypatch
) -> None:
    """問第三題、第四題都可以，只要每次問的是不同的事。

    設了次數上限的話，最後一輪它會為了結束而假裝問清楚了——而它接下來寫的
    東西建立在一個它自己都知道沒問清楚的假設上。
    """
    project = _project(tmp_path)
    _seat(project, FIVE_NODES)

    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            _asks("發想", "第一題？"),
            cli_result("答一"),
            _asks("發想", "第二題？"),
            cli_result("答二"),
            _asks("發想", "第三題？"),
            cli_result("答三"),
            _asks("發想", "第四題？"),
            cli_result("答四"),
            cli_result("規劃的產出。"),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED", (
        "問到第四題就不准問了——這正是次數上限的樣子"
    )
    assert len(calls) == 12


def test_the_same_question_twice_stops_and_asks_the_user(
    tmp_path: Path, monkeypatch
) -> None:
    """同一個問題問第二次還是同一個問題 = 卡住。停下來交給使用者。

    不自動放行也不自動終止：放行的話它會照著一個沒有被回答的假設繼續做；
    終止的話使用者只會看到「失敗」，而真正發生的是兩格之間有一件事講不通。
    """
    project = _project(tmp_path)
    _seat(project, FIVE_NODES)

    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            _asks("發想", "同一題？"),
            cli_result("我不知道"),
            _asks("發想", "同一題？"),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "CONSULT_STUCK"
    assert len(calls) == 3, "卡住了還繼續派工"
    assert "同一題？" in completed[0]["test_output"]


# --- 四、不討論的時候一毛都不多花 -------------------------------------------


def test_a_run_where_nobody_asks_costs_exactly_what_it_did_before(
    tmp_path: Path, monkeypatch
) -> None:
    """沒有人問，就一次呼叫都不多。

    加一條路而讓沒有走那條路的專案多付錢，是這個專案最不能接受的那種改動
    ——它花的是使用者的錢，而畫面上什麼都不會說。

    假腳本用完會丟 AssertionError，所以這個剛好夠的長度**就是**斷言：
    多一次呼叫，這條會紅。
    """
    project = _project(tmp_path)
    _seat(project, FIVE_NODES)

    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result("規劃的產出。"),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert len(calls) == 4


def test_it_finds_the_seat_even_when_the_model_types_the_name_differently() -> None:
    r"""模型回的名字是**使用者打的那個寫法**，存檔的是小寫。

    ## 2026-08-25 量到的

    座位名在 `AgentSlot.__post_init__` 會被壓成小寫。使用者把一格取名
    「NODE2」，提示詞裡印給模型看的也是他打的那個字，模型回
    「討論：【NODE2】」——而 `seats` 裡是 `node2`。

    比對用 `who not in seats` 的時候那一句被判成：

    > 它指名的【NODE2】不在這個專案的工作線上。

    **畫面說模型指了一個不存在的人，而模型指對了。**而且這一條在拍板那一關
    是同一個形狀：核准那一格說「退給 NODE2」，引擎說線上沒有那一格。

    ## 為什麼回的是存檔那個寫法

    下游要拿它去派工、蓋章、查對話，那幾張表的鍵都是小寫的。
    比對過了卻回原樣的話，**下一步照樣找不到人**——那是把同一個 bug
    往後挪一格。
    """
    from app.core.orchestrator_parsing import (  # noqa: PLC0415
        parse_approval,
        parse_consult_request,
    )

    seats = ("node1", "node2", "node3")
    for typed in ("NODE2", "node2", "Node2", "【NODE2】"):
        asked = parse_consult_request(f"討論：{typed}\n這樣可以嗎？", seats=seats)
        assert not asked["problem"], (
            f"模型打「{typed}」被判成指了一個不存在的人：{asked['problem']}"
        )
        assert asked["who"] == "node2", (
            f"回的是「{asked['who']}」而不是存檔那個寫法——"
            "下一步派工會拿它去查，查不到"
        )

    sent_back = parse_approval(
        "拍板：不可以" + NEWLINE + "退給：【NODE3】" + NEWLINE + "理由：驗收標準沒對上",
        seats=seats,
    )
    assert not sent_back["problem"], sent_back["problem"]
    assert sent_back["who"] == "node3"
