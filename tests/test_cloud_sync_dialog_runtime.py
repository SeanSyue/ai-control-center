from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.services.cloud_sync import CloudSyncSettings
from app.ui.cloud_sync_dialog import CloudSettingsDialog


def test_cloud_settings_dialog_reflects_current_settings() -> None:
    application = QApplication.instance() or QApplication([])
    current = CloudSyncSettings(
        enabled=True,
        root_path="C:/Drive/AICC",
        auto_sync_on_start=False,
        auto_sync_on_switch=True,
        auto_sync_after_run=False,
        device_id="device-1",
    )

    dialog = CloudSettingsDialog(current)

    assert dialog.enabled_checkbox.isChecked() is True
    assert dialog.path_edit.text() == "C:/Drive/AICC"
    assert dialog.start_checkbox.isChecked() is False
    assert dialog.switch_checkbox.isChecked() is True
    assert dialog.after_run_checkbox.isChecked() is False

    dialog.deleteLater()
    application.processEvents()


def test_cloud_settings_dialog_settings_round_trips_edits() -> None:
    application = QApplication.instance() or QApplication([])
    dialog = CloudSettingsDialog(CloudSyncSettings())

    dialog.enabled_checkbox.setChecked(True)
    dialog.path_edit.setText("D:/Backup/AICC")
    dialog.start_checkbox.setChecked(True)

    updated = dialog.settings(device_id="device-2")

    assert updated.enabled is True
    assert updated.root_path == "D:/Backup/AICC"
    assert updated.auto_sync_on_start is True
    assert updated.device_id == "device-2"

    dialog.deleteLater()
    application.processEvents()
