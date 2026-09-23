"""The native chrome has to switch modes with the panels it frames.

Night mode used to stop at the window's edge: the HTML panels repainted, the Qt
frame around them did not. Fixing that meant giving one stylesheet two
palettes, and the risk of that shape is silent asymmetry - a rule that gets a
dark colour in one mode and keeps a light one in the other, which nobody sees
until they open that particular panel at night.

So the structural tests below are the point, not decoration: they assert that
neither palette can drift from the other, and that no colour can be written
past the palette. The last test then checks the actual pixels, because a
correct stylesheet that never reaches the widgets would pass everything above.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.native_theme import DAY, NIGHT, PALETTES, stylesheet  # noqa: E402
from app.ui.web_panel import THEME  # noqa: E402

TEMPLATE_SOURCE = (
    Path(__file__).resolve().parents[1] / "app" / "ui" / "native_theme.py"
).read_text(encoding="utf-8")

# The QSS lives between Template(""" and the closing """), so the role scan
# below reads the rules and not the two palette dicts above them.
QSS = TEMPLATE_SOURCE.split('Template(\n    """', 1)[1].split('"""\n)', 1)[0]

# Comments quote the colours they explain - "kept Qt's default #EFEFEF" is the
# reason a rule exists, not a rule. Scanning them as if they were declarations
# turns every explanation into a failure.
RULES_ONLY = re.sub(r"/\*.*?\*/", "", QSS, flags=re.S)


def test_the_two_palettes_define_exactly_the_same_roles() -> None:
    # This is the asymmetry guard. Adding a role to one palette and forgetting
    # the other is the natural mistake, and its symptom is a KeyError at
    # startup in whichever mode was forgotten - possibly not the one you
    # happened to be testing in.
    assert DAY.keys() == NIGHT.keys()


def test_every_role_the_rules_use_exists_in_both_palettes() -> None:
    used = set(re.findall(r"\$([a-z_]+)", RULES_ONLY))

    assert used, "沒有從樣式表抓到任何 $role —— 這個測試的解析方式失效了"
    assert used <= DAY.keys(), f"樣式表用了不存在的 role: {sorted(used - DAY.keys())}"


def test_no_role_is_defined_but_unused() -> None:
    # A palette entry nothing reads looks like a knob you can turn. Turning it
    # would change nothing, and finding that out costs an afternoon.
    used = set(re.findall(r"\$([a-z_]+)", RULES_ONLY))

    assert not (DAY.keys() - used), f"沒有任何規則用到: {sorted(DAY.keys() - used)}"


def test_no_colour_is_written_inline_in_the_rules() -> None:
    # Every colour must come from the palette, or it silently keeps its light
    # value at night. This is how the two splitter handles stayed grey.
    inline = re.findall(r"#[0-9A-Fa-f]{6}(?![0-9A-Za-z_])", RULES_ONLY)

    assert not inline, f"樣式表裡直接寫死了顏色: {sorted(set(inline))}"


def test_an_unknown_mode_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError):
        stylesheet("dusk")


def test_both_modes_render_without_leaving_a_placeholder_behind() -> None:
    for mode in PALETTES:
        rendered = stylesheet(mode)
        assert "$" not in rendered, f"{mode} 有沒被取代的 $role"
        assert rendered.count("{") == rendered.count("}")


def _page_background(window) -> tuple[int, int, int]:
    """The gap beside the cards, which is the only place the page shows.

    x=4 is inside the content widget's 24px left margin: past the window edge,
    short of the first card. Sampling the window's own background instead would
    measure nothing - the scroll area covers it completely.
    """
    return window.grab().toImage().pixelColor(4, 300).getRgb()[:3]


def test_switching_mode_repaints_the_native_window(tmp_path, monkeypatch) -> None:
    """The frame, not just the panels.

    The regression being guarded is dark HTML panels mounted in a light Qt
    window, so this has to measure the chrome around the panels. Both absolute
    values are asserted, not merely that they differ: "it changed" would still
    pass if night went to some colour that is not the design's.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)

    before = THEME.mode
    try:
        THEME.set_mode("day")
        window = MainWindow(project)
        window.resize(900, 600)
        window.show()
        assert _page_background(window) == (0xF4, 0xF7, 0xFB), "白天底色不是 #F4F7FB"

        # The window is already built: this asserts the running window repaints,
        # not merely that a freshly constructed one would pick the right sheet.
        THEME.set_mode("night")
        assert _page_background(window) == (0x0A, 0x0E, 0x24), (
            "夜間底色不是 aicc.css 的 --bg #0A0E24 —— 原生外框沒有跟著換"
        )

        THEME.set_mode("day")
        assert _page_background(window) == (0xF4, 0xF7, 0xFB), "切回白天沒有還原"
        window.close()
    finally:
        THEME.set_mode(before)
