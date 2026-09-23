"""Can the user actually get to each page, and does each page still exist.

Every other check on the redesigned pages measures one page in isolation:
tools/verify_web_pages.py loads it, wires up the channel, counts what render()
drew. All of that passes for a page nothing opens. conversation_library_v2.html
passed for the whole of ROADMAP P10 after the 陣容 page took its slot - the
panel that rendered it stopped being built, the file stayed in the build, and
the verification counted it among "全部 9 頁通過".

Three separate lists have to agree for a page to be reachable, and nothing was
comparing them:

    build_pages.PAGES        what gets copied into app/ui/web
    <panel>.PAGE             what a panel renders
    main_window._add_page    which panels are in the stack
    shell_panel.PAGES        which keys the sidebar offers

These are source-level checks on purpose. Reaching the same conclusions through
a running MainWindow means a QWebEngineView per panel, and the suite already
pays for that where it has to.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ui.shell_panel import PAGES as SIDEBAR
from tools.page_wiring import missing_pages, mounted_pages, orphan_pages, read_wiring


def test_every_built_page_is_opened_by_some_panel() -> None:
    # The one this file exists for. An orphan is invisible from every other
    # angle: it builds, it loads, it renders, and no user can reach it.
    assert orphan_pages() == [], (
        "app/ui/web 裡有沒人開得起來的頁面 —— 從 build_pages.PAGES 移除，"
        "或把它接回某個面板"
    )


def test_every_page_a_panel_renders_is_in_the_build() -> None:
    # The other direction. This one does have a symptom, but only when a user
    # clicks the sidebar entry, which can be a long way from the deletion.
    assert missing_pages() == [], "有面板指名的頁面不在 app/ui/web 裡"


def test_the_sidebar_and_the_page_stack_offer_the_same_keys() -> None:
    wiring = read_wiring()

    # An entry the sidebar draws with nothing behind it does nothing when
    # clicked; a panel with no entry cannot be navigated to at all. Neither
    # raises - _show_page returns silently on an unknown key.
    assert set(wiring.add_page) == {key for key, _label in SIDEBAR}


def test_every_page_the_code_navigates_to_exists() -> None:
    # Regression: P10 renamed the `conversations` key to `lineup` and left
    # create_project's `_show_page("conversations")` behind. _show_page looks
    # the key up in a dict and returns on a miss, so creating a project simply
    # stopped moving to the 陣容 page - no error, no log line, nothing.
    wiring = read_wiring()

    unknown = sorted(set(wiring.show_page) - set(wiring.add_page))

    assert unknown == [], f"_show_page 用了沒有註冊的頁面 key：{unknown}"


def test_the_retired_conversation_library_page_is_gone_from_every_list() -> None:
    # Retired in P10, deleted afterwards. Pinned in all three places because it
    # came back once already: build_pages kept copying it in after the panel
    # stopped rendering it, which is what made it look mounted.
    page = "conversation_library_v2.html"

    build = Path("app/ui/web/build_pages.py").read_text(encoding="utf-8")
    panel = Path("app/ui/conversation_web_panel.py").read_text(encoding="utf-8")

    assert not (Path("app/ui/web") / page).exists()
    assert f'"{page}"' not in build
    assert f'PAGE = "{page}"' not in panel
    assert page not in mounted_pages()


def test_the_conversation_panel_says_its_missing_page_is_deliberate() -> None:
    # ABSTRACT is load-bearing: WebViewPanel raises on an empty PAGE, which is
    # what catches a real panel that forgot its file. Without the flag this
    # class could not be constructed at all, and the two tests that drive it
    # (session binding, escaping) would have had to be rewritten around it.
    from app.ui.conversation_web_panel import ConversationWebPanel
    from app.ui.lineup_web_panel import LineupWebPanel

    assert ConversationWebPanel.__dict__.get("ABSTRACT") is True
    assert ConversationWebPanel.PAGE == ""

    # The subclass is a real panel with a real page, and declares no exemption
    # of its own - the check has to be the one that stops it inheriting one.
    assert "ABSTRACT" not in LineupWebPanel.__dict__
    assert LineupWebPanel.PAGE == "lineup_v1.html"


def test_a_panel_that_forgets_its_page_still_refuses_to_be_built() -> None:
    # Negative control for the flag above, in both places it can leak. If
    # ABSTRACT were read as "PAGE is optional" the panel would silently load
    # app/ui/web itself and render a directory listing; if it were inherited,
    # every future subclass of the conversation lists would get that for free.
    from PySide6.QtWidgets import QApplication

    from app.ui.conversation_web_panel import ConversationWebPanel
    from app.ui.web_panel import WebViewPanel

    QApplication.instance() or QApplication([])

    class Forgetful(WebViewPanel):
        pass

    class ForgetfulHeir(ConversationWebPanel):
        pass

    with pytest.raises(ValueError):
        Forgetful()

    with pytest.raises(ValueError):
        ForgetfulHeir()
