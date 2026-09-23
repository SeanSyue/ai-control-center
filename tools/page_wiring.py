"""Which pages the running app can actually put on screen.

verify_web_pages.py measures one page at a time: does this file load, wire up
its channel, render. A page can pass every one of those and still be
unreachable, because reachability is not a property of the page - it is whether
MainWindow builds a panel that names it. conversation_library_v2.html passed
for the whole of ROADMAP P10 with nothing left to open it, and the tool
reported it inside "全部 9 頁通過".

main_window.py is read rather than imported: importing it builds the whole
application, and the three facts needed are all syntax.

    from app.ui.lineup_web_panel import LineupWebPanel   where a class lives
    self.lineup_panel = LineupWebPanel(...)              what gets built
    self._add_page("lineup", self.lineup_panel)          what the stack holds

The PAGE constant is the one thing not parsed. It comes from importing the
panel's own module and reading the attribute, so a panel that inherits its page
from a base class still resolves - parsing would have to re-implement the MRO
to get that right, and would get it right only until someone changed it.

Two shapes count as mounted, because two shapes are on screen:

* a panel handed to `_add_page`, reachable from the sidebar; and
* a panel held in an attribute without being in the page stack, which is what
  the shell is - `shell.html` is always visible and belongs to no key.

Neither `session` nor `live` resolves to a page, and that is correct: they are
native Qt widgets. A key with no PAGE is not an orphan, it is a page-less panel.
"""
from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "app" / "ui" / "main_window.py"
WEB_DIR = ROOT / "app" / "ui" / "web"


@dataclass(frozen=True)
class Wiring:
    """Everything read out of main_window.py, before anything is resolved."""

    imports: dict[str, tuple[str, str]]   # local name -> (module, real name)
    built: dict[str, str]                 # self.<attr> -> class constructed
    add_page: dict[str, str]              # page key -> self.<attr> it holds
    show_page: tuple[str, ...]            # every literal key _show_page is given


def _self_attr(node: ast.AST) -> str | None:
    """`self.foo` -> "foo", anything else -> None."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return node.attr
    return None


def _constructed(node: ast.AST) -> str | None:
    """`Foo(...)` -> "Foo". Only a bare name; `mod.Foo()` is not used here."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def read_wiring(path: Path | None = None) -> Wiring:
    tree = ast.parse((path or MAIN_WINDOW).read_text(encoding="utf-8"))

    imports: dict[str, tuple[str, str]] = {}
    built: dict[str, str] = {}
    add_page: dict[str, str] = {}
    show_page: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                # Keyed by the local name, valued with the real one, so
                # `PAGES as SHELL_PAGES` still resolves if it is ever called.
                imports[alias.asname or alias.name] = (node.module, alias.name)

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                attribute = _self_attr(target)
                class_name = _constructed(node.value)
                if attribute and class_name:
                    built[attribute] = class_name

        elif isinstance(node, ast.Call):
            method = _self_attr(node.func)
            if method == "_add_page" and len(node.args) == 2:
                key = _literal(node.args[0])
                if key is not None:
                    # "" for a local widget rather than an attribute, which is
                    # what `live` is - it holds no page either way.
                    add_page[key] = _self_attr(node.args[1]) or ""
            elif method == "_show_page" and node.args:
                key = _literal(node.args[0])
                # The sidebar's own dispatch passes `kind.split(...)`, not a
                # literal; that path is covered by comparing the keys against
                # shell_panel.PAGES instead.
                if key is not None:
                    show_page.append(key)

    return Wiring(imports, built, add_page, tuple(show_page))


def _page_of(wiring: Wiring, class_name: str) -> str:
    """The PAGE a constructed class renders, "" if it is not a web panel.

    Import failures are not swallowed. Every module here is one main_window
    imports at module scope, so a module that cannot be imported is a broken
    application, not a gap in this reader - and a caught exception would
    silently turn a real panel into an orphan report.
    """
    found = wiring.imports.get(class_name)
    if found is None:
        return ""
    module_name, real_name = found
    panel = getattr(importlib.import_module(module_name), real_name, None)
    return str(getattr(panel, "PAGE", "") or "")


def mounted_pages(wiring: Wiring | None = None) -> dict[str, str]:
    """`{"lineup_v1.html": "lineup"}` - page file -> how the app opens it.

    The value is the page key when the panel is in the stack, and `self.<attr>`
    when it is held outside it (the shell). Both are on screen; only the first
    is something the sidebar can navigate to.
    """
    wiring = wiring or read_wiring()
    key_of = {attr: key for key, attr in wiring.add_page.items() if attr}

    pages: dict[str, str] = {}
    for attribute, class_name in wiring.built.items():
        page = _page_of(wiring, class_name)
        if page:
            pages.setdefault(page, key_of.get(attribute, f"self.{attribute}"))
    return pages


def orphan_pages(web_dir: Path | None = None) -> list[str]:
    """Built pages no panel opens. Empty is the only healthy answer."""
    mounted = set(mounted_pages())
    return sorted(
        path.name
        for path in (web_dir or WEB_DIR).glob("*.html")
        if path.name not in mounted
    )


def missing_pages(web_dir: Path | None = None) -> list[str]:
    """Pages a panel names that are not in the build - the other direction.

    A panel whose file was deleted fails at the moment the user opens it, which
    can be a long way from the commit that deleted it.
    """
    directory = web_dir or WEB_DIR
    return sorted(page for page in mounted_pages() if not (directory / page).is_file())
