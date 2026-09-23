"""One day/night mode for the whole app.

Every page ships its own toggle and each panel used to keep its own `_mode`, so
flipping one left the other five behind. These tests pin the shared behaviour,
because the symptom only shows when you switch tabs - exactly the thing nobody
does while staring at the panel they just changed.

The mode is stored in QSettings, which is the user's real store: these tests
used to set `ui/mode` in the actual registry and read it straight back, so a
second process touching that key in between turned them red for no reason (and
left the user's app on whichever theme the suite finished on). `conftest.py`
now points the whole process at a throwaway INI file, and
`test_your_real_settings_stay_yours.py` keeps it pointed there.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.web_panel import THEME, WebViewPanel  # noqa: E402


class _Panel(WebViewPanel):
    PAGE = "roadmap_v1.html"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.pushes = 0

    def collect_state(self) -> dict:
        return {"mode": self._mode}

    def push_state(self) -> None:
        # The real one is a no-op until the page loads; count the intent.
        self.pushes += 1


@pytest.fixture
def app_and_mode():
    QApplication.instance() or QApplication([])
    before = THEME.mode
    yield
    THEME.set_mode(before)
    QSettings("Sean", "AI Control Center").setValue("ui/mode", before)


def test_toggling_one_panel_moves_every_panel(app_and_mode) -> None:
    first, second = _Panel(), _Panel()
    THEME.set_mode("night")
    first.pushes = second.pushes = 0

    first._dispatch_action("mode", "day")

    assert first._mode == "day"
    assert second._mode == "day", "另一個面板沒有跟著切換"
    assert second.pushes == 1, "另一個面板沒有被通知重畫"


def test_a_panel_cannot_own_a_private_mode(app_and_mode) -> None:
    panel = _Panel()
    THEME.set_mode("night")

    with pytest.raises(AttributeError):
        panel._mode = "day"        # read-only property, by design


def test_an_unknown_mode_is_ignored_rather_than_stored(app_and_mode) -> None:
    THEME.set_mode("night")

    THEME.set_mode("rainbow")

    assert THEME.mode == "night"


def test_the_mode_is_remembered_between_runs(app_and_mode) -> None:
    # Start from the other mode so the line below is a real write. Asking for
    # the mode that is already set returns early without storing anything, and
    # then this test would be reading back a value it never wrote.
    THEME.set_mode("night")

    THEME.set_mode("day")

    stored = QSettings("Sean", "AI Control Center").value("ui/mode")

    assert str(stored) == "day"
