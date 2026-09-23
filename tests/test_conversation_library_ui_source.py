from __future__ import annotations

from pathlib import Path

from app.ui.shell_panel import PAGES


def test_main_window_places_lineup_before_dashboard_and_session_panel() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    # The class is not pinned, the order is - the same reason as
    # test_dashboard_ui_source. This tab has since become the HTML panel, and a
    # test that spelled out ConversationLibraryPanel would have failed for the
    # swap rather than for anything being wrong with the layout.
    #
    # The attribute followed the key: it was conversation_library_panel while
    # holding a LineupWebPanel mounted at `lineup`, which is the name that made
    # the retired conversation library page look like it was still on screen.
    assert "self.lineup_panel = " in source
    assert "self.dashboard_panel = " in source
    assert "self.session_panel = SessionTimelinePanel" in source

    # Order now lives in one list instead of in the sequence of addTab calls,
    # so this reads the list. It is also the order actually drawn: the sidebar
    # renders PAGES top to bottom, whereas the order pages are added to the
    # stack is invisible.
    # `conversations` became `lineup` when the pairing stage became a line-up
    # composer (ROADMAP P10). Same page, same position, same job - deciding who
    # runs before anything runs - it just stopped assuming exactly two.
    keys = [key for key, _label in PAGES]
    assert keys.index("lineup") < keys.index("dashboard")
    assert keys.index("lineup") < keys.index("session")


def test_main_window_refreshes_session_panel_after_binding() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert "sessions_bound.connect" in source
    assert "create_project_requested.connect" in source
    assert "create_project_from_conversation_pair" in source
    assert "def _refresh_bound_sessions" in source
    assert "self.session_panel.refresh_from_disk()" in source
