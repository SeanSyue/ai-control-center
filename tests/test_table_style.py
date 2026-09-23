from __future__ import annotations

from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

from app.ui.table_style import (
    CELL_PADDING_COMPENSATION,
    ROW_HEIGHT,
    fit_columns,
    style_table,
)


def _table(stretch_last: bool = False) -> QTableWidget:
    QApplication.instance() or QApplication([])
    table = QTableWidget(2, 3)
    table.setHorizontalHeaderLabels(["狀態", "專案", "最近任務"])
    for row in range(2):
        for column, text in enumerate(["待命", "demo_project", "尚未執行"]):
            table.setItem(row, column, QTableWidgetItem(text))
    table.horizontalHeader().setStretchLastSection(stretch_last)
    return table


CELL_PADDING = 12      # QSS: QTableView::item { padding: 12px }


def _styled_table(tmp_path, monkeypatch):
    """A table living inside a real MainWindow, so the app stylesheet applies.

    The padding this test is about comes from `apply_styles()`; a bare
    QTableWidget has none, and asserting against it would be measuring nothing.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    from app.services.project_registry import ProjectRegistry
    from app.ui.main_window import MainWindow

    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    window = MainWindow(project)

    table = QTableWidget(2, 3, window)
    table.setHorizontalHeaderLabels(["狀態", "專案", "最近任務"])
    for row in range(2):
        for column, text in enumerate(["待命", "demo_project", "尚未執行"]):
            table.setItem(row, column, QTableWidgetItem(text))
    table.horizontalHeader().setStretchLastSection(True)
    table.show()
    return window, table


def test_every_column_fits_its_content_plus_the_stylesheet_padding(
    tmp_path, monkeypatch
) -> None:
    # This is the property that matters, and it replaces an assertion that the
    # widths equal "raw + CELL_PADDING_COMPENSATION". That older test could only
    # ever restate the constant, so it stayed green whatever the constant was -
    # including when measurement showed the compensation was unnecessary and it
    # went to 0. A blank/clipped column is what a reader would notice, so that
    # is what is asserted.
    window, table = _styled_table(tmp_path, monkeypatch)
    fit_columns(table)
    metrics = table.fontMetrics()

    # The last column stretches, so it is not part of the fit question.
    for column in range(table.columnCount() - 1):
        widest = max(
            metrics.horizontalAdvance(table.item(row, column).text())
            for row in range(table.rowCount())
        )
        header = metrics.horizontalAdvance(
            table.horizontalHeaderItem(column).text()
        )
        needed = max(widest, header) + CELL_PADDING * 2
        assert table.columnWidth(column) >= needed, (
            f"第 {column} 欄寬 {table.columnWidth(column)} 小於需要的 {needed}，內容會被切掉"
        )

    window.close()


def test_fit_columns_is_idempotent_when_the_last_section_stretches() -> None:
    # The regression this guards: every table in the app calls
    # setStretchLastSection(True), and under that mode setColumnWidth() on the
    # last section writes back into the section's stored size, so
    # resizeColumnsToContents() stops recomputing it. The padding compensation
    # then stacked on itself once per refresh, growing the last column without
    # bound (measured 798px -> 2598px over 60 refreshes) while the other
    # columns stayed put. The test above missed it purely because its table
    # left stretchLastSection off - i.e. it never used the real configuration.
    table = _table(stretch_last=True)

    fit_columns(table)
    after_one = [table.columnWidth(c) for c in range(table.columnCount())]

    for _ in range(9):
        fit_columns(table)
    after_ten = [table.columnWidth(c) for c in range(table.columnCount())]

    assert after_ten == after_one


def test_fit_columns_leaves_the_stretch_setting_as_it_found_it() -> None:
    # The fix works by switching the stretch off for the duration; leaking that
    # off state would silently change the layout of all three tables.
    stretched = _table(stretch_last=True)
    plain = _table(stretch_last=False)

    fit_columns(stretched)
    fit_columns(plain)

    assert stretched.horizontalHeader().stretchLastSection() is True
    assert plain.horizontalHeader().stretchLastSection() is False


def test_style_table_enables_alternating_rows_and_the_designed_row_height() -> None:
    # alternate-background-color in the stylesheet does nothing unless the
    # view is told to alternate, and Qt derives row height from the font
    # unless given an explicit default.
    table = _table()

    assert table.alternatingRowColors() is False

    style_table(table)

    assert table.alternatingRowColors() is True
    assert table.verticalHeader().defaultSectionSize() == ROW_HEIGHT
