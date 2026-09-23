"""Untrusted strings pushed to a page must not be able to run as HTML.

The panels feed conversation titles and snippets - scanned off local Claude and
Codex sessions, and pulled from imported transcripts - into pages that build
their rows with `innerHTML`. Measured before escape_state existed: a title
carrying `<img src=x onerror=...>` executed as live JavaScript on all three
list pages, and none ships a CSP strong enough to stop an inline handler.

These test the pure escaping function rather than driving QtWebEngine, both
because a live view left alive at interpreter shutdown crashes the suite
(exit 139) and because the property worth pinning is simple: every untrusted
string comes back with its HTML-significant characters neutralised, and the one
field that is meant to carry HTML does not.
"""
from __future__ import annotations

from app.ui.web_panel import escape_state

PAYLOAD = '<img src=x onerror="window.__pwned=1">'


def test_angle_brackets_in_a_value_are_escaped() -> None:
    out = escape_state({"title": PAYLOAD}, frozenset())

    assert "<img" not in out["title"]
    assert "&lt;img" in out["title"]


def test_the_single_quote_is_escaped_but_that_is_not_enough_on_its_own() -> None:
    # This test used to be called "...breaks_out_of_onclick_is_escaped", which
    # claimed a security property it does not verify. Measured afterwards: HTML
    # escaping does NOT protect a value interpolated into a JS string inside an
    # inline handler. The HTML parser decodes `&#x27;` back to `'` before the JS
    # parser sees the attribute, so
    #
    #     onclick="pick('claude','x&#x27;);alert(1);//')"
    #
    # runs as pick('claude','x');alert(1);//'). Escaping is necessary and it is
    # what makes the text and attribute-value positions safe; the inline-handler
    # position is defended by not having inline handlers, which
    # test_no_inline_handler_interpolates_data (in verify_web_pages.py's static
    # scan) is what actually guards.
    out = escape_state({"id": "c'1"}, frozenset())

    assert "'" not in out["id"]
    assert "&#x27;" in out["id"]


def test_the_double_quote_that_breaks_out_of_an_attribute_is_escaped() -> None:
    # Titles also render inside title="${c.title}"; a double quote ends the
    # attribute early.
    out = escape_state({"title": 'a" onmouseover="x'}, frozenset())

    assert '"' not in out["title"]
    assert "&quot;" in out["title"]


def test_nested_lists_and_dicts_are_walked() -> None:
    # The conversation state is {claude: [ {...}, ... ], codex: [...]}, so the
    # escaping has to reach into lists of dicts, not just top-level strings.
    state = {"claude": [{"title": PAYLOAD, "snippet": PAYLOAD}]}

    out = escape_state(state, frozenset())

    assert "<img" not in out["claude"][0]["title"]
    assert "<img" not in out["claude"][0]["snippet"]


def test_a_declared_html_field_is_left_intact() -> None:
    # The knowledge panel's rendered Markdown is already sanitised by
    # markdown_lite against a closed tag whitelist. Escaping it again would
    # print every memory file as raw HTML source.
    state = {"files": [{"html": "<h2>標題</h2>", "name": PAYLOAD}]}

    out = escape_state(state, frozenset({"html"}))

    assert out["files"][0]["html"] == "<h2>標題</h2>"
    # ...but a sibling field in the same dict is still escaped.
    assert "<img" not in out["files"][0]["name"]


def test_non_strings_pass_through_unchanged() -> None:
    state = {"count": 5, "ok": True, "ratio": 1.5, "empty": None}

    assert escape_state(state, frozenset()) == state


def test_the_knowledge_panel_declares_its_html_field() -> None:
    # The wiring, not just the function: if HTML_FIELDS were empty the rendered
    # Markdown would be double-escaped, and if any other panel declared one it
    # would be a hole. Only the knowledge panel may carry html.
    from app.ui.knowledge_web_panel import KnowledgeWebPanel
    from app.ui.conversation_web_panel import ConversationWebPanel
    from app.ui.dashboard_web_panel import DashboardWebPanel
    from app.ui.documents_web_panel import DocumentsWebPanel

    assert KnowledgeWebPanel.HTML_FIELDS == frozenset({"html"})
    for panel in (ConversationWebPanel, DashboardWebPanel, DocumentsWebPanel):
        assert panel.HTML_FIELDS == frozenset(), panel.__name__
