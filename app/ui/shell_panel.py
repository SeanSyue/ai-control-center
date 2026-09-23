"""The application shell — renders shell.html.

The shell is the sidebar and the execution bar. It is not part of any page: the
user has to be able to start a run, see what is running, and see that a project
is waiting for a decision from *every* page, so it lives outside the
QStackedWidget that swaps the pages.

One file is loaded twice, as two views:

    shell.html?part=side   the left rail (navigation)
    shell.html?part=top    the execution bar plus the decision banner

Two views rather than one because a Qt widget is a rectangle and "left column
plus top bar" is an L. Splitting the view does not split the styling - both
halves are the same file, so there is still exactly one definition of each.

`PAGES` below is pushed to the page rather than left to the copy in shell.js.
The page's own list is its browser-preview fallback; the authoritative list is
here because this is the side that knows which panels were actually built. It
is the same rule the settings page's `gate_floor` follows: whichever side can
be wrong is the side that must not hold the copy.
"""
from __future__ import annotations

from PySide6.QtCore import Signal

from app.ui.web_panel import WebViewPanel

# key -> sidebar label. The key is what comes back as `navigate:<key>` and what
# MainWindow maps to a widget in the stack; the order is the order on screen.
#
# `live` has no counterpart in shell.js's DEFAULT_PAGES. That is the whole
# reason this list is pushed: the live log was the eighth tab on the old tab
# bar, and a sidebar built from the page's own defaults would have silently
# dropped it - reachable by nothing, with no error anywhere.
PAGES: tuple[tuple[str, str], ...] = (
    ("lineup", "page.lineup"),
    ("dashboard", "page.dashboard"),
    ("session", "page.sessions"),
    ("knowledge", "page.knowledge"),
    ("documents", "page.documents"),
    ("roadmap", "page.roadmap"),
    ("settings", "page.settings"),
    ("live", "page.log"),
    ("audit", "page.audit"),
)

# Which entries belong to something other than the everyday eight. The sidebar
# draws a divider above the first entry of a new group.
#
# Kept beside PAGES rather than folded into it: only one page has a group, and
# a third element that is empty eight times out of nine reads as noise at every
# call site. `test_shell_panel` asserts every key here exists in PAGES, so the
# two cannot drift apart.
#
# 「proof」 is the whole reason the divider exists: the other eight pages are for
# doing the work, 決策紀錄 is for proving the work was done. The design session
# could not place the line from CSS alone - it has no way to know which entry is
# the last everyday one.
PAGE_GROUPS: dict[str, str] = {"audit": "proof"}

# What the sidebar occupies. The stylesheet gives .shell-side `width:100%`, so
# the width is the widget's to decide, and this is the value the design folder's
# own verification measures the rail at (224x900).
SIDE_WIDTH = 224

# The execution bar with no decision banner. Used until the page reports its
# real height, so the bar is never zero-height on the first paint.
TOP_HEIGHT = 64


class ShellPanel(WebViewPanel):
    """One half of the shell. `part` is "side" or "top"."""

    PAGE = "shell.html"

    # (kind, arg) straight from the page. MainWindow owns what they mean -
    # they are the same actions its native buttons used to be wired to.
    action = Signal(str, str)

    def __init__(self, part: str, state_provider, parent=None) -> None:
        if part not in ("side", "top"):
            raise ValueError(f"shell part 只能是 side 或 top，收到 {part!r}")
        super().__init__(parent)
        self.part = part
        self._state_provider = state_provider
        # Shadows the class attribute. Read in _build_view, which does not run
        # until the widget is first shown, so setting it here is early enough.
        self.QUERY = f"part={part}"

        if part == "side":
            self.setFixedWidth(SIDE_WIDTH)
        else:
            self.setFixedHeight(TOP_HEIGHT)

    def collect_state(self) -> dict:
        return self._state_provider()

    def handle_action(self, kind: str, arg: str) -> None:
        # The page cannot resize its own Qt widget, so it measures itself after
        # every render and reports back. Without this the decision banner is
        # drawn inside a 64px view and the user sees its top edge only - and
        # "this project is waiting for you" is the one thing that must never be
        # clipped. The page only sends this when the height actually changes.
        if kind == "shell_height":
            try:
                height = int(arg)
            except (TypeError, ValueError):
                return
            if height > 0:
                self.setFixedHeight(height)
            return
        self.action.emit(kind, arg)
