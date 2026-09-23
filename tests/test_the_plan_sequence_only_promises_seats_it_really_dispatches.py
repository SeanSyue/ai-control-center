r"""計畫序列那張工作表上寫著工作的每一格，引擎這一輪真的要派工給它。

## 改壞的樣子（2026-08-27 之前就是這個樣子）

`Orchestrator.SEQUENCE_JOBS["planning"]["jobs"]` 裡有一行
`DUTY_APPROVE: "job.approve"`，而 `workflow_worker._run_planning`
**一次都沒有碰過核准那一格**——拍板那一關（`_approval_phase`）只長在
開發序列那條路上。

實測老闆那五格（發想／規劃／ui設計／執行／審閱）：

    陣容頁說   派工給 2 格（執行、審閱）
    引擎實際   派 0 格

## 使用者會看到什麼

改之前：他排的「審閱」那一格上印著**「這一輪：拍板」**——一句他會照著
安排的承諾。他會等一個永遠不會出現的拍板，而執行紀錄上那一格從頭到尾
沒有一行；畫面上沒有任何一個字說了這件事。

改之後：那一格畫成虛線圈（「這次不會跑」）。**看起來像少了東西，
而那正是事實**——引擎真的不會派工給它。空白他會去問，承諾他不會。

## 這一份怎麼問

**不是逐條比對字面。**照著那張表再抄一份斷言的話，加一行新的謊話會被
一起抄過去，兩邊永遠相等——那種測試守的是「這張表沒有被動過」，
不是「這張表說的是真的」。

所以這裡真的跑一輪計畫序列，把**引擎自己記下來的派工**
（`StepLog`，`_run_role_prompt` 是每一次模型呼叫的唯一漏斗）拿出來，
跟**陣容頁拿到的那份承諾**（`Orchestrator.sequence_for`，頁面吃的是
同一個函式的同一份輸出）比對座位名字。兩個方向都會紅：

* 表上多一行沒人兌現的（說謊）→ 承諾的座位多一個
* 引擎派給一個表上沒寫的（漏講）→ 派工的座位多一個

## 〈它擋不住什麼〉

1. **討論回合 0 的時候，`job.read_plan` 那一行也是空頭支票。**
   `_run_planning` 的 `for round_number in range(1, rounds + 1)` 在
   `planning_rounds == 0` 時一圈都不跑，於是審計畫那一步
   （`run_claude_plan_review`，派給「執行」那一格）不存在——而
   **出廠值就是 `planning_rounds=0`**（`ai_settings.py`）。

   2026-08-27 實機量過（一格執行＋一格複查、回合 0）：

       頁面說   派工給 2 格
       引擎派   1 格（只有複查那一格）

   這一份用 `rounds=1` 跑，所以量不到那個狀態。`sequence_for()` 目前
   看不到 `planning_rounds`，要修得先決定「工作表要不要吃執行設定」——
   **那不是這一輪拍板的範圍，留給 PM。**

2. **同一種職責排兩格的時候，只有最後一格會跑。**
   計畫序列的兩步都是 `required_seat_with_duty()`，而它回的是
   `seats[-1]`（`orchestrator.seat_with_duty`）；那張表是**按職責**排的，
   說不出「只有最後一格」。2026-08-27 實機量過（複查兩格＋執行兩格、
   回合 1）：

       頁面說   派工給 4 格
       引擎派   查二、做二、查二——**查一與做一整輪沒有被叫過**

   開發序列不是這樣：擬做法／執行／複查那三步都是把**每一格**同職責的
   都跑一遍（`steps/planning.py`、`building.py`、`reviewing.py`），
   所以同一份陣容換一條序列，結果不一樣。要修得先決定
   「計畫序列要不要也跑每一格（多花額度）」——**留給 PM。**

3. **開發序列那張表沒有在這裡跑。** 這一份只斷言「核准那一種還留在
   開發序列裡」（防的是照著拿掉一行、順手把兩張表都拿掉的過修），
   核准那一關真的會派工給誰是
   `tests/test_the_last_seat_reads_every_stamp.py` 在守。

4. **虛線圈長什麼樣是頁面畫的。** 這裡只到「後端沒有給那一格工作」為止；
   沒有工作的那一格畫成什麼，在 `AICC-視覺概念/lineup_v1.html`。
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
    DUTY_WORK,
    AgentSlot,
    Lineup,
)
from app.core.orchestrator import Orchestrator  # noqa: E402
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

# 四種職責一格一個，名字是使用者取的。
#
# **每一種都有人坐**是這一份的前提：少排一種的話，「沒有被派工」可以被
# 解釋成「沒有人可以派」，而這裡要問的是「有人坐著，引擎要不要用他」。
EVERY_DUTY = [
    AgentSlot("阿想", "claude", duty=DUTY_PLAN),
    AgentSlot("阿做", "claude", duty=DUTY_WORK),
    AgentSlot("阿查", "codex", duty=DUTY_REVIEW),
    AgentSlot("阿拍", "claude", duty=DUTY_APPROVE),
]

# 討論回合 1：擬一版、審一次、再統整一版。三次呼叫。
ONE_ROUND = [
    cli_result("第一版計畫"),
    cli_result("這一版少了驗收標準"),
    cli_result("最終計畫"),
]


def _run_the_plan_sequence(
    tmp_path: Path, monkeypatch, slots, script, rounds: int
):
    """真的跑一輪計畫序列，回傳（結局、引擎派工過的座位名字）。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 做一件事\n", encoding="utf-8")
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    SessionRegistry(project).save_lineup(Lineup.of(slots))

    runner_class = scripted_cli_runner_class(list(script))
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            workflow_mode="planning",
            planning_rounds=rounds,
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
        ),
    )
    completed: list = []
    failed: list = []
    # 畫面上亮起來的那一格。跟 StepLog 同一個漏斗出來的，所以底下順手
    # 對一次——**亮著的那一格與紀錄上跑過的那一格不同**是一種很難發現的錯。
    lit: list[str] = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.active_role_changed.connect(lit.append)
    worker.run()
    QApplication.instance().processEvents()

    assert not failed, "這一輪丟了 traceback：\n" + "\n".join(
        str(f)[:400] for f in failed
    )
    orchestrator = worker.orchestrator
    assert orchestrator is not None
    dispatched = [
        step.role for step in orchestrator.step_log.read(orchestrator.run_id)
    ]
    assert lit == dispatched, (
        "陣容上亮起來的座位跟紀錄上真的跑過的不是同一批", lit, dispatched
    )
    return completed[-1], dispatched


def _seats_the_page_promises(slots) -> set[str]:
    """陣容頁畫出「這一輪：⋯」的那幾格，用它吃的那一份輸出算。"""
    sequence = Orchestrator.sequence_for(
        "planning", duties=tuple(slot.duty for slot in slots)
    )
    return {slots[int(index)].role for index in sequence["jobs"]}


def test_the_seats_the_page_promises_are_the_seats_the_engine_dispatches(
    tmp_path, monkeypatch
) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：陣容頁在「阿拍」那一格上印「這一輪：拍板」，而
    `_run_planning` 從頭到尾沒有派工給他。他會等一個不會來的拍板。

    兩邊都是算出來的，沒有一邊是抄那張表抄過來的——所以表上多一行
    沒人兌現的、或是引擎改派給別人，都會在這裡分開。
    """
    result, dispatched = _run_the_plan_sequence(
        tmp_path, monkeypatch, EVERY_DUTY, ONE_ROUND, rounds=1
    )
    assert result["workflow_status"] == "PLANNING_COMPLETED", result

    promised = _seats_the_page_promises(EVERY_DUTY)
    assert set(dispatched) == promised, (
        "陣容頁承諾的座位跟引擎真的派工的座位對不上：\n"
        f"  頁面說要做事的：{sorted(promised)}\n"
        f"  引擎真的派的：  {sorted(set(dispatched))}"
    )


def test_the_seat_he_put_on_approve_is_drawn_as_not_running(
    tmp_path, monkeypatch
) -> None:
    """核准那一格在計畫序列裡拿不到工作——**因為它真的不會跑**。

    這一條把上面那個集合比對釘在使用者看得到的那一格上：少了它，
    上面那條在「引擎開始派工給核准」的那一天也會是綠的，而那是相反的修法。

    改壞的樣子：他的第五格上印著「這一輪：拍板」，他照著等。
    """
    result, dispatched = _run_the_plan_sequence(
        tmp_path, monkeypatch, EVERY_DUTY, ONE_ROUND, rounds=1
    )
    assert result["workflow_status"] == "PLANNING_COMPLETED", result

    assert "阿拍" not in dispatched, dispatched
    assert "阿拍" not in _seats_the_page_promises(EVERY_DUTY), (
        "核准那一格被畫成有工作，而引擎一次都沒有派工給它"
    )
    # 反方向：**不是把核准整個從產品裡拿掉。** 開發序列那條路上
    # （`workflow_worker._approval_phase`）它真的會跑，那張表要留著。
    assert DUTY_APPROVE in Orchestrator.SEQUENCE_JOBS["development"]["jobs"], (
        "開發序列的核准那一種也被拿掉了——那一格在那條路上真的會被派工"
    )


def test_a_planning_seat_is_drawn_as_not_running_too(
    tmp_path, monkeypatch
) -> None:
    """擬做法那一格在計畫序列裡也不會跑，而且理由不一樣。

    核准那一格是「這條路上沒有那一關」，擬做法那一格是「這條序列本身
    就是在擬」。兩種都會落到同一個虛線圈，所以順手確認**這一次拿掉一行
    沒有把它一起弄丟**——它從 2026-08-25 起就不在那張表上了。
    """
    _result, dispatched = _run_the_plan_sequence(
        tmp_path, monkeypatch, EVERY_DUTY, ONE_ROUND, rounds=1
    )
    assert "阿想" not in dispatched, dispatched
    assert "阿想" not in _seats_the_page_promises(EVERY_DUTY)


def test_the_two_seats_that_do_run_are_the_ones_on_the_table(
    tmp_path, monkeypatch
) -> None:
    """反方向：**不准把那張表清空來換一個綠燈。**

    上面那條集合比對，兩邊都空的時候也是相等的——而那時候陣容頁上
    每一格都是虛線圈，使用者按下開始會看到五格全部「不會跑」而引擎照跑。
    所以這裡明著點名那兩格，並且要求它們**真的被派到工**。
    """
    _result, dispatched = _run_the_plan_sequence(
        tmp_path, monkeypatch, EVERY_DUTY, ONE_ROUND, rounds=1
    )
    # 擬計畫（阿查，複查那一種）跑兩次：第一版與統整版。審計畫（阿做）
    # 一次。討論回合設 1 就是這三次。
    assert dispatched == ["阿查", "阿做", "阿查"], dispatched
    assert _seats_the_page_promises(EVERY_DUTY) == {"阿查", "阿做"}
