from __future__ import annotations

from app.i18n import t

from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.conversation_search import (
    ConversationHit,
    ConversationSearchService,
)
from app.services.session_binding import bind_hits, suggest_project_name
from app.services.session_registry import SessionRegistry
from app.ui.table_style import fit_columns, style_table


class ConversationLibraryPanel(QWidget):
    """Search local Claude/Codex conversations and bind them to a project."""

    sessions_bound = Signal()
    create_project_requested = Signal(str)

    def __init__(
        self,
        project_path: Path,
        search_service: ConversationSearchService | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_path = project_path.expanduser().resolve()
        self.registry = SessionRegistry(self.project_path)
        self.search_service = search_service or ConversationSearchService()
        self.claude_hits: list[ConversationHit] = []
        self.codex_hits: list[ConversationHit] = []
        self.selected_claude: ConversationHit | None = None
        self.selected_codex: ConversationHit | None = None
        self._build_ui()

    def set_project_path(self, project_path: Path) -> None:
        self.project_path = project_path.expanduser().resolve()
        self.registry = SessionRegistry(self.project_path)
        self.selected_claude = None
        self.selected_codex = None
        self.claude_hits = []
        self.codex_hits = []
        self._render_tables()
        self._update_selection_summary()
        self.claude_preview.setPlainText(t("library.pick_claude_first"))
        self.codex_preview.setPlainText(t("library.pick_codex_first"))

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        title = QLabel(t("library.title"))
        title.setObjectName("sectionLabel")
        layout.addWidget(title)

        description = QLabel(
            t("library.help")
        )
        description.setObjectName("pathLabel")
        description.setWordWrap(True)
        layout.addWidget(description)

        controls = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setObjectName("conversationSearchInput")
        self.search_input.setPlaceholderText(t("library.search_placeholder"))
        self.search_input.returnPressed.connect(self.scan_conversations)
        controls.addWidget(self.search_input, 1)

        self.scan_button = QPushButton(t("library.rescan"))
        self.scan_button.setObjectName("refreshConversationLibraryButton")
        self.scan_button.clicked.connect(self.scan_conversations)
        controls.addWidget(self.scan_button)

        self.pair_button = QPushButton(t("library.bind_selected"))
        self.pair_button.setObjectName("pairConversationsButton")
        self.pair_button.clicked.connect(self.bind_selected_sessions)
        controls.addWidget(self.pair_button)

        self.create_project_button = QPushButton(t("library.create_from_pair"))
        self.create_project_button.setObjectName("createProjectFromPairButton")
        self.create_project_button.clicked.connect(self.request_project_from_pair)
        controls.addWidget(self.create_project_button)
        layout.addLayout(controls)

        self.status_label = QLabel(t("library.not_scanned"))
        self.status_label.setObjectName("pathLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.selection_label = QLabel(t("library.nothing_selected"))
        self.selection_label.setObjectName("pathLabel")
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)

        table_splitter = QSplitter(Qt.Horizontal)
        table_splitter.setChildrenCollapsible(False)
        table_splitter.setHandleWidth(8)

        claude_widget = self._build_provider_column("Claude", "claudeConversationTable")
        codex_widget = self._build_provider_column("Codex", "codexConversationTable")
        table_splitter.addWidget(claude_widget)
        table_splitter.addWidget(codex_widget)
        table_splitter.setSizes([520, 520])
        layout.addWidget(table_splitter, 2)

        preview_splitter = QSplitter(Qt.Horizontal)
        preview_splitter.setChildrenCollapsible(False)
        preview_splitter.setHandleWidth(8)
        self.claude_preview = self._build_preview("claudeConversationPreview")
        self.codex_preview = self._build_preview("codexConversationPreview")
        preview_splitter.addWidget(self.claude_preview)
        preview_splitter.addWidget(self.codex_preview)
        preview_splitter.setSizes([520, 520])
        layout.addWidget(preview_splitter, 2)

        root.addWidget(card, 1)
        self._update_selection_summary()

    def _build_provider_column(self, title: str, object_name: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("sectionLabel")
        layout.addWidget(label)

        table = QTableWidget(0, 5)
        table.setObjectName(object_name)
        table.setHorizontalHeaderLabels([
                t("library.column.name"),
                t("library.column.source"),
                t("dash.detail.state"),
                t("library.column.updated"),
                t("library.column.project"),
            ])
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        style_table(table)
        table.horizontalHeader().setStretchLastSection(True)
        if object_name.startswith("claude"):
            self.claude_table = table
            table.itemSelectionChanged.connect(self._handle_claude_selection_changed)
        else:
            self.codex_table = table
            table.itemSelectionChanged.connect(self._handle_codex_selection_changed)
        layout.addWidget(table, 1)
        return widget

    def _build_preview(self, object_name: str) -> QPlainTextEdit:
        preview = QPlainTextEdit()
        preview.setObjectName(object_name)
        preview.setReadOnly(True)
        preview.setFont(QFont("Consolas", 10))
        preview.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        preview.setPlaceholderText(t("library.detail_placeholder"))
        return preview

    @Slot()
    def scan_conversations(self) -> None:
        query = self.search_input.text().strip()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.claude_hits = self.search_service.search(
                project_path=self.project_path,
                provider="claude",
                query=query,
                limit=200,
                include_snippets=True,
            )
            self.codex_hits = self.search_service.search(
                project_path=self.project_path,
                provider="codex",
                query=query,
                limit=200,
                include_snippets=True,
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                t("library.scan_failed"),
                f"讀取 Claude/Codex 本機對話時發生錯誤：\n{exc}",
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._render_tables()
        self.status_label.setText(
            f"找到 {len(self.claude_hits)} 個 Claude 對話、"
            f"{len(self.codex_hits)} 個 Codex 對話"
        )

    @Slot()
    def bind_selected_sessions(self) -> None:
        if self.selected_claude is None and self.selected_codex is None:
            QMessageBox.information(
                self,
                t("library.nothing_picked.title"),
                t("library.nothing_picked.body"),
            )
            return

        bind_hits(
            self.registry,
            self.project_path,
            (self.selected_claude, self.selected_codex),
        )

        self.sessions_bound.emit()
        QMessageBox.information(
            self,
            t("library.bound.title"),
            t("library.bound.body"),
        )

    @Slot()
    def request_project_from_pair(self) -> None:
        title = self.suggest_project_name_from_pair()
        self.create_project_requested.emit(title)

    def suggest_project_name_from_pair(self) -> str:
        return suggest_project_name((self.selected_claude, self.selected_codex))

    def _render_tables(self) -> None:
        self._render_table(self.claude_table, self.claude_hits)
        self._render_table(self.codex_table, self.codex_hits)
        if self.claude_hits:
            self.claude_table.selectRow(0)
        else:
            self.claude_preview.setPlainText(t("library.no_claude"))
        if self.codex_hits:
            self.codex_table.selectRow(0)
        else:
            self.codex_preview.setPlainText(t("library.no_codex"))

    def _render_table(
        self,
        table: QTableWidget,
        hits: list[ConversationHit],
    ) -> None:
        table.setRowCount(0)
        for hit in hits:
            row = table.rowCount()
            table.insertRow(row)
            values = [
                hit.display_title,
                hit.source_label,
                hit.resume_status,
                hit.updated_at[:19] if hit.updated_at else "",
                hit.project_path,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row)
                if column == 0:
                    item.setToolTip(hit.snippet or hit.title)
                table.setItem(row, column, item)
        fit_columns(table)

    @Slot()
    def _handle_claude_selection_changed(self) -> None:
        self._handle_provider_selection("claude")

    @Slot()
    def _handle_codex_selection_changed(self) -> None:
        self._handle_provider_selection("codex")

    def _handle_provider_selection(self, provider: str) -> None:
        table = self.claude_table if provider == "claude" else self.codex_table
        hits = self.claude_hits if provider == "claude" else self.codex_hits
        preview = self.claude_preview if provider == "claude" else self.codex_preview
        selected = table.selectedItems()
        if not selected:
            return

        row = selected[0].row()
        if row < 0 or row >= len(hits):
            return

        hit = hits[row]
        if provider == "claude":
            self.selected_claude = hit
        else:
            self.selected_codex = hit

        self._update_selection_summary()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            preview.setPlainText(
                self.search_service.render_preview(hit, self.project_path)
            )
        except Exception as exc:
            preview.setPlainText(
                f"{hit.display_title}\n\n預覽讀取失敗：{exc}"
            )
        finally:
            QApplication.restoreOverrideCursor()

    def _update_selection_summary(self) -> None:
        claude = (
            self.selected_claude.display_title
            if self.selected_claude is not None
            else t("library.nothing_picked")
        )
        codex = (
            self.selected_codex.display_title
            if self.selected_codex is not None
            else t("library.nothing_picked")
        )
        self.selection_label.setText(f"目前選擇：Claude {claude}，Codex {codex}")
