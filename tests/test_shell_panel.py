"""The shell's own wiring: height reporting, navigation, and the decision banner.

These cover the Qt half of the contract with the design pages. The page half is
checked by tools/verify_web_pages.py, which loads shell.html for real and
asserts it reports its height; nothing there can see whether Python acts on it.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.shell_panel import PAGES, SIDE_WIDTH, TOP_HEIGHT, ShellPanel  # noqa: E402


def _window(tmp_path: Path, monkeypatch) -> MainWindow:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    return MainWindow(project)


def test_shell_height_action_resizes_the_execution_bar() -> None:
    # The decision banner makes the bar grow from 64px to about 116. The page
    # cannot resize its own Qt widget, so it reports the number and this is
    # what has to act on it - without this the banner is drawn below the bottom
    # edge of its own view and the user sees its top edge only.
    QApplication.instance() or QApplication([])
    panel = ShellPanel("top", lambda: {})

    assert panel.height() == TOP_HEIGHT

    panel.handle_action("shell_height", "116")
    assert panel.height() == 116

    panel.handle_action("shell_height", "64")
    assert panel.height() == 64


def test_shell_height_ignores_values_that_would_collapse_the_bar() -> None:
    # A page that reports 0 (or nothing parseable) must not be able to make the
    # execution bar disappear - that would take the run controls and the
    # decision banner off screen with no way back.
    QApplication.instance() or QApplication([])
    panel = ShellPanel("top", lambda: {})
    panel.handle_action("shell_height", "116")

    for bad in ("0", "", "abc", "-40"):
        panel.handle_action("shell_height", bad)
        assert panel.height() == 116


def test_shell_height_is_not_forwarded_as_an_action() -> None:
    QApplication.instance() or QApplication([])
    panel = ShellPanel("top", lambda: {})
    seen: list[tuple[str, str]] = []
    panel.action.connect(lambda kind, arg: seen.append((kind, arg)))

    panel.handle_action("shell_height", "116")
    panel.handle_action("run", "")

    assert seen == [("run", "")]


def test_side_panel_is_the_width_the_design_measures_it_at() -> None:
    QApplication.instance() or QApplication([])
    panel = ShellPanel("side", lambda: {})
    assert panel.width() == SIDE_WIDTH


def test_navigate_action_switches_the_page(tmp_path, monkeypatch) -> None:
    window = _window(tmp_path, monkeypatch)

    for key, _label in PAGES:
        window._on_shell_action(f"navigate:{key}", "")
        assert window._current_page_key() == key
        assert window._shell_state()["shell"]["active_page"] == key

    window.close()


def test_navigate_to_a_page_that_does_not_exist_changes_nothing(
    tmp_path, monkeypatch
) -> None:
    window = _window(tmp_path, monkeypatch)
    window._on_shell_action("navigate:dashboard", "")

    window._on_shell_action("navigate:not_a_page", "")

    assert window._current_page_key() == "dashboard"
    window.close()


def test_every_sidebar_entry_has_a_page_behind_it(tmp_path, monkeypatch) -> None:
    # The sidebar is built from PAGES, so an entry with no widget would render
    # as a nav item that does nothing when clicked. Asserted as a count, not
    # just as "no unknown keys": a PAGES entry quietly dropped would otherwise
    # still pass.
    window = _window(tmp_path, monkeypatch)

    pushed = window._shell_state()["shell"]["pages"]
    assert len(pushed) == len(PAGES)
    assert [entry["key"] for entry in pushed] == [key for key, _ in PAGES]

    window.close()


def test_gate_banner_appears_while_a_project_waits(tmp_path, monkeypatch) -> None:
    window = _window(tmp_path, monkeypatch)
    session = window._focused_session
    session.pending_ask_result = {"review_question": "要覆寫這個檔案嗎？"}

    gate = window._shell_state()["shell"]["gate"]

    assert gate is not None
    assert gate["question"] == "要覆寫這個檔案嗎？"
    assert gate["project"] == window.project_path.name

    window.close()


def test_gate_later_hides_the_banner_without_ending_the_wait(
    tmp_path, monkeypatch
) -> None:
    # 「稍後」 is banner-level only. The distinction matters: 「結束等待」 is a
    # separate, confirmed action that abandons the question, and a defer that
    # quietly did that would lose the user's decision point.
    window = _window(tmp_path, monkeypatch)
    session = window._focused_session
    session.pending_ask_result = {"review_question": "要覆寫這個檔案嗎？"}

    window._on_shell_action("gate_later", "")

    assert window._shell_state()["shell"]["gate"] is None
    assert session.pending_ask_result is not None
    assert session.pending_ask_result is not None

    window.close()


def test_gate_later_on_one_project_does_not_hide_anothers_banner(
    tmp_path, monkeypatch
) -> None:
    window = _window(tmp_path, monkeypatch)
    other = tmp_path / "project_b"
    other.mkdir()
    ProjectRegistry.initialize_project(other)

    first = window.project_path
    window._focused_session.pending_ask_result = {"review_question": "A?"}
    window._on_shell_action("gate_later", "")

    window._session_for(other.resolve()).pending_ask_result = {"review_question": "B?"}
    window.switch_to_project(str(other.resolve()))

    gate = window._shell_state()["shell"]["gate"]
    assert gate is not None
    assert gate["question"] == "B?"

    window.switch_to_project(str(first))
    assert window._shell_state()["shell"]["gate"] is None

    window.close()


def test_the_wait_clock_keeps_ticking_while_a_decision_is_pending(
    tmp_path, monkeypatch
) -> None:
    # The banner renders 「已等待 X 分 Y 秒」 from ask_started_at at paint time, so
    # it only advances if something repaints. The timer used to be stopped for
    # every not-busy state, which froze the number at whatever it read when the
    # question arrived - a measurement that stops being true a second later.
    window = _window(tmp_path, monkeypatch)

    assert window.timer.isActive() is False

    window._focused_session.pending_ask_result = {"review_question": "?"}
    window._refresh_action_buttons()
    assert window.timer.isActive() is True

    window._focused_session.pending_ask_result = None
    window._refresh_action_buttons()
    assert window.timer.isActive() is False

    window.close()


def test_a_finished_run_does_not_get_repainted_as_never_run(
    tmp_path, monkeypatch
) -> None:
    # Found by driving the real app: a run failed with CODEX_ERROR and the
    # execution bar went back to 尚未執行, grey dot, no trace that anything had
    # happened. on_completed sets the final status, then on_thread_finished
    # calls _refresh_action_buttons(), which recomputes "not busy, nothing
    # pending" and overwrites it.
    #
    # Same shape as 94fd30a, which fixed exactly this for the stage text by
    # writing it into the session. set_status had no such home, so the status
    # kept being derived from scratch - and the redesign made it worse, because
    # the status is now the coloured dot rather than a small label.
    window = _window(tmp_path, monkeypatch)
    project = window.project_path
    session = window._focused_session
    session.thread = object()

    window.set_status("Codex 執行失敗", "error")
    session.thread = None
    window.on_thread_finished(project)

    run = window._shell_state()["shell"]["run"]
    assert run["state"] == "error", "跑完又失敗的執行被畫回「從沒跑過」"
    assert run["label"] == "Codex 執行失敗"

    window.close()


def test_starting_a_new_run_clears_the_previous_outcome(
    tmp_path, monkeypatch
) -> None:
    # The other half: a remembered status must not outlive the run it belonged
    # to, or the next run starts by showing the last one's failure.
    window = _window(tmp_path, monkeypatch)
    session = window._focused_session

    window.set_status("Codex 執行失敗", "error")
    session.thread = object()
    window._refresh_action_buttons()

    assert window._shell_state()["shell"]["run"]["state"] == "running"

    session.thread = None
    window._refresh_action_buttons()
    assert window._shell_state()["shell"]["run"]["state"] == "idle"

    window.close()


def test_run_state_reports_a_failure_as_a_failure(tmp_path, monkeypatch) -> None:
    # 「執行失敗」 used to be raised with the waiting-for-you style, which in the
    # shell is a gold dot meaning "this project needs an answer". A failed run
    # needs nothing from anyone.
    window = _window(tmp_path, monkeypatch)

    window.set_status("執行失敗", "error")
    run = window._shell_state()["shell"]["run"]

    assert run["state"] == "error"
    assert run["label"] == "執行失敗"

    window.close()


def test_unknown_status_kind_falls_back_instead_of_reaching_the_page(
    tmp_path, monkeypatch
) -> None:
    window = _window(tmp_path, monkeypatch)

    window.set_status("某種狀態", "not_a_state")

    assert window._shell_state()["shell"]["run"]["state"] == "idle"
    window.close()


def test_a_finished_run_is_pushed_as_done_not_idle(tmp_path, monkeypatch) -> None:
    # The execution bar paints `done` mint and `idle` grey, so this is the
    # difference between "it finished" and "it never started" - the one thing
    # the user most wants to read at a glance.
    #
    # The fallback in set_status is why this needs asserting rather than
    # eyeballing: an unrecognised kind is quietly rewritten to "idle", so
    # forgetting to whitelist "done" would not raise, it would silently put the
    # grey dot back and look exactly like the bug it fixed.
    window = _window(tmp_path, monkeypatch)

    for label in ("計畫完成", "PASS"):
        window.set_status(label, "done")
        run = window._shell_state()["shell"]["run"]
        assert run["state"] == "done", f"{label} 被降級成 {run['state']}"
        assert run["label"] == label

    window.close()


def test_the_completion_states_in_on_completed_ask_for_done() -> None:
    # 這兩種收場現在由 app/core/outcomes.py 判定，所以這裡直接問它——
    # 比字串更硬：問的是真的會畫出來的那個值。
    from app.core import outcomes

    assert (
        outcomes.describe({"workflow_status": "PLANNING_COMPLETED"}).style
        == "done"
    )
    assert outcomes.describe({"workflow_status": "COMPLETED"}).style == "done"
    assert (
        outcomes.describe({"workflow_status": "PLANNING_COMPLETED"}).label
        == "計畫完成"
    )
    assert outcomes.describe({"workflow_status": "COMPLETED"}).label == "PASS"
