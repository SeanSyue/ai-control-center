"""The two settings that only exist because something reads them.

`gate_on` and `long_task` sat unimplemented for months, and the honest reason
was recorded at the time: the Decision Gate's ASK_USER categories were a fixed
sentence in a prompt, and the milestone planner ran unconditionally, so there
was nothing for a toggle to change. A field can be added in five minutes; what
makes it a setting is that turning it off alters what the program does.

So these tests are aimed at the behaviour, not the field: the prompt text
changes, and the plan stops being written.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import prompt_builder
from app.core.orchestrator import Orchestrator
from app.services.ai_settings import (
    DEFAULT_GATE_CATEGORIES,
    gate_categories,
    AIExecutionSettings,
)


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n測試。\n", encoding="utf-8")
    return project


def _gate_prompt(gate_on) -> str:
    return prompt_builder.build_decision_gate_prompt(
        task="任務", source="claude", question="可以刪掉資料表嗎？",
        memory_context="", sensitivity="personal", gate_on=gate_on,
    )


def test_every_category_appears_when_all_are_enabled() -> None:
    prompt = _gate_prompt(DEFAULT_GATE_CATEGORIES)

    for label in gate_categories().values():
        assert label in prompt, label


def test_a_disabled_category_leaves_the_prompt() -> None:
    # The measurable half: turning off "套件安裝" has to change what Codex is
    # told, or the checkbox is decorative.
    prompt = _gate_prompt(tuple(k for k in gate_categories() if k != "install"))

    assert "套件安裝" not in prompt
    assert "資料刪除" in prompt


def test_the_non_negotiable_clauses_survive_an_empty_selection() -> None:
    # Unchecking everything must not produce a gate that approves anything.
    # Secrets, data outside the project, git history rewriting and "not enough
    # information" are the floor and are not configurable.
    prompt = _gate_prompt(())

    assert "機密資訊" in prompt
    assert "專案外資料" in prompt
    assert "Git commit/push/reset/rebase" in prompt
    assert "資料不足以判斷" in prompt


def test_the_default_matches_the_sentence_it_replaced() -> None:
    # Making these configurable must not change the behaviour of a project that
    # never touches the setting.
    assert set(DEFAULT_GATE_CATEGORIES) == set(gate_categories())
    prompt = _gate_prompt(None)
    for label in gate_categories().values():
        assert label in prompt


def test_unknown_categories_are_dropped_and_order_is_stable() -> None:
    # A settings file written by a newer build, or a page sending them in a
    # different order, must not produce a different prompt.
    a = AIExecutionSettings(gate_on=("paid", "delete", "not_a_real_category")).normalized()
    b = AIExecutionSettings(gate_on=("delete", "paid")).normalized()

    assert a.gate_on == b.gate_on
    assert "not_a_real_category" not in a.gate_on


def test_long_task_mode_off_writes_no_milestone_plan(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    Orchestrator(project, settings=AIExecutionSettings(long_task_mode=False))

    plans = list((project / ".aicc").rglob("milestones.md"))
    assert plans == [], f"關掉長任務模式後仍寫出計畫：{plans}"


def test_long_task_mode_on_writes_the_plan(tmp_path: Path) -> None:
    # The other direction, so the test above cannot pass because the plan is
    # never written at all.
    project = make_project(tmp_path)

    Orchestrator(project, settings=AIExecutionSettings(long_task_mode=True))

    assert list((project / ".aicc").rglob("milestones.md"))


def test_the_settings_survive_a_json_round_trip(tmp_path: Path) -> None:
    # gate_on is a tuple in Python and a list in JSON; without the rebuild in
    # normalized() it would come back as a list and compare unequal forever.
    from app.services.ai_settings import AISettingsStore

    store = AISettingsStore(make_project(tmp_path))
    store.save_project(
        AIExecutionSettings(gate_on=("delete", "security"), long_task_mode=False)
    )

    loaded = store.load_project()
    assert loaded.gate_on == ("delete", "security")
    assert loaded.long_task_mode is False


def test_the_floor_shown_on_screen_is_the_floor_in_the_prompt() -> None:
    """The settings page is told the four non-negotiable clauses.

    It could have hard-coded them instead, and that is exactly the failure this
    prevents: the page would claim "these always ask" while the prompt said
    something else, and the screen is the more convincing of the two. Both come
    from gate_floor(), so they cannot disagree.
    """
    from app.services.ai_settings import gate_floor

    prompt = _gate_prompt(())

    assert gate_floor(), "地板清單是空的 —— 這條測試會空轉"
    for clause in gate_floor():
        assert clause in prompt, clause


def test_the_settings_panel_sends_the_floor(tmp_path) -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.services.ai_settings import gate_floor
    from app.services.project_registry import ProjectRegistry
    from app.ui.settings_web_panel import SettingsWebPanel

    QApplication.instance() or QApplication([])
    project = tmp_path / "settings-project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)

    state = SettingsWebPanel(project).collect_state()

    sent = state["gate_floor"]
    assert [item["label"] for item in sent] == list(gate_floor())
    # Every clause carries its own sentence. Without this the page would keep
    # its own notes - half the drift, and the silent half.
    assert all(item["note"] for item in sent), sent
    # ...and the configurable eight are a separate field, so the page can draw
    # the two groups differently rather than as one undifferentiated list.
    assert "gate_options" in state
    assert not {item["label"] for item in sent} & {
        o["label"] for o in state["gate_options"]
    }


# ---- 未儲存的變更要看得見（2026-08-09 實機發現）--------------------------


def _settings_panel(tmp_path):
    from PySide6.QtWidgets import QApplication

    from app.services.project_registry import ProjectRegistry
    from app.ui.settings_web_panel import SettingsWebPanel

    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)
    return SettingsWebPanel(project)


def test_turning_a_gate_category_off_stays_off_on_screen(tmp_path) -> None:
    """The 「假互動」 the user hit: the toggle snapped straight back on.

    `_apply` records the change and then pushes state, and `collect_state` read
    the saved file - so the push handed the page the old value and the page
    repainted it. The change WAS recorded (the bar said 有未儲存的變更), which
    made it worse than a dead control: 儲存變更 would have saved something the
    screen was not showing.
    """
    panel = _settings_panel(tmp_path)
    before = list(panel.collect_state()["gate_on"])
    assert "delete" in before, "前提不成立：預設應該有「資料刪除」"

    remaining = [key for key in before if key != "delete"]
    panel.handle_action("set:gate_on", ",".join(remaining))

    after = panel.collect_state()
    assert "delete" not in after["gate_on"], "關掉之後又彈回來了"

    # 2026-08-24：那個「還沒存的編輯」狀態拿掉了（老闆拍板全部立刻落地），
    # 所以這裡改驗**它真的寫進檔案了**——比驗一個旗標強，因為旗標對了而
    # 檔案沒寫的那種壞法，畫面上看不出來。
    from app.services.ai_settings import AISettingsStore

    assert "delete" not in AISettingsStore(panel._project).load_project().gate_on


def test_there_is_nothing_left_to_revert(tmp_path) -> None:
    """**「還原」沒有東西可以還原了**——每一次改動當場就寫進檔案。

    這條原本守的是「還原要真的還原，不是只把旗標清掉」。老闆 2026-08-24
    拍板拿掉「儲存變更」那顆按鈕之後，那件事的形狀變了：

    > 一顆對一半控制項灰掉的按鈕，本身就是那個問題的第二個版本
    > ——他還是要先看那顆按鈕是不是灰的，才知道剛才那一下有沒有生效。

    **沒有跟著刪掉這條**，是因為舊版頁面還會送 `revert` 過來，而一個沒有人
    收的動作在畫面上跟「收了但沒反應」長得一模一樣。這裡驗的是：收下它，
    什麼都不改，然後把**檔案裡真正的值**推回去。
    """
    panel = _settings_panel(tmp_path)

    remaining = [k for k in panel.collect_state()["gate_on"] if k != "delete"]
    panel.handle_action("set:gate_on", ",".join(remaining))

    panel.handle_action("revert", "")

    after = panel.collect_state()
    assert "delete" not in after["gate_on"], (
        "「還原」把已經存進檔案的改動退掉了——那會讓使用者以為那一下沒生效"
    )


def test_switching_project_shows_that_project_own_settings(tmp_path) -> None:
    """切專案要看到**那個專案自己的**設定。

    這條原本守的是「另一個專案的未儲存變更不准被帶過來」。2026-08-24 之後
    沒有「未儲存的變更」這回事了，但**要守的事沒有變**：切過去看到的必須是
    那個專案檔案裡的值。

    改壞的樣子一樣糟：他在 A 專案關掉了某一格，切到 B 看到那一格也是關的，
    而 B 的檔案裡它是開的。
    """
    from app.services.project_registry import ProjectRegistry

    panel = _settings_panel(tmp_path)
    panel.handle_action("set:gate_on", "delete")

    other = tmp_path / "other"
    (other / ".aicc").mkdir(parents=True)
    (other / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    ProjectRegistry.initialize_project(other)
    panel.set_project(other)

    state = panel.collect_state()
    assert len(state["gate_on"]) > 1, "另一個專案的設定被帶過來了"
