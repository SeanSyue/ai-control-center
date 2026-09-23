"""A read-only viewer for the long text the web panels produce.

The native KnowledgePanel had a preview pane built into it, so a readiness
report or a document excerpt had somewhere to go. The HTML panels have no such
pane - their layouts are fixed by the design - so the text needs a home of its
own or half the controls have nowhere to put their answer.

Deliberately not a QMessageBox: these are readiness reports, indexed excerpts
and generated handoff packages, routinely hundreds of lines. A message box
would either truncate them or grow past the screen, and neither is scrollable.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)


class TextViewDialog(QDialog):
    def __init__(self, title: str, text: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        if subtitle:
            caption = QLabel(subtitle)
            caption.setObjectName("subtitleLabel")
            caption.setWordWrap(True)
            # Selectable because it is usually a path someone wants to copy.
            caption.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(caption)

        self.body = QPlainTextEdit(text)
        self.body.setReadOnly(True)
        self.body.setObjectName("liveLog")   # full-contrast surface, not the inset
        layout.addWidget(self.body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def reveal(path: Path) -> bool:
    """Open a folder in Explorer, or select a file inside its folder.

    Returns whether the call was made, so a caller can fall back to showing the
    contents instead of silently doing nothing.
    """
    try:
        if path.is_dir():
            os.startfile(str(path))          # noqa: S606 - Windows shell open
            return True
        if path.is_file():
            # /select, highlights the file rather than opening it in whatever
            # application happens to own the extension.
            # SHOWS_A_WINDOW_ON_PURPOSE：這一行的全部意義就是開一個檔案總管
            # 視窗把檔案框起來給使用者看。加上 NO_WINDOW 會讓它什麼都不發生。
            subprocess.Popen(["explorer", "/select,", str(path)])
            return True
    except (OSError, AttributeError):
        return False
    return False
