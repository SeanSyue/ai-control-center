"""Test helpers for driving real Qt signals/threads.

Stage 1 (see ROADMAP.md P4) rewires MainWindow to run multiple
WorkflowWorker/CloudSyncWorker instances concurrently, each moved to its
own QThread and wired via signals - exactly like MainWindow._launch_worker
already does in production. Prior tests (test_workflow_worker_runtime.py)
call worker.run() directly and synchronously in the test's own thread,
which never exercises Qt's actual cross-thread queued signal delivery,
QThread startup/teardown, or the thread.finished -> deleteLater chain.
Those are precisely the mechanisms Stage 1's correctness depends on, so
they need real coverage, not just synchronous state-machine coverage.

Built entirely on PySide6.QtTest primitives (QSignalSpy, QTest.qWait)
that ship inside the PySide6 dependency this project already has - no
new package added for this.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from PySide6.QtCore import QThread
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

DEFAULT_TIMEOUT_MS = 5000
_POLL_INTERVAL_SECONDS = 0.01


class SignalTimeoutError(AssertionError):
    """Raised when wait_for_signal's deadline passes with no emission."""


def wait_for_signal(
    signal,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    count: int = 1,
) -> QSignalSpy:
    """Block until `signal` has fired `count` time(s), pumping the event
    loop via QApplication.processEvents() in a plain Python poll loop.

    Returns the QSignalSpy - use spy.at(i) for the i-th emission's argument
    list (this PySide6 build's QSignalSpy does not support len()/[] directly,
    only .count()/.at()/.size()). Raises SignalTimeoutError if the deadline
    passes first. Use a generous timeout_ms for anything backed by a real
    QThread - actual OS thread scheduling is involved, not just a
    same-thread call.

    Deliberately NOT implemented with QSignalSpy.wait(): empirically (in
    this PySide6 6.11.1 build) that call blocks for its full timeout
    without releasing the Python GIL, starving a background QThread of
    any Python execution time for the entire wait - a WorkflowWorker run
    that completes in ~0.2s when polled via processEvents()+time.sleep()
    instead stalls for the full timeout and only proceeds once the wait()
    call itself times out and returns control to Python. time.sleep()
    between processEvents() calls does release the GIL, letting the
    worker thread actually run.
    """
    spy = QSignalSpy(signal)
    deadline = time.monotonic() + (timeout_ms / 1000)
    application = QApplication.instance()

    while spy.count() < count:
        if time.monotonic() >= deadline:
            raise SignalTimeoutError(
                f"Timed out after {timeout_ms}ms waiting for {count} "
                f"emission(s) of {signal!r}; got {spy.count()}."
            )
        if application is not None:
            application.processEvents()
        time.sleep(_POLL_INTERVAL_SECONDS)

    return spy


@contextmanager
def running_worker_thread(worker, *, run_slot_name: str = "run"):
    """Move `worker` onto a fresh QThread and start it, mirroring the exact
    wiring MainWindow._launch_worker uses in production: thread.started ->
    worker.run, worker.finished -> thread.quit/deleteLater, thread.finished
    -> thread.deleteLater.

    Always quits and waits for the thread on exit - including when the
    `with` body raises - so a failed assertion never leaks a running
    QThread into the next test.
    """
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(getattr(worker, run_slot_name))
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)

    thread.start()
    try:
        yield thread
    finally:
        thread.quit()
        thread.wait(DEFAULT_TIMEOUT_MS)


def wait_until(
    predicate,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    what: str = "",
) -> None:
    """等到 `predicate()` 成立。**中間一定要 sleep。**

    ## 為什麼不是 `while ...: app.processEvents()`

    那樣寫會當掉整個行程——不是丟例外，是 `abort()`／`Segmentation fault`，
    沒有 traceback、沒有 Qt 訊息。

    2026-08-24 量到的（N9，`test_conversation_search_reaches_python.py`）：

    | 寫法 | 當掉率 |
    |---|---|
    | `while ...: processEvents()`（沒有 sleep）| **8 / 290 ≈ 2.8%** |
    | `QEventLoop` ＋ `QTimer` | 1 / 270 |
    | `processEvents()` ＋ `sleep(0.01)` | **0 / 280** |

    這件事 `wait_for_signal` 的說明裡其實已經寫過一半：

    > `time.sleep()` between `processEvents()` calls does release the GIL,
    > letting the worker thread actually run.

    不放開 GIL 的話，背景執行緒**拿不到任何 Python 執行時間**，而主執行緒
    在那個緊迴圈裡不斷重入 Qt 的事件派送——那正是它死掉的地方。

    ## 這一支在，是因為下一個人會再寫一次那個緊迴圈

    N9 那個緊迴圈是有人自己寫的，而它跟 `wait_for_signal` 只差一行 sleep。
    **同一個 repo 裡一個對的寫法跟一個會當機的寫法並存**，而它們看起來
    一模一樣——所以要有一個叫得出名字的地方。
    """
    deadline = time.monotonic() + (timeout_ms / 1000)
    application = QApplication.instance()

    while not predicate():
        if time.monotonic() >= deadline:
            raise SignalTimeoutError(
                f"等了 {timeout_ms}ms，{what or '那件事'}還是沒有發生。"
            )
        if application is not None:
            application.processEvents()
        # **這一行不是效能調整，是它不會當機的原因。**見上面那張表。
        time.sleep(_POLL_INTERVAL_SECONDS)

    if application is not None:
        application.processEvents()
