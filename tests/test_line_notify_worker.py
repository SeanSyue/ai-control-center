from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.core.line_notify_worker import LineNotifyWorker
from app.services.line_notifier import LineNotifier, LineSendResult
from tests.qt_helpers import running_worker_thread, wait_for_signal


def test_line_notify_worker_emits_completed_with_send_push_result(
    monkeypatch,
) -> None:
    QApplication.instance() or QApplication([])

    monkeypatch.setattr(
        LineNotifier,
        "send_push",
        lambda self, message: LineSendResult(True, f"sent: {message}"),
    )

    worker = LineNotifyWorker(LineNotifier(), "hello")
    completed = []
    worker.completed.connect(completed.append)

    with running_worker_thread(worker):
        wait_for_signal(worker.finished, timeout_ms=5000)

    assert len(completed) == 1
    assert completed[0].success is True
    assert completed[0].message == "sent: hello"


def test_line_notify_worker_does_not_block_gui_thread(monkeypatch) -> None:
    # Proves the actual point of Phase 10: a slow send_push call must not
    # freeze the GUI event loop - a QTimer ticking on the main thread must
    # keep firing while the worker's blocking call is in flight elsewhere.
    application = QApplication.instance() or QApplication([])

    def slow_send_push(self, message):
        time.sleep(0.3)
        return LineSendResult(True, "ok")

    monkeypatch.setattr(LineNotifier, "send_push", slow_send_push)

    worker = LineNotifyWorker(LineNotifier(), "hello")
    completed = []
    worker.completed.connect(completed.append)

    from PySide6.QtCore import QTimer

    tick_count = 0

    def on_tick():
        nonlocal tick_count
        tick_count += 1

    ticker = QTimer()
    ticker.setInterval(20)
    ticker.timeout.connect(on_tick)
    ticker.start()

    with running_worker_thread(worker):
        wait_for_signal(worker.finished, timeout_ms=5000)

    ticker.stop()

    assert len(completed) == 1
    # The 0.3s sleep is well over 10x the 20ms tick interval - if the GUI
    # thread had been blocked instead of freed to pump events, this would
    # be 0 or close to it.
    assert tick_count >= 5
