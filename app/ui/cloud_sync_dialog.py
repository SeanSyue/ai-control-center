from __future__ import annotations

from app.i18n import t

import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from app.services.cloud_sync import CloudSyncSettings, GoogleDriveFolderSync


class CloudSyncWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        sync_service: GoogleDriveFolderSync,
        project_path: Path,
    ) -> None:
        super().__init__()
        self.sync_service = sync_service
        self.project_path = project_path

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(
                self.sync_service.sync_project(self.project_path)
            )
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            self.finished.emit()


class CloudSettingsDialog(QDialog):
    def __init__(
        self,
        settings: CloudSyncSettings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("cloud.settings.title"))
        self.resize(620, 260)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.enabled_checkbox = QCheckBox(t("cloud.enable"))
        self.enabled_checkbox.setChecked(settings.enabled)
        form.addRow(t("dash.detail.state"), self.enabled_checkbox)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(settings.root_path)
        self.path_edit.setPlaceholderText(
            t("cloud.pick_folder_hint")
        )
        path_row.addWidget(self.path_edit, 1)
        browse_button = QPushButton(t("common.browse"))
        browse_button.clicked.connect(self.browse_folder)
        path_row.addWidget(browse_button)
        form.addRow(t("cloud.folder_label"), path_row)

        self.start_checkbox = QCheckBox(t("cloud.auto.on_start"))
        self.start_checkbox.setChecked(settings.auto_sync_on_start)
        form.addRow(t("cloud.auto.title"), self.start_checkbox)

        self.switch_checkbox = QCheckBox(t("cloud.auto.on_switch"))
        self.switch_checkbox.setChecked(settings.auto_sync_on_switch)
        form.addRow("", self.switch_checkbox)

        self.after_run_checkbox = QCheckBox(t("cloud.auto.on_finish"))
        self.after_run_checkbox.setChecked(settings.auto_sync_after_run)
        form.addRow("", self.after_run_checkbox)

        root.addLayout(form)

        note = QLabel(
            t("cloud.scope_note")
        )
        note.setWordWrap(True)
        note.setObjectName("pathLabel")
        root.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @Slot()
    def browse_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            t("cloud.pick_folder"),
            self.path_edit.text().strip() or str(Path.home()),
        )
        if selected:
            self.path_edit.setText(selected)

    def settings(self, device_id: str) -> CloudSyncSettings:
        return CloudSyncSettings(
            enabled=self.enabled_checkbox.isChecked(),
            root_path=self.path_edit.text().strip(),
            auto_sync_on_start=self.start_checkbox.isChecked(),
            auto_sync_on_switch=self.switch_checkbox.isChecked(),
            auto_sync_after_run=self.after_run_checkbox.isChecked(),
            device_id=device_id,
        )
