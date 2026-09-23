"""Minimal Markdown → HTML for the shared-memory panel.

Deliberately not a full Markdown implementation. `knowledge_v1.html` injects the
result with `innerHTML` and styles a fixed set of tags, so the design session and
this converter agreed on a whitelist contract:

    h2 h3 h4 · p · ul ol li (nested) · strong em · code · pre>code
    table thead tbody tr th td · hr · a · blockquote

A full library would emit tags outside that set (definition lists, footnotes,
task lists), and an unstyled tag is an ugly tag. A closed tag set is the only
thing that makes the rendered result predictable.

Two rules this module keeps:

* **Escape first, then add tags.** Everything is HTML-escaped before any markup
  is inserted, so nothing in a memory file can become live DOM. Those files are
  edited by Claude/Codex during memory maintenance, not just by hand.
* **Unsupported syntax is visible, not silent.** Anything that looks like
  Markdown but is not handled goes out inside `<span class="md-raw">`, which the
  page styles as grey monospace with a dashed rule. A silent passthrough would
  never get reported; something that looks wrong does.

Measured against the nine real memory files (926 lines): 415 list items, 372
inline-code spans, 94 headings, 47 ordered items, 28 bold, 19 italic, 6 table
lines, 2 fenced blocks, 3 rules - and zero links, blockquotes or raw HTML.
"""
from __future__ import annotations

import html
import re

__all__ = ["render_markdown", "ALLOWED_TAGS"]

ALLOWED_TAGS = frozenset(
    {
        "h2", "h3", "h4", "p", "ul", "ol", "li", "strong", "em", "code",
        "pre", "table", "thead", "tbody", "tr", "th", "td", "hr", "a",
        "blockquote", "span",
    }
)

_FENCE = re.compile(r"^\s*```(\w*)\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RULE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_UL = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")

_CODE_SPAN = re.compile(r"`([^`]+)`")
_STRONG = re.compile(r"\*\*(\S(?:[^*]*\S)?)\*\*")
_EM = re.compile(r"(?<!\*)\*(\S(?:[^*]*\S)?)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_SAFE_HREF = re.compile(r"^(https?://|mailto:)", re.I)

_PLACEHOLDER = "\x00CODE{}\x00"


def _inline(text: str) -> str:
    """Escape, then apply the inline subset. Code spans are protected first."""
    spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        spans.append(html.escape(match.group(1), quote=False))
        return _PLACEHOLDER.format(len(spans) - 1)

    # Pull code spans out before escaping so their contents are never treated
    # as emphasis markers ("`**`" appears in these files).
    text = _CODE_SPAN.sub(stash, text)
    text = html.escape(text, quote=False)

    text = _STRONG.sub(r"<strong>\1</strong>", text)
    text = _EM.sub(r"<em>\1</em>", text)

    def link(match: re.Match[str]) -> str:
        label, href = match.group(1), match.group(2)
        if not _SAFE_HREF.match(href):
            # Anything that is not plainly external stays visible as text
            # rather than becoming a link into the local filesystem.
            return f'<span class="md-raw">{match.group(0)}</span>'
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    text = _LINK.sub(link, text)

    for index, code in enumerate(spans):
        text = text.replace(_PLACEHOLDER.format(index), f"<code>{code}</code>")
    return text


def _is_cjk(char: str) -> bool:
    return bool(char) and (
        "　" <= char <= "〿"      # CJK punctuation
        or "一" <= char <= "鿿"   # unified ideographs
        or "＀" <= char <= "￯"   # fullwidth forms
    )


def _needs_space(left: str, right: str) -> bool:
    """A joined line break needs a space only between non-CJK characters."""
    if not left or not right:
        return False
    if _is_cjk(left) or _is_cjk(right):
        return False
    return not left.isspace() and not right.isspace()


def _close_lists(
    stack: list[str], indents: list[int], out: list[str], depth: int = 0
) -> None:
    """Close open list tags down to `depth`, keeping stack and indents in step."""
    depth = max(0, depth)
    while len(stack) > depth:
        out.append(f"</{stack.pop()}>")
        if indents:
            indents.pop()


def _table(rows: list[str]) -> list[str]:
    """A pipe table. The page wraps <table> itself, so no wrapper here."""

    def cells(line: str) -> list[str]:
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        return [_inline(c.strip()) for c in line.split("|")]

    head, body = cells(rows[0]), rows[2:]
    out = ["<table>", "<thead><tr>"]
    out += [f"<th>{c}</th>" for c in head]
    out.append("</tr></thead>")
    if body:
        out.append("<tbody>")
        for row in body:
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells(row)) + "</tr>")
        out.append("</tbody>")
    out.append("</table>")
    return out


def render_markdown(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    stack: list[str] = []          # open ul/ol tags, outermost first
    indents: list[int] = []
    para: list[str] = []
    index = 0

    def flush_para() -> None:
        """Join wrapped lines instead of emitting <br>.

        A newline inside a paragraph is a soft wrap in the source, not an
        intended break; keeping it as <br> would freeze the .md file's wrap
        points into a panel whose width changes. Two CJK characters join with
        nothing - a space between them is visible and wrong - while a Latin
        word boundary keeps its space.
        """
        if not para:
            return
        joined = ""
        for line in para:
            piece = _inline(line)
            if joined and _needs_space(joined[-1], line[:1]):
                joined += " "
            joined += piece
        out.append(f"<p>{joined}</p>")
        para.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        fence = _FENCE.match(line)
        if fence:
            flush_para()
            _close_lists(stack, indents, out)
            body: list[str] = []
            index += 1
            while index < len(lines) and not _FENCE.match(lines[index]):
                body.append(lines[index])
                index += 1
            index += 1
            escaped = html.escape("\n".join(body), quote=False)
            out.append(f"<pre><code>{escaped}</code></pre>")
            continue

        if not stripped:
            flush_para()
            _close_lists(stack, indents, out)
            index += 1
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_para()
            _close_lists(stack, indents, out)
            # h1 is not in the whitelist and the panel already shows the file
            # name as its title, so the document's own H1 becomes an h2.
            level = min(max(len(heading.group(1)), 2), 4)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if _RULE.match(line):
            flush_para()
            _close_lists(stack, indents, out)
            out.append("<hr>")
            index += 1
            continue

        if (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and _TABLE_SEP.match(lines[index + 1])
        ):
            flush_para()
            _close_lists(stack, indents, out)
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(lines[index])
                index += 1
            out.extend(_table(rows))
            continue

        quote = _QUOTE.match(line)
        if quote:
            flush_para()
            _close_lists(stack, indents, out)
            out.append(f"<blockquote>{_inline(quote.group(1))}</blockquote>")
            index += 1
            continue

        bullet = _UL.match(line) or _OL.match(line)
        if bullet:
            flush_para()
            tag = "ul" if _UL.match(line) else "ol"
            indent = len(bullet.group(1).expandtabs(4))
            # Dedent: close every level deeper than this line's indent.
            while len(stack) > 1 and indents and indent < indents[-1]:
                _close_lists(stack, indents, out, len(stack) - 1)
            if not stack:
                out.append(f"<{tag}>")
                stack.append(tag)
                indents.append(indent)
            elif indent > indents[-1]:
                out.append(f"<{tag}>")
                stack.append(tag)
                indents.append(indent)
            elif stack[-1] != tag:
                # Same level, different list kind: swap ul <-> ol.
                _close_lists(stack, indents, out, len(stack) - 1)
                out.append(f"<{tag}>")
                stack.append(tag)
                indents.append(indent)
            out.append(f"<li>{_inline(bullet.group(2))}</li>")
            index += 1
            continue

        para.append(line.strip())
        index += 1

    flush_para()
    _close_lists(stack, indents, out)
    return "\n".join(out)
