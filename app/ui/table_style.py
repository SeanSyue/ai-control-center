"""Shared table metrics for the Quiet Celestial redesign.

Every table in the app hit the same Qt trap when the redesign added padding
to ``QTableView::item`` and ``QHeaderView::section``:
``resizeColumnsToContents()`` measures the *text*, not the stylesheet box
around it, so each column came out exactly the padding too narrow and clipped
its own contents. On the Dashboard the 狀態 column vanished completely.

Qt offers no "resize to contents including stylesheet padding", so the
compensation has to be explicit. It lives here rather than being pasted into
each panel so the number cannot drift out of step with the stylesheet.
"""
from __future__ import annotations

from PySide6.QtWidgets import QTableWidget

# Measured 2026-08-08 on the real Windows platform: `resizeColumnsToContents()`
# already includes the stylesheet padding, so no compensation is needed.
#
# With this at 0, every column still comes out at least as wide as its longest
# cell text plus the 12px padding on each side - two columns landed at exactly
# 80px for 56px of text, i.e. text + 24. The header pads identically
# (`QHeaderView::section { padding:10px 12px }`) and uses a smaller font, so it
# rarely dominates.
#
# IMPLEMENTATION-NOTES.md attributes a blank 狀態 column to missing padding
# compensation. That diagnosis does not survive measurement; the blanking was
# far more likely the `setStretchLastSection` write-back bug fixed in
# `fit_columns()` below, which made column widths drift on every refresh.
# `tests/test_table_style.py` now asserts the property that actually matters -
# columns fit their content plus the padding - so if the symptom ever returns
# it fails a test instead of needing someone to notice it on screen.
CELL_PADDING_COMPENSATION = 0

# The redesign specifies 44px rows. Qt derives row height from the font
# otherwise, which is noticeably tighter.
ROW_HEIGHT = 44


def style_table(table: QTableWidget) -> None:
    """Apply the redesign's row metrics.

    Alternating colours have to be switched on here: the stylesheet sets
    ``alternate-background-color`` but Qt ignores it unless the view is
    actually told to alternate.
    """
    table.setAlternatingRowColors(True)
    table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)


def fit_columns(table: QTableWidget) -> None:
    """Size columns to their contents, allowing for stylesheet padding.

    ``setStretchLastSection(True)`` - which all three of the app's tables use -
    makes this trickier than it looks. Writing to the last section with
    ``setColumnWidth()`` while the stretch is active stores that value as the
    section's own size, and ``resizeColumnsToContents()`` then stops deriving
    it from the contents. The compensation below would therefore be added on
    top of the previous *compensated* width on every call, growing the last
    column by ``CELL_PADDING_COMPENSATION`` per refresh with no upper bound:
    measured 798px -> 2598px over 60 refreshes, which is reachable just by
    flipping a dashboard filter back and forth at the default window size.

    Switching the stretch off for the duration keeps the last section derived
    from its contents like every other column, which makes this function
    idempotent. Compensating the last column too (rather than skipping it)
    matters when the table is too narrow to stretch at all - there the stretch
    contributes nothing and the column would otherwise clip its contents by
    the padding amount.
    """
    header = table.horizontalHeader()
    stretch_last = header.stretchLastSection()
    if stretch_last:
        header.setStretchLastSection(False)
    try:
        table.resizeColumnsToContents()
        for column in range(table.columnCount()):
            table.setColumnWidth(
                column, table.columnWidth(column) + CELL_PADDING_COMPENSATION
            )
    finally:
        if stretch_last:
            header.setStretchLastSection(True)
