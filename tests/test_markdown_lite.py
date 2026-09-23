"""The Markdown subset behind the shared-memory panel.

The important property is not "renders Markdown" but "renders only the tags the
page has styles for, and nothing in a memory file can become live DOM" -
knowledge_v1.html injects the result with innerHTML and has no escaping of its
own. The real memory files are used as the corpus, because a hand-written
sample only ever exercises the assumptions of whoever wrote it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.markdown_lite import ALLOWED_TAGS, render_markdown

# The private memory folder is not part of this repository, so the real-file
# cases run against docs/ instead: same job, markdown that actually ships.
KNOWLEDGE = Path(__file__).resolve().parents[1] / "docs"
TAG = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)")


def tags_in(html: str) -> set[str]:
    return {t.lower() for t in TAG.findall(html)}


def test_nothing_in_a_memory_file_can_become_live_dom() -> None:
    rendered = render_markdown(
        "文字 <script>alert(1)</script> 與 <img src=x onerror=alert(2)>"
    )

    assert "<script" not in rendered
    assert "onerror" not in rendered.replace("&lt;img src=x onerror=alert(2)&gt;", "")
    assert "&lt;script&gt;" in rendered


def test_code_span_contents_are_not_treated_as_emphasis() -> None:
    # These files contain things like `**` and `*args` inside backticks.
    rendered = render_markdown("設定 `**kwargs` 與 `*args`")

    assert "<strong>" not in rendered
    assert "<em>" not in rendered
    assert rendered.count("<code>") == 2


def test_headings_never_emit_h1() -> None:
    # h1 is outside the whitelist; the panel already shows the file name.
    rendered = render_markdown("# 標題\n\n## 次標題\n\n##### 很深的標題")

    assert "<h1>" not in rendered
    assert rendered.count("<h2>") == 2       # h1 folded to h2
    assert "<h4>" in rendered                # h5 clamped to h4


def test_nested_lists_close_cleanly() -> None:
    rendered = render_markdown("- 一\n  - 一之一\n  - 一之二\n- 二\n")

    assert rendered.count("<ul>") == rendered.count("</ul>") == 2
    assert rendered.count("<li>") == 4


def test_a_wrapped_paragraph_joins_instead_of_breaking() -> None:
    # A newline inside a paragraph is a soft wrap in the source, not an
    # intended break, and <br> is not in the whitelist.
    cjk = render_markdown("這是一個很長的句子\n續接下一行")
    latin = render_markdown("an english line\nwrapped here")

    assert "<br>" not in cjk and "<br>" not in latin
    assert "句子續接" in cjk                  # no stray space between CJK
    assert "line wrapped" in latin            # but a Latin word boundary keeps one


def test_a_local_looking_link_is_shown_rather_than_linked() -> None:
    rendered = render_markdown("見 [設定檔](C:\\Users\\secret\\notes.md)")

    assert "<a " not in rendered
    assert 'class="md-raw"' in rendered


def test_pipe_table_becomes_a_table_without_a_wrapper() -> None:
    # The page's wrapTables() adds .tablewrap itself.
    rendered = render_markdown("| 欄一 | 欄二 |\n|---|---|\n| a | b |\n")

    assert "<table>" in rendered and "tablewrap" not in rendered
    assert rendered.count("<th>") == 2
    assert rendered.count("<td>") == 2


@pytest.mark.parametrize(
    "path", sorted(KNOWLEDGE.glob("*.md")), ids=lambda p: p.name
)
def test_real_memory_files_only_emit_whitelisted_tags(path: Path) -> None:
    rendered = render_markdown(path.read_text(encoding="utf-8"))

    assert rendered.strip(), f"{path.name} 轉出空白"
    assert tags_in(rendered) <= ALLOWED_TAGS, (
        f"{path.name} 吐出白名單外的標籤：{tags_in(rendered) - ALLOWED_TAGS}"
    )


@pytest.mark.parametrize(
    "path", sorted(KNOWLEDGE.glob("*.md")), ids=lambda p: p.name
)
def test_real_memory_files_balance_their_list_tags(path: Path) -> None:
    rendered = render_markdown(path.read_text(encoding="utf-8"))

    for tag in ("ul", "ol", "li", "table", "pre"):
        assert rendered.count(f"<{tag}>") == rendered.count(f"</{tag}>"), (
            f"{path.name} 的 <{tag}> 沒有配對"
        )
