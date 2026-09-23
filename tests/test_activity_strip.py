"""The strip that replaced 「同時看兩個專案的即時輸出」 (ROADMAP P4).

The user was asked what they were actually trying to see when they wanted two
projects at once, and the answer was 「有沒有出事」. That answer picks a
different feature from the one that was asked for: live output scrolls away
and is worthless to somebody who was not watching, while state waits.

So these tests are mostly about what the strip **leaves out**. A strip that
lists every project is a project list, and a project list does not answer
「哪個要我」 any faster than the sidebar already does.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def make_project(root: Path, name: str) -> Path:
    project = root / name
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    return project


def window_with(tmp_path: Path, monkeypatch, names: list[str]):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    paths = [make_project(tmp_path, name) for name in names]
    window = MainWindow(paths[0])
    for path in paths:
        window._session_for(path.resolve())
    return window, [p.resolve() for p in paths]


def strip(window) -> list[dict]:
    return window._shell_state()["shell"]["activity"]


def test_a_quiet_machine_shows_no_strip_at_all(
    tmp_path: Path, monkeypatch
) -> None:
    """Empty, not a bar saying 「沒事」.

    Reporting the normal case costs a line of screen every day to say nothing.
    """
    window, _ = window_with(tmp_path, monkeypatch, ["a", "b", "c"])

    assert strip(window) == []

    window.deleteLater()


def test_only_projects_with_something_going_on_are_listed(
    tmp_path: Path, monkeypatch
) -> None:
    """The reason this stays readable at five projects and two panes do not."""
    window, paths = window_with(tmp_path, monkeypatch, ["a", "b", "c"])
    busy = window._session_for(paths[1])
    busy.pending_ask_result = {"review_question": "要不要刪掉舊檔？"}

    entries = strip(window)

    assert [entry["name"] for entry in entries] == ["b"]
    assert entries[0]["state"] == "waiting"

    window.deleteLater()


def test_a_deferred_question_stays_in_the_strip(
    tmp_path: Path, monkeypatch
) -> None:
    """稍後 means later, not resolved.

    The banner hides itself on 稍後 - that is what the button is for. If the
    strip hid it too, nothing in the app would remember, which is exactly the
    case this feature exists for.
    """
    window, paths = window_with(tmp_path, monkeypatch, ["a", "b"])
    session = window._session_for(paths[0])
    session.pending_ask_result = {"review_question": "要不要刪掉舊檔？"}
    window.defer_gate_banner()

    assert window._shell_state()["shell"]["gate"] is None, "橫幅本來就該收起來"
    assert [e["state"] for e in strip(window)] == ["waiting"]

    window.deleteLater()


def test_a_failed_run_stays_visible_until_something_replaces_it(
    tmp_path: Path, monkeypatch
) -> None:
    window, paths = window_with(tmp_path, monkeypatch, ["a", "b"])
    session = window._session_for(paths[1])
    session.last_outcome = ("Codex 執行失敗", "error")

    entries = strip(window)

    assert [e["state"] for e in entries] == ["error"]
    # The failure's own words, not the stage it happened to stop on - 「正在
    # 執行測試」 next to a red dot reads as still running.
    assert entries[0]["stage"] == "Codex 執行失敗"

    window.deleteLater()


def test_a_run_that_stopped_to_ask_for_settings_is_in_the_strip(
    tmp_path: Path, monkeypatch
) -> None:
    """The ending that used to finish a run and then disappear.

    VERIFY_NOT_CONFIGURED stops before anything is dispatched, because the
    project has never said how it should be checked. Nothing failed, so it is
    not `error`; nothing is going to come back for it, so it is not `running`;
    and there is no question to answer, so it is not `waiting`. Left out of
    all three, a project that ended this way was indistinguishable from an
    idle one - and the person this strip is for started the run and walked
    away, so the strip is the only place that was ever going to tell them.

    Driven through the real on_completed rather than by setting last_outcome
    by hand: the handler is what decides which family this ending belongs to,
    and it is the half that was wrong.
    """
    window, paths = window_with(tmp_path, monkeypatch, ["a", "b"])

    window.on_completed(
        paths[1],
        {
            "workflow_status": "VERIFY_NOT_CONFIGURED",
            "review_status": "NOT_RUN",
            "verification_problem": "這個專案沒有宣告驗證指令，請先設定：verify_command。",
        },
    )

    entries = strip(window)

    assert [e["name"] for e in entries] == ["b"], f"背景專案的結局不見了：{entries}"
    assert entries[0]["state"] == "partial"
    # One line, and it has to be the actionable half: 「尚未設定驗證」 alone
    # does not tell anybody where to go.
    assert "verify_command" in entries[0]["stage"]

    window.deleteLater()


def test_a_background_run_remembers_how_it_ended(
    tmp_path: Path, monkeypatch
) -> None:
    """last_outcome has to be written by the project that ran, not by focus.

    set_status is the only thing that used to write it, and set_status writes
    `self._focused_session` - which on_completed never reaches for a project
    the user is not looking at. So every ending that happened in the
    background left last_outcome = None, and this strip reads exactly that
    field: 「有沒有出事」 was answerable only for whichever project happened to
    be on screen when it finished, which is the one case the strip is not
    needed for.
    """
    window, paths = window_with(tmp_path, monkeypatch, ["a", "b"])

    window.on_failed(paths[1], "boom")

    assert window._session_for(paths[1]).last_outcome == ("執行失敗", "error")
    assert [(e["name"], e["state"]) for e in strip(window)] == [("b", "error")]

    window.deleteLater()


def test_a_background_ending_repaints_the_strip_without_waiting_for_a_click(
    tmp_path: Path, monkeypatch
) -> None:
    """Being in the pushed state is not the same as being on the screen.

    on_completed returns early for a project that is not focused, on the
    grounds that nothing visible changed - which stopped being true when this
    strip started drawing every project. Without a push, the strip keeps
    showing 「執行中」 for a project that ended minutes ago until the user
    happens to click something, and 「有事的時候看得到」 that needs a click
    first is the thing this strip replaced.

    Asserted through the panels, because _shell_state() alone cannot tell the
    difference: it recomputes on demand, so a test that only reads it passes
    whether or not anything was ever pushed.
    """
    window, paths = window_with(tmp_path, monkeypatch, ["a", "b"])
    pushes: list[str] = []
    for name in ("shell_side", "shell_top"):
        panel = getattr(window, name, None)
        assert panel is not None, f"外框少了 {name}，這條測試就什麼都沒守到"
        monkeypatch.setattr(
            panel, "push_state", lambda name=name: pushes.append(name)
        )

    window.on_completed(
        paths[1],
        {
            "workflow_status": "VERIFY_NOT_CONFIGURED",
            "review_status": "NOT_RUN",
            "verification_problem": "這個專案沒有宣告驗證指令，請先設定：verify_command。",
        },
    )

    assert pushes, "背景專案跑完了，畫面沒有被重畫"

    window.deleteLater()


def test_a_finished_run_is_not_an_event(tmp_path: Path, monkeypatch) -> None:
    # 「做完了」 is the outcome you wanted. Leaving it in the strip would make
    # a successful night look like a list of things to deal with.
    window, paths = window_with(tmp_path, monkeypatch, ["a", "b"])
    window._session_for(paths[1]).last_outcome = ("PASS", "done")

    assert strip(window) == []

    window.deleteLater()


def test_the_ones_that_need_a_person_come_first(
    tmp_path: Path, monkeypatch
) -> None:
    """Ordered by what it costs to miss, not by name or by start time."""
    window, paths = window_with(tmp_path, monkeypatch, ["a", "b", "c", "d"])
    window._session_for(paths[0]).last_outcome = ("Claude 執行失敗", "error")
    window._session_for(paths[1]).pending_ask_result = {"review_question": "?"}
    window._session_for(paths[2]).current_stage = "Codex 正在審查"
    window._session_for(paths[2]).thread = object()
    # 「跑完了、沒東西壞、要你看」 sits below a failure (nothing is broken) and
    # above a running project (which needs nobody).
    window._session_for(paths[3]).last_outcome = ("尚未設定驗證", "partial")

    assert [e["state"] for e in strip(window)] == [
        "waiting",
        "error",
        "partial",
        "running",
    ]

    window._session_for(paths[2]).thread = None
    window.deleteLater()


def test_each_entry_carries_the_id_the_switch_action_already_takes(
    tmp_path: Path, monkeypatch
) -> None:
    """Clicking an entry must not need a new action.

    `set:project` already takes a path string, and the shell's project list
    already uses the same id. A second identifier for the same thing is a
    second thing that can be wrong.
    """
    window, paths = window_with(tmp_path, monkeypatch, ["a", "b"])
    window._session_for(paths[1]).pending_ask_result = {"review_question": "?"}

    entry = strip(window)[0]
    window._on_shell_action("set:project", entry["id"])

    assert window.project_path == paths[1]

    window.deleteLater()


def test_the_focused_project_is_marked_rather_than_hidden(
    tmp_path: Path, monkeypatch
) -> None:
    """Whether to dim it is the page's call; whether it is on screen is a fact.

    Hidden here, a project you are looking at would vanish from the strip and
    the count would drop by one for no visible reason.
    """
    window, paths = window_with(tmp_path, monkeypatch, ["a", "b"])
    window._session_for(paths[0]).pending_ask_result = {"review_question": "?"}
    window._session_for(paths[1]).pending_ask_result = {"review_question": "?"}

    entries = {e["name"]: e["focused"] for e in strip(window)}

    assert entries == {"a": True, "b": False}

    window.deleteLater()
