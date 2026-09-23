r"""計畫序列排不到複查的時候，畫面請他走的第二條路，不准走回同一坨 traceback。

## 這一條是 2026-08-26 覆核上一條的時候撞出來的

上一條（`test_a_planning_run_with_nobody_to_review_says_which_duty_is_missing`）
把計畫序列的第一秒修好了：他的五格（發想／規劃／ui設計＝擬做法、執行、審閱＝
核准，**一格複查都沒有**）排不到複查，收成一個講得出理由的結局，並且告訴他

> 兩條路：到陣容頁把其中一格的職責改成「複查」，
> 或到「AI 執行設定」把序列換成「開發序列」。

**第二條路當時走不通。**同一份陣容換成開發序列，實測（七次派工的額度全部
花完之後）：

    AttributeError: 'NoneType' object has no attribute 'stdout'
      app/core/orchestrator.py:2195  run_codex_memory_update

沒有一格複查的時候 `steps/reviewing.look()` 回 `NO_REVIEWING_SEAT`，而
2026-08-24 起那不再停下整輪——`codex_result` 是 `None`，一路傳到維護共同記憶
那一步當成 `CliResult` 用。**而補掉那個 `None` 只是往前挪一行**：下一行
`seat_with_duty(DUTY_REVIEW, fallback=ROLE_REVIEWER)` 退回 `reviewer`，那是
他的陣容裡不存在的名字，於是

    NotImplementedError: 這個專案的陣容裡沒有「reviewer」這一格，但工作流
    要派工給它。目前排的是：發想、規劃、ui設計、執行、審閱。請到陣容頁重新編組。

**也就是上一條要消滅的那一句話本人**，只是它躲在整輪的最後一步。

## 改壞的樣子

按照畫面說的把序列換成「開發序列」→ 五格全部跑完、測試過了、拍板也蓋了 →
**畫面在最後一秒變成紅燈加一坨 traceback**，而他剛剛才照著上一句話換過來。

## 使用者現在會看到什麼

一個 `partial` 的收場（不是紅燈、不算進「今日錯誤 N 件」），底下寫著：

> 程式做完了、驗證過了，產出都在。只有最後一步沒做：維護共同記憶要先請
> 職責是「複查」的那一格擬草稿，而這份陣容的 5 格（發想、規劃、ui設計、
> 執行、審閱）裡一格都不是——AICC 不替你挑一格代打。到陣容頁把其中一格的
> 職責改成「複查」，或到「AI 執行設定」把「自動維護共同記憶」關掉。

**先講產出還在**，因為他讀到「共同記憶沒有人維護」的第一個念頭是整輪重跑，
而那要再花一次七次派工的額度、停在同一個地方。

## 同一條路上有兩個坑，這份檔案兩個都守

第二個更早撞上：做事那一格**中途問了一題**（設定面板上那句話自己寫著這會
發生——「高風險、權限受阻或需求不明時」），AICC 會先請一格複查判斷這題要不
要打擾他，而那一步也是 `fallback=ROLE_REVIEWER`。實測第五次派工之後就是同一句
`NotImplementedError`。現在那一題**直接問他**（`ASK_USER`，帶一句為什麼），
因為那本來就是要人回答的題目——挑一格代打的話，那一格會替他回答一個他從來
沒有授權的問題（「要不要把舊的設定檔一起刪掉」），而畫面上看起來像有人審過。
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
from app.core.workflow_worker import (  # noqa: E402
    MEMORY_NO_SEAT_TEXT_KEYS,
    WorkflowWorker,
    why_memory_was_not_maintained,
)
from app.i18n import STRINGS  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

# 老闆真的排的那五格。**一格複查都沒有**，而這份陣容沒有任何一格是錯的。
HIS_FIVE = [
    AgentSlot("發想", "codex", duty=DUTY_PLAN),
    AgentSlot("規劃", "claude", duty=DUTY_PLAN),
    AgentSlot("ui設計", "claude", duty=DUTY_PLAN),
    AgentSlot("執行", "claude", duty=DUTY_WORK),
    AgentSlot("審閱", "codex", duty=DUTY_APPROVE),
]

# **每一次派工都回同一則。**這一份檔案量的是「派了幾次工、收場是什麼」，
# 不是「第幾步收到什麼」——而一則一則對位的腳本會讓「陣容多一格」這種事
# 整份錯位，然後測試紅在一個跟它要守的東西無關的地方。
#
# 這一則同時是一份交付說明（`result`）、一次說得出理由的通過
# （`status`／`summary`）、以及一份記憶草稿的審閱結果（`memory_update`）。
EVERY_SEAT_SAYS = json.dumps(
    {
        "result": "做完了",
        "status": "PASS",
        "summary": "看過了，改動跟計畫一致",
        "issues": [],
        "instructions": [],
        "question": "",
        "memory_update": "## NEXT_STEPS.md\n下一步照舊。",
    },
    ensure_ascii=False,
)


def _run(tmp_path: Path, monkeypatch, slots, *, memory: bool = True):
    """開發序列跑一輪，回 (收場, traceback, 派工次數)。

    腳本刻意**比這條路用得到的長**：多派一次工不會撞到
    `ScriptedCliRunner` 的「腳本用完了」，所以「一次都沒多派」是被斷言
    量出來的，不是被腳本長度擋出來的。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 做一件事\n", encoding="utf-8")
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    SessionRegistry(project).save_lineup(Lineup.of(slots))

    runner_class = scripted_cli_runner_class(
        [cli_result(EVERY_SEAT_SAYS) for _ in range(20)]
    )
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            # 他照著上一句話換過來的那一條。
            workflow_mode="development",
            # **出廠值**（`ai_settings.py:161`）。他兩個設定檔都不存在，
            # 所以維護共同記憶是開著的——這一條路要走到底才會撞到。
            auto_memory_maintenance=memory,
            auto_session_bootstrap=True,
        ),
    )
    completed: list = []
    failed: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed, runner_class.calls


def test_the_second_way_out_does_not_end_in_a_traceback(
    tmp_path, monkeypatch
) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：他照著畫面把序列換成開發序列，五格跑完、測試過了，
    然後畫面在最後一秒變成紅燈加一坨 traceback。
    """
    completed, failed, _ = _run(tmp_path, monkeypatch, HIS_FIVE)
    assert not failed, (
        "他照著畫面換過來的那一條路，跑完最後一步丟了 traceback：\n"
        + "\n".join(str(f)[-500:] for f in failed)
    )
    assert completed, "既沒有失敗也沒有完成——那一輪不知道去哪裡了"


def test_the_ending_says_the_work_survived(tmp_path, monkeypatch) -> None:
    """收的是 `MEMORY_NO_SEAT_FOR_DUTY`，而且不准是「跑完了」。

    只斷言「沒有丟例外」的話，安靜地收成 `COMPLETED` 也會過——而那是
    薄荷色的「工作流程已完成」加上一份**沒有人維護的共同記憶**，
    也就是這個專案最在意的那一種假訊號。
    """
    completed, _, _ = _run(tmp_path, monkeypatch, HIS_FIVE)
    result = completed[-1]
    assert result["workflow_status"] == "MEMORY_NO_SEAT_FOR_DUTY", result

    described = outcomes.describe(result)
    assert described.style == "partial", described
    assert described.status in outcomes.known_statuses()
    # 不是「這一輪沒有開始」——這一輪跑完了，那一句是另一種缺法。
    assert result["workflow_status"] != "NO_SEAT_FOR_DUTY"


def test_it_does_not_tell_him_to_just_run_it_again(
    tmp_path, monkeypatch
) -> None:
    """歸「少一項設定」，不歸「再跑一次就好」。

    收成 `MEMORY_ERROR` 也會有一句話、也是 `partial`——但那一句的結尾是
    「再跑一次就好」，而同一份陣容再跑一次會停在同一個地方，只是那一次
    他要先花完一整輪七次派工的額度才看到同一句話。
    """
    completed, _, _ = _run(tmp_path, monkeypatch, HIS_FIVE)
    assert completed[-1].get("outcome_group") == outcomes.GROUP_SETTING, (
        completed[-1]
    )


def test_it_says_which_duty_is_missing_and_names_his_seats(
    tmp_path, monkeypatch
) -> None:
    """那句話要**先講產出還在**，再講缺的是哪一種職責、他排的是哪幾格。"""
    completed, _, _ = _run(tmp_path, monkeypatch, HIS_FIVE)
    said = completed[-1].get("memory_problem", "")

    assert said == why_memory_was_not_maintained(
        DUTY_REVIEW, tuple(s.role for s in HIS_FIVE)
    ), said
    # **而且它要真的到得了畫面。**收場表那一列少了 `detail_key` 的話，
    # 這一句會被那張表的罐頭句子蓋掉，而上面那個 `==` 照樣是綠的。
    assert outcomes.describe(completed[-1]).stage == said
    # 先講產出還在。少了這半句，他的第一個念頭是整輪重跑。
    assert "產出都在" in said, said
    # 一個他的陣容裡沒有的出廠名字，不准出現在他要讀的那句話裡。
    assert "reviewer" not in said, said
    assert "複查" in said, said
    assert "陣容頁" in said, said
    # 他排的那五格要被念出來——講不出名字的說明等於沒有說明。
    for seat in ("發想", "規劃", "ui設計", "執行", "審閱"):
        assert seat in said, (seat, said)


def test_it_picks_the_sentence_for_the_duty_that_is_missing() -> None:
    """**測的是「選哪一句」，不是「那句話長什麼樣」。**

    缺複查是沒有人擬草稿，缺執行是草稿擬得出來但沒有人審它——挑錯那一句
    的時候，兩句話都還是好好的。
    """
    seats = ("甲", "乙")
    assert why_memory_was_not_maintained(
        DUTY_WORK, seats
    ) != why_memory_was_not_maintained(DUTY_REVIEW, seats)
    assert "執行" in why_memory_was_not_maintained(DUTY_WORK, seats)


def test_it_does_not_pick_one_to_stand_in(tmp_path, monkeypatch) -> None:
    """**不准挑一格代打。**

    挑到的那一格會替他改八份共同記憶，其中兩份是 `SECURITY_MODEL.md` 與
    `OPEN_QUESTIONS.md`——「被截斷的安全規則，讀起來跟完整的一模一樣」。

    量法是把同一輪跑兩次、只差在維護共同記憶開或關：**開著的那一次不准
    多派一次工**。腳本還有剩，多派得出來。
    """
    _, _, with_memory = _run(
        tmp_path / "on", monkeypatch, HIS_FIVE, memory=True
    )
    _, _, without_memory = _run(
        tmp_path / "off", monkeypatch, HIS_FIVE, memory=False
    )
    assert len(with_memory) == len(without_memory), (
        f"維護共同記憶開著的時候多派了 "
        f"{len(with_memory) - len(without_memory)} 次工，有一格被挑去代打了"
    )


def test_a_line_with_a_reviewing_seat_still_maintains_memory(
    tmp_path, monkeypatch
) -> None:
    """**不准擋過頭。**

    一條什麼都拒絕的門，跟一個永遠說可以的門一樣沒用。排了複查的陣容，
    維護共同記憶那一步要照跑——而「照跑」量的是**真的派了工**，
    不是「收場碼不是那一個」。
    """
    with_reviewer = list(HIS_FIVE) + [
        AgentSlot("複查那一格", "codex", duty=DUTY_REVIEW)
    ]
    completed, failed, calls = _run(
        tmp_path / "on", monkeypatch, with_reviewer
    )
    assert not failed, failed
    assert completed[-1]["workflow_status"] != "MEMORY_NO_SEAT_FOR_DUTY", (
        completed[-1]
    )
    _, _, without_memory = _run(
        tmp_path / "off", monkeypatch, with_reviewer, memory=False
    )
    assert len(calls) > len(without_memory), (
        "排了複查的陣容，維護共同記憶那一步一次工都沒派出去"
    )


# ---------------------------------------------------------------------------
# 同一條路上的第二個坑：做事那一格問了一題
# ---------------------------------------------------------------------------
#
# 上面那一段守的是「跑到最後一步」。**跑不到最後一步的那一種更早撞上**：
# 做事那一格中途問了一題（「高風險、權限受阻或需求不明」——設定面板上那句
# 話自己寫著這會發生），AICC 會先請一格複查判斷「這題要不要打擾你」。
#
# 那一步 2026-08-26 實機量到的樣子，是第五次派工之後：
#
#     NotImplementedError: 這個專案的陣容裡沒有「reviewer」這一格⋯⋯
#
# **又是同一句話**，而他還是照著上一句話換到這條路上的。

# 做事那一格中途問了一題。`ASK_USER:` 是引擎認的那個記號
# （`orchestrator_parsing.parse_claude_question`）。
A_SEAT_ASKS = json.dumps(
    {
        "result": "ASK_USER: 要不要把舊的設定檔一起刪掉？ END_ASK_USER",
        "status": "PASS",
        "summary": "有一題要問",
        "issues": [],
        "instructions": [],
        "question": "",
    },
    ensure_ascii=False,
)

# 決策關卡那一次派工長什麼樣。認開場白，因為那是那份提示詞裡唯一保證存在、
# 而且指名那一步的句子。
GATE_MARKER = "你是 AI Control Center 的 Decision Gate。"


def _run_with_a_question(tmp_path: Path, monkeypatch, slots):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 做一件事\n", encoding="utf-8")
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    SessionRegistry(project).save_lineup(Lineup.of(slots))

    runner_class = scripted_cli_runner_class(
        [cli_result(A_SEAT_ASKS) for _ in range(20)]
    )
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=True,
            auto_session_bootstrap=True,
        ),
    )
    completed: list = []
    failed: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    QApplication.instance().processEvents()
    gate_calls = [
        call
        for call in runner_class.calls
        if GATE_MARKER in (call.get("input_text") or "")
    ]
    return completed, failed, gate_calls


def test_a_question_with_nobody_to_judge_asks_him_instead(
    tmp_path, monkeypatch
) -> None:
    """沒有一格複查的時候，那一題**直接問他**，不是丟一坨 traceback。

    改壞的樣子：第五次派工之後 `NotImplementedError: 這個專案的陣容裡沒有
    「reviewer」這一格`，而他問的那一題連看都沒看到。
    """
    completed, failed, gate_calls = _run_with_a_question(
        tmp_path, monkeypatch, HIS_FIVE
    )
    assert not failed, (
        "做事那一格問了一題，整輪丟了 traceback：\n"
        + "\n".join(str(f)[-500:] for f in failed)
    )
    assert completed[-1]["workflow_status"] == "ASK_USER", completed[-1]
    # 他問的那一題要到得了畫面——這扇門不通的時候，門後那句話才是重點。
    assert "舊的設定檔" in completed[-1].get("review_question", ""), completed[-1]
    # **而且不准挑一格代打。**代打的那一格會替他回答一個他從來沒有授權的
    # 問題（「要不要把舊的設定檔一起刪掉」），而畫面上看起來像有人審過。
    assert gate_calls == [], f"決策關卡派了 {len(gate_calls)} 次工"


def test_it_says_why_it_is_asking_him_directly(tmp_path, monkeypatch) -> None:
    """那句話要講**為什麼直接問你**，而不是留白。

    留白的話他會以為 AICC 壞了——實際上是這份陣容裡沒有一格能先替他判斷。
    """
    completed, _, _ = _run_with_a_question(tmp_path, monkeypatch, HIS_FIVE)
    said = completed[-1].get("decision_gate", {}).get("reason", "")
    assert "複查" in said, said
    assert "reviewer" not in said, said
    assert "陣容頁" in said, said
    for seat in ("發想", "規劃", "ui設計", "執行", "審閱"):
        assert seat in said, (seat, said)


def test_a_line_with_a_reviewing_seat_still_asks_the_gate(
    tmp_path, monkeypatch
) -> None:
    """**不准擋過頭。**排了複查的陣容，那一題照樣先送進決策關卡。"""
    with_reviewer = list(HIS_FIVE) + [
        AgentSlot("複查那一格", "codex", duty=DUTY_REVIEW)
    ]
    _, failed, gate_calls = _run_with_a_question(
        tmp_path, monkeypatch, with_reviewer
    )
    assert not failed, failed
    assert gate_calls, "排了複查的陣容，決策關卡一次工都沒派出去"


def test_every_duty_this_step_dispatches_has_a_sentence() -> None:
    """這一步派工的兩種職責，每一種都要有自己的一句話。

    少一種的話，那一種缺席的時候 `MEMORY_NO_SEAT_TEXT_KEYS[duty]` 會丟
    KeyError——而那是在**整輪跑完、額度都花完的最後一秒**丟，沒有任何一個
    except 接得住它。這一條讓它在這裡紅。
    """
    assert set(MEMORY_NO_SEAT_TEXT_KEYS) == {DUTY_REVIEW, DUTY_WORK}
    for duty, key in MEMORY_NO_SEAT_TEXT_KEYS.items():
        assert key in STRINGS, (duty, key)
        said = why_memory_was_not_maintained(duty, ("甲", "乙"))
        # 塞得進狀態列。超過的話 `_last_words()` 切的是**前面**，
        # 而前面那半句正是「你的程式沒事」。
        assert len(said) <= outcomes.STAGE_MAX_CHARS, (duty, len(said))
        assert "陣容頁" in said, (duty, said)
        assert "產出都在" in said, (duty, said)
