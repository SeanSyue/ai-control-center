from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication

from app.core.bound_slot import BoundSlot


class _Worker(QObject):
    done = Signal(str)
    finished = Signal()

    def run(self) -> None:
        self.done.emit("payload")
        self.finished.emit()


def _run_cross_thread(connect_fn) -> dict:
    """Move a worker to a real QThread, let `connect_fn` wire up its `done`
    signal however it likes, and return {"thread_id": ..., "value": ...}
    captured by the receiver - or raise if nothing was received in time.
    """
    application = QApplication.instance() or QApplication([])
    main_thread_id = threading.get_ident()
    captured: dict = {}

    def receiver(value: str) -> None:
        captured["thread_id"] = threading.get_ident()
        captured["value"] = value

    thread = QThread()
    worker = _Worker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    connect_fn(worker, receiver)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    thread.start()
    deadline = time.monotonic() + 5
    while "thread_id" not in captured:
        assert time.monotonic() < deadline, "timed out waiting for signal"
        application.processEvents()
        time.sleep(0.01)

    thread.wait(5000)
    return {**captured, "main_thread_id": main_thread_id}


def test_bound_slot_dispatches_on_receivers_thread_not_emitters() -> None:
    # This is the actual bug Phase 12's manual live test found: a plain
    # functools.partial-wrapped bound method connected to a cross-thread
    # signal is NOT auto-queued by PySide6 (verified against 6.11.1) - it
    # runs synchronously on the EMITTING (worker) thread instead, even with
    # Qt.QueuedConnection forced explicitly. BoundSlot must not have this
    # problem: a genuine bound method of a QObject (BoundSlot.run) IS
    # correctly recognized by PySide6's auto-connection-type detection.
    application = QApplication.instance() or QApplication([])
    holder = QObject()

    def connect_fn(worker, receiver):
        worker.done.connect(BoundSlot(receiver, parent=holder).run)

    result = _run_cross_thread(connect_fn)

    assert result["value"] == "payload"
    assert result["thread_id"] == result["main_thread_id"]

    application.processEvents()


def test_bound_slot_forwards_bound_args_before_signal_args() -> None:
    application = QApplication.instance() or QApplication([])
    holder = QObject()
    captured = []

    def receiver(bound_a, bound_b, signal_value):
        captured.append((bound_a, bound_b, signal_value))

    thread = QThread()
    worker = _Worker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.done.connect(
        BoundSlot(receiver, "project/a", "run-123", parent=holder).run
    )
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    thread.start()
    deadline = time.monotonic() + 5
    while not captured:
        assert time.monotonic() < deadline, "timed out waiting for signal"
        application.processEvents()
        time.sleep(0.01)
    thread.wait(5000)

    assert captured == [("project/a", "run-123", "payload")]

    application.processEvents()


def test_functools_partial_control_case_runs_on_emitters_thread() -> None:
    # Control case, kept as living documentation of the bug BoundSlot
    # exists to work around: functools.partial connected directly to a
    # cross-thread signal dispatches on the WRONG (emitting) thread. If a
    # future PySide6 upgrade ever fixes this, this test - not BoundSlot's
    # own tests above - is the one that will fail, which is the right
    # signal that BoundSlot may no longer be necessary.
    import functools

    application = QApplication.instance() or QApplication([])

    class _Receiver(QObject):
        def __init__(self, sink: dict) -> None:
            super().__init__()
            self._sink = sink

        def on_done(self, bound_arg: str, value: str) -> None:
            self._sink["thread_id"] = threading.get_ident()
            self._sink["value"] = value
            self._sink["bound_arg"] = bound_arg

    main_thread_id = threading.get_ident()
    captured: dict = {}
    receiver = _Receiver(captured)

    thread = QThread()
    worker = _Worker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.done.connect(functools.partial(receiver.on_done, "bound-value"))
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    thread.start()
    deadline = time.monotonic() + 5
    while "thread_id" not in captured:
        assert time.monotonic() < deadline, "timed out waiting for signal"
        application.processEvents()
        time.sleep(0.01)
    thread.wait(5000)

    assert captured["value"] == "payload"
    assert captured["thread_id"] != main_thread_id

    application.processEvents()


def test_every_cross_thread_worker_signal_is_connected_with_bound_slot() -> None:
    """No worker signal may be wired with functools.partial.

    A source-level check because the failure it guards is not visible from
    inside a test: PySide6 (6.11.1) dispatches a partial-bound cross-thread
    connection synchronously on the EMITTING thread, so the handler touches
    GUI objects from a worker thread. That took down the whole process once,
    and 296 automated tests missed it - it was only found by driving the app
    by hand. What can be checked cheaply is that nobody reintroduced the
    spelling.

    Deliberately derived from the file rather than a hard-coded list: a new
    `worker.<signal>.connect(...)` added later is covered the day it is added,
    which a list would not be.
    """
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    connections = re.findall(
        r"worker\.(\w+)\.connect\(\s*([^\n]*)", source
    )
    assert connections, "找不到任何 worker 訊號連線——這個檢查已經失效了"

    for signal_name, first_line in connections:
        # thread.quit/deleteLater are Qt's own slots, already thread-safe and
        # taking no bound arguments - BoundSlot does not apply to them.
        if "deleteLater" in first_line or "quit" in first_line:
            continue
        assert "BoundSlot" in first_line, (
            f"worker.{signal_name} 沒有用 BoundSlot 連線：{first_line.strip()}\n"
            "跨執行緒又帶綁定參數的連線一定要用 BoundSlot，"
            "functools.partial 會在發送端的執行緒上同步執行。"
        )

    # The seat indicator's signal is new (2026-08-09) and crosses the same
    # boundary, so name it: a rename that quietly drops the connection would
    # otherwise pass the loop above by simply having nothing to check.
    assert "active_role_changed" in [name for name, _ in connections]
