"""Pixel-level guards for the Quiet Celestial stylesheet regressions.

These three bugs all shipped through a fully green suite, because every
existing test asserts state (``isEnabled()``, ``isReadOnly()``) and none
asserted what the widget actually *renders*. State was correct in all three
cases - the pixels were not. So these tests grab the widget and compare
images, which is the same evidence that exposed the bugs in the first place.

Colour comparisons survive the offscreen platform; only font metrics differ
there, and nothing here measures a size.
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QPushButton,
    QScrollBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.diagnostics_dialog import CrashDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.native_theme import PALETTES  # noqa: E402
from app.ui.native_theme import stylesheet as native_stylesheet  # noqa: E402


def _pixels(widget) -> bytes:
    return bytes(widget.grab().toImage().constBits())


def _indicator_colour(box) -> tuple[int, int, int, int]:
    """Most common colour inside a checkbox's indicator square.

    A checked indicator is mostly fill, so the dominant colour is the fill
    itself. When the box goes missing and only a bare tick is drawn, the
    dominant colour becomes the surrounding background instead - which is
    exactly the regression this distinguishes.
    """
    image = box.grab().toImage()
    middle = image.height() // 2
    counts = Counter()
    for x in range(2, 18):
        for y in range(middle - 6, middle + 7):
            counts[image.pixelColor(x, y).getRgb()] += 1
    return counts.most_common(1)[0][0]


def _background(widget) -> tuple[int, int, int, int]:
    """Colour of an empty patch: low and centre-left, clear of text, caret and
    the always-on scrollbar. Whole-image comparison is too strict here - a
    blinking text caret is enough to make two otherwise identical grabs differ.
    """
    image = widget.grab().toImage()
    return image.pixelColor(widget.width() // 2, widget.height() - 12).getRgb()


def _window(tmp_path: Path, monkeypatch) -> MainWindow:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    return MainWindow(project)


def test_a_horizontal_scrollbar_has_a_handle(tmp_path, monkeypatch) -> None:
    """Both orientations, not just :vertical.

    Adjudicating one of the redesign's six unresolved concerns (see
    design/NEXT-STEPS.md). It does not reproduce - the sheet defines
    `QScrollBar:horizontal` and its handle - but nothing was guarding that, and
    the failure mode is a groove you cannot grab with no error anywhere.

    The handle's colour is compared against the palette's own `line` value,
    looked up rather than written down. "Do the pixels change when the range
    collapses" was tried first and is useless: Qt draws its own handle when the
    stylesheet does not, so that comparison passes with the rule deleted -
    green whatever happens, which is the failure this file exists to catch.
    """
    QApplication.instance() or QApplication([])
    expected = QColor(PALETTES["night"]["line"]).getRgb()[:3]
    host = QWidget()
    host.setStyleSheet(native_stylesheet("night"))
    layout = QVBoxLayout(host)
    bars = {}
    for name, orientation in (("水平", Qt.Horizontal), ("垂直", Qt.Vertical)):
        bar = QScrollBar(orientation)
        bar.setRange(0, 100)
        bar.setValue(50)
        bar.setFixedSize(200, 200) if False else None
        layout.addWidget(bar)
        bars[name] = bar
    host.resize(400, 300)
    host.show()

    for name, bar in bars.items():
        image = bar.grab().toImage()
        middle = image.pixelColor(image.width() // 2, image.height() // 2)
        assert middle.getRgb()[:3] == expected, (
            f"{name}捲軸中央不是樣式表的滑塊顏色："
            f"實際 {middle.getRgb()[:3]}，預期 {expected}"
        )

    host.close()


def test_a_disabled_spinbox_looks_disabled(tmp_path, monkeypatch) -> None:
    """Another of the six, also not reproducing - and worth pinning down.

    The claim was that a `QWidget { color }` type selector reached QSpinBox and
    left the disabled state indistinguishable. That selector is gone (the sheet
    uses `QMainWindow, QDialog`) and there is an explicit `QSpinBox:disabled`
    rule, so the concern is answered twice over.

    The **border** is what carries the state, and measuring is how that was
    settled: in night mode `field` and `disabled_bg` are the same colour
    (#11162F), so the background cannot distinguish the two states no matter
    which rule applies. Two earlier versions of this test were thrown away for
    that - one compared enabled against disabled (Qt greys text by itself, so
    it passed with the rule deleted), the other compared the background against
    `disabled_bg` (which the enabled state also matches).
    """
    QApplication.instance() or QApplication([])
    night = PALETTES["night"]
    enabled_border = QColor(night["line"]).getRgb()[:3]
    disabled_border = QColor(night["line_soft"]).getRgb()[:3]
    assert enabled_border != disabled_border, "調色盤本身分不出來，這條測試就沒有意義"

    host = QWidget()
    host.setStyleSheet(native_stylesheet("night"))
    layout = QVBoxLayout(host)
    spin = QSpinBox()
    spin.setValue(42)
    layout.addWidget(spin)
    host.resize(240, 90)
    host.show()

    def border() -> tuple:
        image = spin.grab().toImage()
        return image.pixelColor(0, image.height() // 2).getRgb()[:3]

    spin.setEnabled(True)
    assert border() == enabled_border, f"可用時的邊框是 {border()}"
    spin.setEnabled(False)
    assert border() == disabled_border, (
        f"停用時的邊框是 {border()}，預期 {disabled_border} —— 看不出停用"
    )

    host.close()


def test_flat_tool_buttons_look_different_when_disabled(tmp_path, monkeypatch) -> None:
    # Qt scores an attribute selector and a pseudo-class the same (0x10), so
    # QPushButton[flatTool="true"] tied with QPushButton:disabled and won on
    # source order, overriding background, border and colour. Any tool button
    # that got disabled therefore stayed looking clickable, and clicking it did
    # nothing with no explanation.
    #
    # This used to walk MainWindow, whose tool buttons moved into the shell's
    # execution bar. CrashDialog's 顯示技術細節 is the remaining flatTool button,
    # and it is parented to the window so it inherits the same stylesheet the
    # original buttons were rendered under.
    #
    # A synthetic QWidget host was tried first and is not usable: its pixels
    # differ between enabled and disabled *whether or not* the :disabled rule is
    # present, so the test passed with the fix removed. It has to be a real
    # dialog under the real sheet, which is the condition the bug needed.
    window = _window(tmp_path, monkeypatch)
    dialog = CrashDialog("summary", "details", None, parent=window)
    button = dialog.toggle
    assert button.property("flatTool")

    button.setEnabled(True)
    enabled = _pixels(button)
    button.setEnabled(False)
    disabled = _pixels(button)

    assert enabled != disabled, "flatTool 按鈕在停用時看起來完全一樣"

    dialog.close()
    window.close()


def test_checkbox_indicator_survives_the_global_background_rule(
    tmp_path, monkeypatch
) -> None:
    # A QWidget type selector carrying a background also matches QCheckBox,
    # which hands CE_CheckBox to QStyleSheetStyle; with no ::indicator rule to
    # draw with, the checked state lost its filled box and rendered as a bare
    # tick floating on the panel background.
    #
    # Comparing checked against unchecked is not enough to catch that - a bare
    # tick still differs from an empty box, and the label text alone changes
    # colour between states. The assertion has to be about the indicator area
    # specifically, against a checkbox the app's stylesheet never touched.
    window = _window(tmp_path, monkeypatch)

    styled = QCheckBox("啟用", window)
    styled.setChecked(True)
    native = QCheckBox("啟用")
    native.setChecked(True)

    assert _indicator_colour(styled) == _indicator_colour(native), (
        "the checked indicator no longer renders like an unstyled one - the "
        "global background rule has started matching QCheckBox again"
    )

    native.deleteLater()
    window.close()


def test_nothing_flips_read_only_at_runtime_without_repolishing() -> None:
    """`QPlainTextEdit[readOnly="true"]` **只在 polish 的時候被評估一次**。

    所以 `setReadOnly(False)` 之後那塊灰底不會自己退掉：欄位看起來還是不能
    編輯，而它其實默默地收得下字。那是使用者會怪自己的那種 bug。

    2026-08-23 之前這條是拿 `KnowledgePanel`（唯一會在跑的時候切換唯讀的
    widget）當現場量的。那個面板砍掉了，而**全 app 沒有任何一個地方在跑的
    時候切換唯讀**——每一個 `setReadOnly` 都是建構的時候設一次 True。

    所以這條從「量顏色」改成「守那個前提」：**有人重新引進切換的那一天，
    這裡會紅，而訊息會告訴他要補什麼。**把原本那條直接刪掉的話，重新引進
    的那一天沒有任何東西會講話。
    """
    offenders = []
    for path in Path("app").rglob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        if "setReadOnly(False)" in source:
            offenders.append(str(path))
    assert not offenders, (
        "這幾個檔案在跑的時候把唯讀關掉了："
        + "、".join(offenders)
        + "。`QPlainTextEdit[readOnly=\"true\"]` 只在 polish 時評估一次，"
        "所以切換之後要自己 unpolish/polish 一次，不然欄位看起來還是不能編輯"
        "——而它其實收得下字。補上之後把這條檢查改成量顏色的版本"
        "（git 記錄裡有原本那一份）。"
    )
