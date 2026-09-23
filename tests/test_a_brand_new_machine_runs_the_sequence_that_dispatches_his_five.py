r"""一台全新的機器按下開始，他排的那五格真的被派到工。

**改壞的樣子**：`workflow_mode` 的出廠值退回 `planning`——寫在
`AIExecutionSettings` 那一行，或者三張出廠表格（省額度／標準／深入）裡
任何一張自己再寫死一份都算。他那台機器兩個 `ai-settings.json` 都不存在，
所以出廠值就是生效值。

**使用者會看到什麼**：他按下開始，**一次派工都沒有發生**。計畫序列這條路
每一步都派給「複查」，而他排的五格裡一格複查都沒有——畫面收在「這條序列
要派工的那一種職責，陣容裡一格都沒有」，他排的「執行」那一格從頭到尾沒有
被叫過。而那一格正是他要看的東西。

**這裡不斷言「出廠值等於 development」**，那句話只是把那一行抄第二遍：
抄錯的那一天兩邊一起錯，而它讀起來還是綠的。這裡量的是**那條序列真的把工
派給了誰**——派工的名單從 `active_role_changed` 收，那是引擎每一次模型呼叫
唯一的漏斗（`orchestrator._run_role_prompt`）。
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
from app.services.ai_settings import (  # noqa: E402
    PROFILE_IDS,
    AIExecutionSettings,
    AISettingsStore,
)
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

# 老闆真的排的那五格（`docs/五節點測試.md` §A）。名字是他取的，職責是他從
# 下拉選的，**一格複查都沒有**——而這份陣容沒有任何一格是錯的。
HIS_FIVE = [
    AgentSlot("發想", "codex", duty=DUTY_PLAN),
    AgentSlot("規劃", "claude", duty=DUTY_PLAN),
    AgentSlot("ui設計", "codex", duty=DUTY_PLAN),
    AgentSlot("執行", "claude", duty=DUTY_WORK),
    AgentSlot("審閱", "codex", duty=DUTY_APPROVE),
]
HIS_DUTIES = tuple(slot.duty for slot in HIS_FIVE)

A_PLAN = cli_result("做法：先看一遍，再動手。")
WORK_DONE = cli_result("改完了，檔案在 app/。")
PASS_REVIEW = cli_result(
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)


def _brand_new_machine(tmp_path: Path, monkeypatch) -> Path:
    """一台什麼都沒設定過的機器上的一個新專案。

    **兩個 `ai-settings.json` 都不存在**——全域那一份（`APPDATA`）與專案
    那一份。這個前提是這一整支測試的案例本身，所以底下直接斷言它，
    而不是假設它：哪天環境變數沒有被導開，這裡會先紅，而不是讓某個
    下游斷言以一個看不懂的形狀失敗。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 做一件事\n", encoding="utf-8")
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    SessionRegistry(project).save_lineup(Lineup.of(HIS_FIVE))

    store = AISettingsStore(project)
    assert not store.global_path.is_file(), (
        f"這台機器上已經有一份全域設定（{store.global_path}）——"
        "那就不是「全新的機器」了，這一支量到的東西跟它的名字不符"
    )
    assert not (project / ".aicc" / "ai-settings.json").is_file(), (
        "這個專案已經存過設定了——出廠值不會生效，案例不成立"
    )
    return project


def _factory_settings(project: Path) -> AIExecutionSettings:
    """他按下開始的那一刻，引擎手上的那一份設定。

    **不是測試自己挑的值**：走的是產品自己那條讀檔路徑
    （專案的 → 全域的 → 出廠表格），一個欄位都不覆蓋。
    """
    return AISettingsStore(project).load_project()


def _run(project: Path, monkeypatch, settings: AIExecutionSettings, script):
    runner_class = scripted_cli_runner_class(list(script))
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(project, settings=settings)
    completed: list = []
    failed: list = []
    dispatched: list[str] = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    # 引擎每一次模型呼叫的唯一漏斗。**這就是「派工給哪幾格」。**
    worker.active_role_changed.connect(dispatched.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed, dispatched


def test_the_seat_he_set_to_execute_really_gets_dispatched(
    tmp_path, monkeypatch
) -> None:
    """**這一條是這份檔案存在的理由。**

    全新的機器、他的五格、什麼都不改按下開始——「執行」那一格要被叫到。
    """
    project = _brand_new_machine(tmp_path, monkeypatch)
    completed, failed, dispatched = _run(
        project,
        monkeypatch,
        _factory_settings(project),
        [A_PLAN, WORK_DONE, PASS_REVIEW, PASS_REVIEW, PASS_REVIEW],
    )

    assert not failed, "整輪丟了 traceback：\n" + "\n".join(
        str(f)[:400] for f in failed
    )
    assert "執行" in dispatched, (
        "他排的「執行」那一格一次都沒有被叫到。實際派工的是："
        f"{dispatched}；收場：{completed[-1] if completed else '沒有'}"
    )
    # **五格都要被叫到**，不是只有一格。少一格的那一版畫面上一樣正常——
    # 他排了五個節點，而其中幾格從頭到尾沒有開口。
    assert set(dispatched) == {slot.role for slot in HIS_FIVE}, (
        f"他排了五格，這一輪只叫到 {sorted(set(dispatched))}"
    )
    assert completed[-1]["workflow_status"] != "NO_SEAT_FOR_DUTY", (
        "這一輪一次派工都沒有發生——那正是 2026-08-26 他看到的那個結局"
    )


def test_a_seat_he_sets_to_review_is_dispatched_too(tmp_path, monkeypatch) -> None:
    """**複查那一格也在這條序列上。**

    他那五格今天一格複查都沒有，而駁回、退回指名的那一格、簽核鏈上的
    複查章全部長在複查那一步上——所以「出廠序列要換成開發序列」這件事，
    只驗到執行那一格被叫到是不夠的：他把其中一格改成「複查」的那一刻，
    那一格也要真的開口。

    計畫序列那條路上這一格拿到的是**寫計畫**，不是複查。
    """
    project = _brand_new_machine(tmp_path, monkeypatch)
    # 同一份陣容，他把「ui設計」那一格的職責改成複查。
    with_reviewer = [
        replace(slot, duty=DUTY_REVIEW) if slot.role == "ui設計" else slot
        for slot in HIS_FIVE
    ]
    SessionRegistry(project).save_lineup(Lineup.of(with_reviewer))

    completed, failed, dispatched = _run(
        project,
        monkeypatch,
        _factory_settings(project),
        [A_PLAN, WORK_DONE, PASS_REVIEW, PASS_REVIEW, PASS_REVIEW, PASS_REVIEW],
    )

    assert not failed, "整輪丟了 traceback：\n" + "\n".join(
        str(f)[:400] for f in failed
    )
    assert {"執行", "ui設計"} <= set(dispatched), (
        f"執行與複查那兩格沒有都被叫到，實際派工的是：{dispatched}"
    )
    assert completed[-1]["review_status"] != "NOT_RUN", completed[-1]


def test_nothing_is_left_undispatched_the_way_the_planning_sequence_leaves_it(
    tmp_path, monkeypatch
) -> None:
    """**同樣的五格，換成計畫序列，一次派工都沒有。**

    這一條是上面那一條的對照組：它證明上面量到的差別真的來自
    `workflow_mode`，不是來自陣容、任務或別的東西。少了它，把出廠值改回
    `planning` 之後只有一條會紅，而「為什麼」沒有人講得出來。
    """
    project = _brand_new_machine(tmp_path, monkeypatch)
    factory = _factory_settings(project)
    completed, failed, dispatched = _run(
        project,
        monkeypatch,
        # 他自己去設定頁把序列切成計畫序列——**那條路還在，而且照舊**。
        AIExecutionSettings(**{**factory.to_dict(), "workflow_mode": "planning"}),
        [A_PLAN],
    )

    assert not failed, "整輪丟了 traceback：\n" + "\n".join(
        str(f)[:400] for f in failed
    )
    assert dispatched == [], (
        f"計畫序列這條路上竟然有人被派到工：{dispatched}"
    )
    assert completed[-1]["workflow_status"] == "NO_SEAT_FOR_DUTY", completed[-1]


def test_the_three_factory_tables_do_not_drift_from_the_dataclass() -> None:
    """三張出廠表格不准各寫一份序列。

    這一輪的病根就是它們各寫一份：出廠值改在 `AIExecutionSettings` 上，
    而使用者**挑一次深淺就換回去了**——省額度／標準／深入三張表格各自
    寫死一份 `workflow_mode="planning"`，畫面上完全正常。

    **右邊不寫死 `"development"`**：寫死的話，這條測的是「兩邊都抄對了
    同一個字」，而下一次換出廠序列的人要改四個地方——而那正是這條要防的事。
    """
    factory = AIExecutionSettings().workflow_mode
    for name in PROFILE_IDS:
        assert AISettingsStore.profile(name).workflow_mode == factory, (
            f"「{name}」那張出廠表格自己寫了一份序列（"
            f"{AISettingsStore.profile(name).workflow_mode!r}），"
            f"跟出廠值（{factory!r}）不一樣——使用者挑一次深淺就換掉了"
        )
    # 舊設定檔裡存的中文值也走同一張表，不然它們會落到別的地方去。
    for legacy in ("省額度", "標準", "深入", "自訂"):
        assert AISettingsStore.profile(legacy).workflow_mode == factory, legacy


@pytest.mark.parametrize("profile_name", PROFILE_IDS)
def test_picking_a_depth_table_does_not_change_which_seats_run(
    profile_name: str,
) -> None:
    """挑深淺換的是花多少，不是跑哪一條序列。

    上面那一條比的是字串；這一條比的是**他在陣容頁上看到的東西**：
    挑完任何一張表格，他那五格每一格都還是有工作，「執行」那一格拿到的
    還是動手那一份。
    """
    settings = AISettingsStore.profile(profile_name)
    sequence = Orchestrator.sequence_for(
        settings.workflow_mode, duties=HIS_DUTIES
    )

    assert sequence["executed_slots"] == len(HIS_FIVE), (
        f"挑了「{profile_name}」之後，他的五格裡有"
        f"{len(HIS_FIVE) - sequence['executed_slots']}格這一輪不會被派工："
        f"{sequence}"
    )
    executing_seat = str(HIS_DUTIES.index(DUTY_WORK))
    reviewing = Orchestrator.SEQUENCE_JOBS[settings.workflow_mode]["jobs"]
    assert DUTY_REVIEW in reviewing, (
        f"挑了「{profile_name}」之後，排成「複查」的那一格不會被派工——"
        "而駁回那條路就長在複查上"
    )
    assert sequence["jobs"][executing_seat], sequence


def test_a_project_that_already_chose_the_planning_sequence_keeps_it(
    tmp_path, monkeypatch
) -> None:
    """**已經存過設定的專案不受影響。**

    換出廠值只換「沒有人選過的時候用哪一個」。他去年在某個專案上挑了計畫
    序列，那個專案就該還是計畫序列——讀檔那條路要蓋得過出廠值，不然這一次
    改動會**安靜地改掉每一個既有專案**，而畫面上什麼都看不出來。
    """
    project = _brand_new_machine(tmp_path, monkeypatch)
    store = AISettingsStore(project)
    chosen = AIExecutionSettings(
        **{**AIExecutionSettings().to_dict(), "workflow_mode": "planning"}
    )
    store.save_project(chosen)

    assert store.load_project().workflow_mode == "planning", (
        "他選過的序列被出廠值蓋掉了"
    )
    # 全域那一份也一樣：一台設定過的機器上開的新專案，跟的是他的選擇。
    fresh = tmp_path / "another"
    (fresh / ".aicc").mkdir(parents=True)
    other_store = AISettingsStore(fresh)
    other_store.save_global(chosen)
    assert other_store.load_project().workflow_mode == "planning", (
        "他在全域設定裡選過的序列，新專案沒有跟到"
    )


def test_the_development_sequence_never_reads_the_planning_rounds(
    tmp_path, monkeypatch
) -> None:
    """出廠的 `planning_rounds` 是 0，而開發序列**一次都不看它**。

    這一條回答的是這次改動要想清楚的那一題：0 在開發序列上是不是一個錯的
    值。答案是它根本不是這條序列的欄位——把它調到 3，跑出來的東西**一模
    一樣**（同樣的幾格、同樣的次數）。所以這次不動它。

    有一天它變成開發序列也讀的東西，這一條會紅，而那正是要停下來講清楚
    「0 代表幾輪」的時刻。
    """
    project = _brand_new_machine(tmp_path, monkeypatch)
    factory = _factory_settings(project)
    assert factory.planning_rounds == 0, factory

    script = [A_PLAN, WORK_DONE, PASS_REVIEW, PASS_REVIEW, PASS_REVIEW]
    _, _, with_zero = _run(project, monkeypatch, factory, script)

    project2 = _brand_new_machine(tmp_path / "second", monkeypatch)
    talkative = AIExecutionSettings(
        **{**factory.to_dict(), "planning_rounds": 3}
    )
    _, _, with_three = _run(project2, monkeypatch, talkative, script)

    assert with_zero == with_three, (
        "把討論回合從 0 調到 3 改變了開發序列跑出來的東西——"
        f"0：{with_zero}／3：{with_three}。那表示這個欄位在這條序列上"
        "有意義了，而出廠的 0 要重新講清楚代表幾輪"
    )
