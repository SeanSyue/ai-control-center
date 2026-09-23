from __future__ import annotations

from app.core import prompt_markers
from app.i18n import t

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.services.session_registry import SessionEntry, SessionRegistry
from app.services.session_scanner import SessionInfo, SessionMessage, SessionScanner
from app.services.timeline_store import TimelineEvent, TimelineStore


class SessionTimelinePanel(QWidget):
    """Project-scoped Claude/Codex session picker and unified timeline."""

    def __init__(self, project_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.project_path = project_path.expanduser().resolve()
        self.registry = SessionRegistry(self.project_path)
        self.timeline = TimelineStore(self.project_path)
        self.scanner = SessionScanner()
        self.include_external_history = False
        self._busy = False
        self._building_selectors = False
        self._preview_cache: dict[tuple[str, str], str] = {}
        self._title_cache: dict[tuple[str, str], str] = {}
        self._build_ui()
        self.refresh_from_disk()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        session_card = QFrame()
        session_card.setObjectName("card")
        session_layout = QVBoxLayout(session_card)
        session_layout.setContentsMargins(18, 14, 18, 14)
        session_layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel(t("sessions.title"))
        title.setObjectName("sectionLabel")
        header.addWidget(title)
        header.addStretch()

        self.scan_button = QPushButton(t("sessions.scan_project"))
        self.scan_button.clicked.connect(self.scan_sessions)
        header.addWidget(self.scan_button)

        self.scan_all_button = QPushButton(t("sessions.scan_all"))
        self.scan_all_button.clicked.connect(
            lambda _checked=False: self.scan_sessions(include_all=True)
        )
        header.addWidget(self.scan_all_button)

        self.latest_pair_button = QPushButton(t("sessions.pick_latest"))
        self.latest_pair_button.clicked.connect(self.select_latest_pair)
        header.addWidget(self.latest_pair_button)

        self.load_history_button = QPushButton(t("sessions.show_selected"))
        self.load_history_button.clicked.connect(self.load_selected_history)
        header.addWidget(self.load_history_button)
        session_layout.addLayout(header)

        self.pair_summary_label = QLabel()
        self.pair_summary_label.setObjectName("sessionPairSummary")
        self.pair_summary_label.setWordWrap(True)
        session_layout.addWidget(self.pair_summary_label)

        guide = QLabel(
            t("sessions.help")
        )
        guide.setWordWrap(True)
        guide.setObjectName("pathLabel")
        session_layout.addWidget(guide)

        claude_row = QHBoxLayout()
        claude_label = QLabel("Claude")
        claude_label.setMinimumWidth(62)
        claude_row.addWidget(claude_label)
        self.claude_combo = QComboBox()
        self.claude_combo.setMinimumHeight(34)
        self.claude_combo.currentIndexChanged.connect(
            self._on_claude_selection_changed
        )
        claude_row.addWidget(self.claude_combo, 1)
        self.new_claude_button = QPushButton(t("sessions.new"))
        self.new_claude_button.clicked.connect(
            lambda: self.select_new_session("claude")
        )
        claude_row.addWidget(self.new_claude_button)
        self.rename_claude_button = QPushButton(t("sessions.rename"))
        self.rename_claude_button.clicked.connect(
            lambda: self.rename_selected_session("claude")
        )
        claude_row.addWidget(self.rename_claude_button)
        session_layout.addLayout(claude_row)

        self.claude_detail_label = QLabel()
        self.claude_detail_label.setWordWrap(True)
        self.claude_detail_label.setObjectName("pathLabel")
        self.claude_detail_label.setContentsMargins(72, 0, 0, 0)
        session_layout.addWidget(self.claude_detail_label)

        codex_row = QHBoxLayout()
        codex_label = QLabel("Codex")
        codex_label.setMinimumWidth(62)
        codex_row.addWidget(codex_label)
        self.codex_combo = QComboBox()
        self.codex_combo.setMinimumHeight(34)
        self.codex_combo.currentIndexChanged.connect(
            self._on_codex_selection_changed
        )
        codex_row.addWidget(self.codex_combo, 1)
        self.new_codex_button = QPushButton(t("sessions.new"))
        self.new_codex_button.clicked.connect(
            lambda: self.select_new_session("codex")
        )
        codex_row.addWidget(self.new_codex_button)
        self.rename_codex_button = QPushButton(t("sessions.rename"))
        self.rename_codex_button.clicked.connect(
            lambda: self.rename_selected_session("codex")
        )
        codex_row.addWidget(self.rename_codex_button)
        session_layout.addLayout(codex_row)

        self.codex_detail_label = QLabel()
        self.codex_detail_label.setWordWrap(True)
        self.codex_detail_label.setObjectName("pathLabel")
        self.codex_detail_label.setContentsMargins(72, 0, 0, 0)
        session_layout.addWidget(self.codex_detail_label)

        note = QLabel(
            t("sessions.preview_help")
        )
        note.setWordWrap(True)
        note.setObjectName("pathLabel")
        session_layout.addWidget(note)

        preview_title = QLabel(t("sessions.preview.title"))
        preview_title.setObjectName("sectionLabel")
        session_layout.addWidget(preview_title)

        preview_splitter = QSplitter(Qt.Horizontal)
        preview_splitter.setObjectName("previewSplitter")
        preview_splitter.setChildrenCollapsible(False)
        preview_splitter.setMinimumHeight(150)
        preview_splitter.setHandleWidth(8)
        # The handle's colours live in native_theme.py with everything else. A
        # local setStyleSheet here would win over the window's and keep this one
        # widget light grey after a switch to night.

        claude_preview_widget = QWidget()
        claude_preview_column = QVBoxLayout(claude_preview_widget)
        claude_preview_column.setContentsMargins(0, 0, 0, 0)
        claude_preview_column.setSpacing(6)

        claude_preview_label = QLabel(t("sessions.preview.claude"))
        claude_preview_label.setObjectName("pathLabel")
        claude_preview_column.addWidget(claude_preview_label)

        self.claude_preview = QPlainTextEdit()
        self.claude_preview.setReadOnly(True)
        self.claude_preview.setMinimumHeight(120)
        self.claude_preview.setPlaceholderText(
            t("sessions.preview.claude_empty")
        )
        self.claude_preview.setFont(QFont("Microsoft JhengHei UI", 9))
        self.claude_preview.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.claude_preview.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOn
        )
        self.claude_preview.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )
        claude_preview_column.addWidget(self.claude_preview, 1)
        preview_splitter.addWidget(claude_preview_widget)

        codex_preview_widget = QWidget()
        codex_preview_column = QVBoxLayout(codex_preview_widget)
        codex_preview_column.setContentsMargins(0, 0, 0, 0)
        codex_preview_column.setSpacing(6)

        codex_preview_label = QLabel(t("sessions.preview.codex"))
        codex_preview_label.setObjectName("pathLabel")
        codex_preview_column.addWidget(codex_preview_label)

        self.codex_preview = QPlainTextEdit()
        self.codex_preview.setReadOnly(True)
        self.codex_preview.setMinimumHeight(120)
        self.codex_preview.setPlaceholderText(
            t("sessions.preview.codex_empty")
        )
        self.codex_preview.setFont(QFont("Microsoft JhengHei UI", 9))
        self.codex_preview.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.codex_preview.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOn
        )
        self.codex_preview.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )
        codex_preview_column.addWidget(self.codex_preview, 1)
        preview_splitter.addWidget(codex_preview_widget)

        preview_splitter.setStretchFactor(0, 1)
        preview_splitter.setStretchFactor(1, 1)
        preview_splitter.setSizes([440, 440])
        session_layout.addWidget(preview_splitter)

        timeline_pane = QWidget()
        timeline_layout = QVBoxLayout(timeline_pane)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(10)

        timeline_header = QHBoxLayout()
        timeline_title = QLabel(t("timeline.title"))
        timeline_title.setObjectName("sectionLabel")
        timeline_header.addWidget(timeline_title)

        self.run_combo = QComboBox()
        self.run_combo.setMinimumWidth(260)
        self.run_combo.currentIndexChanged.connect(
            self._on_timeline_filter_changed
        )
        timeline_header.addWidget(self.run_combo)

        self.source_combo = QComboBox()
        self.source_combo.addItem(t("timeline.all_sources"), "")
        self.source_combo.addItem("Claude", "claude")
        self.source_combo.addItem("Codex", "codex")
        self.source_combo.addItem("AICC", "aicc")
        self.source_combo.addItem(t("timeline.source.user"), "user")
        self.source_combo.currentIndexChanged.connect(
            self._on_timeline_filter_changed
        )
        timeline_header.addWidget(self.source_combo)

        self.refresh_button = QPushButton(t("common.refresh"))
        self.refresh_button.clicked.connect(self.refresh_timeline)
        timeline_header.addWidget(self.refresh_button)
        timeline_header.addStretch()
        timeline_layout.addLayout(timeline_header)

        self.timeline_output = QPlainTextEdit()
        self.timeline_output.setReadOnly(True)
        self.timeline_output.setMinimumHeight(120)
        self.timeline_output.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOn
        )
        self.timeline_output.setPlaceholderText(
            t("timeline.empty_help")
        )
        self.timeline_output.setFont(QFont("Consolas", 10))
        timeline_layout.addWidget(self.timeline_output, 1)

        self.main_splitter = QSplitter(Qt.Vertical)
        self.main_splitter.setObjectName("sessionSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(10)
        self.main_splitter.addWidget(session_card)
        self.main_splitter.addWidget(timeline_pane)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setSizes([560, 260])
        root.addWidget(self.main_splitter, 1)

    def set_project(self, project_path: Path) -> None:
        self.project_path = project_path.expanduser().resolve()
        self.registry = SessionRegistry(self.project_path)
        self.timeline = TimelineStore(self.project_path)
        self.include_external_history = False
        self._preview_cache.clear()
        self._title_cache.clear()
        self.refresh_from_disk()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (
            self.scan_button,
            self.scan_all_button,
            self.latest_pair_button,
            self.load_history_button,
            self.claude_combo,
            self.codex_combo,
            self.new_claude_button,
            self.new_codex_button,
            self.rename_claude_button,
            self.rename_codex_button,
            self.run_combo,
            self.source_combo,
            self.refresh_button,
        ):
            widget.setEnabled(not busy)

    def selected_session_ids(self) -> tuple[str, str]:
        return (
            str(self.claude_combo.currentData() or "").strip(),
            str(self.codex_combo.currentData() or "").strip(),
        )

    def refresh_from_disk(self) -> None:
        self._refresh_session_selectors()
        self._refresh_run_selector()
        self.refresh_timeline()

    def _refresh_session_selectors(self) -> None:
        self._building_selectors = True
        try:
            self._populate_provider_combo("claude", self.claude_combo)
            self._populate_provider_combo("codex", self.codex_combo)
        finally:
            self._building_selectors = False
        self._update_selection_details()
        self._update_session_previews()

    def _populate_provider_combo(
        self,
        provider: str,
        combo: QComboBox,
    ) -> None:
        selected = self.registry.selected_session_id(provider)
        entries = self.registry.list_sessions(provider)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(t("sessions.new_on_next_run"), "")
        selected_index = 0

        title_counts: dict[str, int] = {}
        for number, entry in enumerate(entries, start=1):
            base_label = self._session_label(entry, number, provider)
            title_counts[base_label] = title_counts.get(base_label, 0) + 1
            duplicate_number = title_counts[base_label]
            label = (
                base_label
                if duplicate_number == 1
                else f"{base_label}（{duplicate_number}）"
            )
            combo.addItem(label, entry.session_id)
            item_index = combo.count() - 1
            combo.setItemData(
                item_index,
                self._session_tooltip(entry),
                Qt.ToolTipRole,
            )
            if entry.session_id == selected:
                selected_index = item_index

        combo.setCurrentIndex(selected_index)
        combo.blockSignals(False)

    def _refresh_run_selector(self) -> None:
        current = str(self.run_combo.currentData() or "")
        self.run_combo.blockSignals(True)
        self.run_combo.clear()
        self.run_combo.addItem(t("sessions.all_sessions"), "")
        selected_index = 0

        for run in self.timeline.list_runs():
            stamp = self._display_time(run.updated_at or run.created_at)
            preview = run.task_preview or run.run_id
            if len(preview) > 56:
                preview = preview[:53] + "…"
            label = f"{stamp}｜{run.status}｜{preview}"
            self.run_combo.addItem(label, run.run_id)
            if run.run_id == current:
                selected_index = self.run_combo.count() - 1

        self.run_combo.setCurrentIndex(selected_index)
        self.run_combo.blockSignals(False)

    @Slot()
    def scan_sessions(self, include_all: bool = False) -> None:
        if self._busy:
            return

        claude_sessions: list[SessionInfo] = []
        codex_sessions: list[SessionInfo] = []
        errors: list[str] = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            try:
                claude_sessions = self.scanner.scan_claude(
                    self.project_path,
                    include_all=include_all,
                )
            except Exception as exc:
                errors.append(f"Claude 掃描失敗：{exc}")

            try:
                codex_sessions = self.scanner.scan_codex(
                    self.project_path,
                    include_all=include_all,
                )
            except Exception as exc:
                errors.append(f"Codex 掃描失敗：{exc}")

            existing_by_key = {
                (entry.provider, entry.session_id): entry
                for provider in ("claude", "codex")
                for entry in self.registry.list_sessions(provider)
            }

            for item in [*claude_sessions, *codex_sessions]:
                existing = existing_by_key.get(
                    (item.provider, item.session_id)
                )
                metadata = dict(item.metadata)
                title = item.title

                if (
                    existing is not None
                    and bool(existing.metadata.get("custom_title"))
                ):
                    title = existing.title
                    metadata["custom_title"] = True

                self.registry.upsert(
                    SessionEntry(
                        provider=item.provider,
                        session_id=item.session_id,
                        title=title,
                        project_path=item.project_path or str(self.project_path),
                        created_at=item.created_at,
                        last_used_at=item.updated_at,
                        source=item.source,
                        metadata=metadata,
                    ),
                    select=False,
                )
        finally:
            QApplication.restoreOverrideCursor()

        self._preview_cache.clear()
        self._title_cache.clear()
        self._refresh_session_selectors()
        scope_label = t("sessions.scope.all_local") if include_all else t("sessions.scope.this_project")
        detail = (
            f"範圍：{scope_label}\n"
            f"Claude：找到 {len(claude_sessions)} 個\n"
            f"Codex：找到 {len(codex_sessions)} 個"
        )
        if errors:
            detail += "\n\n" + "\n".join(errors)
        detail += (
            t("sessions.cloud_note")
        )
        QMessageBox.information(
            self,
            t("sessions.scan_done"),
            detail,
        )

    @Slot()
    def select_latest_pair(self) -> None:
        if self._busy:
            return

        changed = False
        for combo in (self.claude_combo, self.codex_combo):
            if combo.count() > 1:
                combo.setCurrentIndex(1)
                changed = True

        if not changed:
            QMessageBox.information(
                self,
                t("sessions.none.title"),
                t("sessions.none.body"),
            )
            return

        self._update_selection_details()

    @Slot()
    def load_selected_history(self) -> None:
        if self._busy:
            return
        if not any(self.selected_session_ids()):
            QMessageBox.information(
                self,
                t("sessions.not_picked.title"),
                t("sessions.not_picked.body"),
            )
            return
        self.include_external_history = True
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.refresh_timeline(include_external=True)
        finally:
            QApplication.restoreOverrideCursor()

    def select_new_session(self, provider: str) -> None:
        if self._busy:
            return
        self.registry.clear_selected(provider)
        combo = self.claude_combo if provider == "claude" else self.codex_combo
        combo.setCurrentIndex(0)
        self.include_external_history = False
        self._update_selection_details()
        self._update_session_previews()
        self.refresh_timeline()

    def rename_selected_session(self, provider: str) -> None:
        if self._busy:
            return

        combo = (
            self.claude_combo
            if provider == "claude"
            else self.codex_combo
        )
        session_id = str(combo.currentData() or "").strip()
        provider_name = "Claude" if provider == "claude" else "Codex"

        if not session_id:
            QMessageBox.information(
                self,
                t("sessions.not_picked.title"),
                f"請先選擇一個 {provider_name} 對話。",
            )
            return

        entry = next(
            (
                item
                for item in self.registry.list_sessions(provider)
                if item.session_id == session_id
            ),
            None,
        )
        current_title = (
            self._resolved_display_title(entry, provider)
            if entry is not None
            else ""
        )

        title, accepted = QInputDialog.getText(
            self,
            f"重新命名 {provider_name} 對話",
            t("sessions.name_label"),
            QLineEdit.Normal,
            current_title,
        )
        title = title.strip()
        if not accepted or not title:
            return

        self.registry.rename(provider, session_id, title)
        self._refresh_session_selectors()
        target_combo = (
            self.claude_combo
            if provider == "claude"
            else self.codex_combo
        )
        index = target_combo.findData(session_id)
        if index >= 0:
            target_combo.setCurrentIndex(index)
        self._preview_cache.pop((provider, session_id), None)
        self._title_cache.pop((provider, session_id), None)
        self._update_selection_details()
        self._update_session_previews()

    @Slot(int)
    def _on_claude_selection_changed(self, _index: int) -> None:
        if self._building_selectors:
            return
        self.registry.set_selected(
            "claude",
            str(self.claude_combo.currentData() or ""),
        )
        self.include_external_history = False
        self._update_selection_details()
        self._update_session_previews()
        self.refresh_timeline()

    @Slot(int)
    def _on_codex_selection_changed(self, _index: int) -> None:
        if self._building_selectors:
            return
        self.registry.set_selected(
            "codex",
            str(self.codex_combo.currentData() or ""),
        )
        self.include_external_history = False
        self._update_selection_details()
        self._update_session_previews()
        self.refresh_timeline()

    @Slot(int)
    def _on_timeline_filter_changed(self, _index: int) -> None:
        self.refresh_timeline()

    @Slot()
    def refresh_timeline(self, include_external: bool | None = None) -> None:
        if include_external is not None:
            self.include_external_history = include_external

        run_id = str(self.run_combo.currentData() or "")
        source = str(self.source_combo.currentData() or "")
        sources = [source] if source else None
        events = self.timeline.load_events(
            run_id=run_id,
            sources=sources,
            limit=2500,
        )

        combined: list[dict[str, Any]] = [
            self._timeline_event_to_dict(event) for event in events
        ]

        if self.include_external_history and not run_id:
            claude_id, codex_id = self.selected_session_ids()
            if claude_id and source in {"", "claude"}:
                combined.extend(
                    self._session_message_to_dict(item)
                    for item in self.scanner.load_claude_messages(
                        claude_id,
                        self.project_path,
                    )
                )
            if codex_id and source in {"", "codex"}:
                combined.extend(
                    self._session_message_to_dict(item)
                    for item in self.scanner.load_codex_messages(
                        codex_id,
                        self.project_path,
                    )
                )

        deduplicated: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        for item in combined:
            key = (
                str(item.get("source", "")),
                str(item.get("session_id", "")),
                str(item.get("role", "")),
                str(item.get("content", "")).strip(),
                "",
            )
            deduplicated[key] = item

        ordered = sorted(
            deduplicated.values(),
            key=lambda item: str(item.get("timestamp", "")),
        )
        text = self._render_timeline(ordered)
        self.timeline_output.setPlainText(text)
        cursor = self.timeline_output.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.timeline_output.setTextCursor(cursor)

    @staticmethod
    def _timeline_event_to_dict(event: TimelineEvent) -> dict[str, Any]:
        return {
            "timestamp": event.timestamp,
            "source": event.source,
            "role": SessionTimelinePanel._role_for_event(event),
            "event_type": event.event_type,
            "content": event.content,
            "session_id": event.session_id,
            "run_id": event.run_id,
        }

    @staticmethod
    def _session_message_to_dict(message: SessionMessage) -> dict[str, Any]:
        return {
            "timestamp": message.timestamp,
            "source": message.provider,
            "role": message.role,
            "event_type": "external_history",
            "content": message.content,
            "session_id": message.session_id,
            "run_id": "",
        }

    @staticmethod
    def _role_for_event(event: TimelineEvent) -> str:
        if event.source == "user":
            return "user"
        if event.source in {"claude", "codex"}:
            return "assistant"
        return event.event_type

    @staticmethod
    def _render_timeline(items: list[dict[str, Any]]) -> str:
        if not items:
            return t("timeline.none_yet")

        source_names = {
            "claude": "Claude",
            "codex": "Codex",
            "aicc": "AICC",
            "user": t("timeline.source.user"),
        }
        lines: list[str] = []
        for item in items:
            source = str(item.get("source", "aicc"))
            source_name = source_names.get(source, source)
            role = str(item.get("role", ""))
            timestamp = SessionTimelinePanel._display_time(
                str(item.get("timestamp", ""))
            )
            session_id = str(item.get("session_id", ""))
            session_suffix = f"｜{session_id[:8]}" if session_id else ""
            role_suffix = f"｜{role}" if role else ""
            lines.append(
                f"[{timestamp}] [{source_name}{role_suffix}{session_suffix}]"
            )
            content = str(item.get("content", "")).rstrip()
            lines.append(content or t("common.no_content"))
            lines.append("")
        return "\n".join(lines).rstrip()


    def _update_session_previews(self) -> None:
        self.claude_preview.setPlainText(
            self._session_preview_text(
                "claude",
                self.claude_combo,
            )
        )
        self.codex_preview.setPlainText(
            self._session_preview_text(
                "codex",
                self.codex_combo,
            )
        )

    def _session_preview_text(
        self,
        provider: str,
        combo: QComboBox,
    ) -> str:
        session_id = str(combo.currentData() or "").strip()
        provider_name = "Claude" if provider == "claude" else "Codex"

        if not session_id:
            return (
                f"尚未綁定既有 {provider_name} 對話。\n\n"
                "下一次執行時會建立新對話。"
            )

        cache_key = (provider, session_id)
        cached = self._preview_cache.get(cache_key)
        if cached is not None:
            return cached

        entry = next(
            (
                item
                for item in self.registry.list_sessions(provider)
                if item.session_id == session_id
            ),
            None,
        )
        title = (
            self._resolved_display_title(entry, provider)
            if entry is not None
            else t("session.unnamed", provider=provider_name)
        )
        stamp = (
            self._display_time(entry.last_used_at or entry.created_at)
            if entry is not None
            else t("common.time_unknown")
        )

        try:
            if provider == "claude":
                messages = self.scanner.load_claude_messages(
                    session_id,
                    self.project_path,
                )
            else:
                messages = self.scanner.load_codex_messages(
                    session_id,
                    self.project_path,
                )
        except Exception as exc:
            preview = (
                f"對話：{title}\n"
                f"最後使用：{stamp}\n"
                f"對話 ID：{session_id[:8]}\n\n"
                f"內容預覽讀取失敗：{exc}"
            )
            self._preview_cache[cache_key] = preview
            return preview

        preview = self._render_session_preview(
            provider=provider,
            title=title,
            stamp=stamp,
            session_id=session_id,
            messages=messages,
        )
        self._preview_cache[cache_key] = preview
        return preview

    @classmethod
    def _render_session_preview(
        cls,
        provider: str,
        title: str,
        stamp: str,
        session_id: str,
        messages: list[SessionMessage],
    ) -> str:
        provider_name = "Claude" if provider == "claude" else "Codex"
        header = (
            f"對話：{title}\n"
            f"最後使用：{stamp}\n"
            f"對話 ID：{session_id[:8]}"
        )

        cleaned: list[tuple[str, str]] = []
        for message in messages:
            content = cls._clean_preview_content(message.content)
            if not content:
                continue
            role = t("timeline.source.user") if message.role == "user" else provider_name
            cleaned.append((role, content))

        if not cleaned:
            return (
                header
                + t("sessions.unreadable")
                + t("sessions.unreadable_reason")
            )

        first_user = next(
            (
                content
                for role, content in cleaned
                if role == t("timeline.source.user")
            ),
            "",
        )

        recent = cleaned[-4:]
        lines = [header]

        if first_user:
            lines.extend(
                [
                    "",
                    t("sessions.first_request"),
                    cls._truncate_preview(first_user, 650),
                ]
            )

        lines.extend(["", t("sessions.latest_exchange")])
        for role, content in recent:
            lines.append(
                f"{role}：{cls._truncate_preview(content, 320)}"
            )

        return "\n".join(lines).rstrip()

    @staticmethod
    def _clean_preview_content(content: str) -> str:
        text = str(content or "").replace("\r\n", "\n").strip()
        if not text:
            return ""

        # **這些字住在 `app/core/prompt_markers.py`。**它們要跟
        # `prompt_builder` 實際送出去的一模一樣，所以是**由提示詞決定的，
        # 不是由介面語言決定的**——介面翻成英文的時候這裡不准動
        #（使用者硬碟上的舊對話紀錄還是中文）。
        markers = prompt_markers.opening_markers()
        for marker in markers:
            index = text.find(marker)
            if index >= 0:
                candidate = text[index + len(marker):].strip()
                if candidate:
                    text = candidate
                    break

        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]
        return "\n".join(lines)

    @staticmethod
    def _truncate_preview(content: str, limit: int) -> str:
        text = content.strip()
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 1)].rstrip() + "…"

    def _update_selection_details(self) -> None:
        self.claude_detail_label.setText(
            self._selection_detail("claude", self.claude_combo)
        )
        self.codex_detail_label.setText(
            self._selection_detail("codex", self.codex_combo)
        )
        self.pair_summary_label.setText(self._pair_summary_text())

    def _pair_summary_text(self) -> str:
        claude = self._selected_title("claude", self.claude_combo)
        codex = self._selected_title("codex", self.codex_combo)
        return f"目前配對：Claude「{claude}」 ↔ Codex「{codex}」"

    def _selected_title(self, provider: str, combo: QComboBox) -> str:
        session_id = str(combo.currentData() or "").strip()
        provider_name = "Claude" if provider == "claude" else "Codex"
        if not session_id:
            return f"新 {provider_name} 對話"

        entry = next(
            (
                item
                for item in self.registry.list_sessions(provider)
                if item.session_id == session_id
            ),
            None,
        )
        if entry is None:
            return session_id[:8]
        return self._resolved_display_title(entry, provider)

    def _selection_detail(
        self,
        provider: str,
        combo: QComboBox,
    ) -> str:
        session_id = str(combo.currentData() or "").strip()
        provider_name = "Claude" if provider == "claude" else "Codex"
        if not session_id:
            return f"目前：下一次執行時建立新的 {provider_name} 對話"

        entry = next(
            (
                item
                for item in self.registry.list_sessions(provider)
                if item.session_id == session_id
            ),
            None,
        )
        if entry is None:
            return f"已選對話：{session_id[:8]}"

        title = self._resolved_display_title(entry, provider)
        stamp = self._display_time(entry.last_used_at or entry.created_at)
        title_source = self._title_source_label(entry)
        return (
            f"已選：{title}　｜　名稱來源：{title_source}　｜　最後使用：{stamp}　｜　"
            f"對話編號：{entry.session_id[:8]}"
        )

    def _resolved_display_title(
        self,
        entry: SessionEntry,
        provider: str,
    ) -> str:
        cache_key = (provider, entry.session_id)
        cached = self._title_cache.get(cache_key)
        if cached:
            return cached

        raw_title = entry.display_title.strip()
        if bool(entry.metadata.get("custom_title")):
            resolved = raw_title
        elif (
            self._metadata_title_source(entry) in ("session_index", "official")
            and raw_title
        ):
            resolved = raw_title
        elif self._is_readable_title(raw_title, entry.session_id):
            resolved = raw_title
        else:
            resolved = self._infer_title_from_session(entry, provider)

        if not resolved:
            provider_name = "Claude" if provider == "claude" else "Codex"
            stamp = self._display_short_time(
                entry.last_used_at or entry.created_at
            )
            resolved = f'{t("session.unnamed", provider=provider_name)}｜{stamp}'

        resolved = self._truncate_title(resolved, 38)
        self._title_cache[cache_key] = resolved
        return resolved

    @staticmethod
    def _is_readable_title(title: str, session_id: str) -> bool:
        compact = " ".join(str(title or "").split()).strip()
        lowered = compact.lower()

        if (
            not compact
            or lowered in {"none", "null", "undefined"}
            or compact == session_id
            or lowered.startswith(("claude ", "codex "))
            or compact.startswith(t("session.unnamed_prefix"))
        ):
            return False

        if prompt_markers.is_boilerplate(compact):
            return False

        return len(compact) <= 42

    def _infer_title_from_session(
        self,
        entry: SessionEntry,
        provider: str,
    ) -> str:
        try:
            if provider == "claude":
                messages = self.scanner.load_claude_messages(
                    entry.session_id,
                    self.project_path,
                )
            else:
                messages = self.scanner.load_codex_messages(
                    entry.session_id,
                    self.project_path,
                )
        except Exception:
            return ""

        for message in messages:
            if message.role != "user":
                continue

            inferred = self._title_from_content(message.content, provider)
            if inferred:
                return inferred

        for message in messages:
            inferred = self._title_from_content(message.content, provider)
            if inferred:
                return inferred

        return ""

    @classmethod
    def _title_from_content(
        cls,
        content: str,
        provider: str,
    ) -> str:
        text = str(content or "").replace("\r\n", "\n").strip()
        if not text:
            return ""

        embedded = cls._extract_json_task(text)
        if embedded:
            text = embedded

        text = cls._clean_preview_content(text)
        if not text:
            return ""

        question_match = re.search(
            r"ASK_USER\s*:\s*([^\n]+)",
            text,
            flags=re.IGNORECASE,
        )
        if question_match:
            question = question_match.group(1).strip(" 「」\"'")
            if question and question != prompt_markers.QUESTION_PLACEHOLDER:
                return cls._truncate_title(question, 34)

        # 整行就是開場白的那些。`prompt_markers` 那一份之外多兩個，
        # 因為它們只在「整行等於它」的時候才算——`審查任務`／`請審查以下修改`
        # 出現在句子中間是正常的內容。
        ignored = {*prompt_markers.WHOLE_LINE_BOILERPLATE, *prompt_markers.CURRENT,
                   *prompt_markers.LEGACY}
        candidates: list[str] = []

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("```"):
                continue

            line = line.lstrip("#").strip()
            line = re.sub(r"^[-*•]+\s*", "", line)
            line = re.sub(r"^\d+[.)、]\s*", "", line)
            line = re.sub(
                prompt_markers.TITLE_PREFIX_PATTERN, "", line
            ).strip()

            if (
                not line
                or line in ignored
                or line.startswith("{")
                or line.startswith("[")
                or line.startswith('"')
                or line.startswith(prompt_markers.PROMPT_LINE_PREFIXES)
            ):
                continue

            candidates.append(" ".join(line.split()))

        if not candidates:
            return ""

        candidate = candidates[0]

        if provider == "codex":
            candidate = re.sub(
                prompt_markers.REVIEW_PREFIX_PATTERN,
                "",
                candidate,
                flags=re.IGNORECASE,
            ).strip()

        return cls._truncate_title(candidate, 34)

    @staticmethod
    def _extract_json_task(text: str) -> str:
        match = re.search(
            r'"task"\s*:\s*("(?:\\.|[^"\\])*")',
            text,
            flags=re.DOTALL,
        )
        if not match:
            return ""

        try:
            value = json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            return ""

        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _truncate_title(title: str, limit: int) -> str:
        compact = " ".join(str(title or "").split()).strip(" ｜|")
        if len(compact) <= limit:
            return compact
        return compact[: max(1, limit - 1)].rstrip() + "…"

    def _session_label(
        self,
        entry: SessionEntry,
        number: int,
        provider: str,
    ) -> str:
        title = self._resolved_display_title(entry, provider)
        return title

    @classmethod
    def _session_tooltip(cls, entry: SessionEntry) -> str:
        # 這裡本來自己寫一份長名，而它**少了三個來源**
        #（`claude-desktop`、`claude-task`、`imported-transcript`）——
        # 那三種的提示會直接把原始代號印給使用者看。
        # 現在跟表格那一欄同一個來源（`conversation_search`）。
        from app.services.conversation_search import source_label

        source = source_label(entry.source, long=True)
        stamp = cls._display_time(entry.last_used_at or entry.created_at)
        return (
            f"最後使用：{stamp}\n"
            f"名稱來源：{cls._title_source_label(entry)}\n"
            f"來源：{source}\n"
            f"對話 ID：{entry.session_id}\n"
            f"專案：{entry.project_path}"
        )

    @classmethod
    def _title_source_label(cls, entry: SessionEntry) -> str:
        if bool(entry.metadata.get("custom_title")):
            return t("title_source.manual")

        labels = {
            # From the Claude desktop app's own sidebar index - the name shown
            # in the app itself, which for titleSource "user" is the one the
            # user typed. Distinguished from 官方名稱 because that one covers
            # any provider-supplied name, including Codex's.
            "session_index": t("title_source.session_name"),
            "official": t("title_source.official"),
            "summary": t("title_source.summary"),
            "task_subject": t("title_source.claude_task"),
            "project_folder": t("title_source.project_folder"),
            "message_preview": t("title_source.guessed_from_content"),
            "fallback": t("title_source.system_id"),
        }
        return labels.get(cls._metadata_title_source(entry), t("title_source.guessed_from_content"))

    @staticmethod
    def _metadata_title_source(entry: SessionEntry) -> str:
        return str(entry.metadata.get("title_source", "")).strip()

    @staticmethod
    def _display_short_time(value: str) -> str:
        text = value.strip()
        if not text:
            return t("common.time_unknown")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.astimezone().strftime("%m/%d %H:%M")
        except ValueError:
            return text[:16]

    @staticmethod
    def _display_time(value: str) -> str:
        text = value.strip()
        if not text:
            return t("common.time_unknown")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.astimezone().strftime("%Y/%m/%d %H:%M")
        except ValueError:
            return text[:16]
