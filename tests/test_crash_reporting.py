"""A packaged build has no console, so today an unhandled exception is silent.

The window simply stops responding to whatever was clicked, nothing is written
anywhere, and there is no way for the user to say what happened. These cover
the two halves of fixing that: the traceback reaches a file, and the sentence
the user reads describes the cause rather than naming a Python type.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from app.ui import diagnostics_dialog as dd  # noqa: E402


def _raise(error: BaseException):
    try:
        raise error
    except BaseException as caught:  # noqa: BLE001 - the point is to capture it
        return type(caught), caught, caught.__traceback__


def test_the_traceback_reaches_a_file(tmp_path, monkeypatch) -> None:
    log = tmp_path / "logs" / "crash.log"
    monkeypatch.setattr(dd, "crash_log_path", lambda: log)

    written = dd.write_crash_log(*_raise(ValueError("壞掉了")))

    assert written == log
    text = log.read_text(encoding="utf-8")
    assert "ValueError" in text and "壞掉了" in text


def test_a_second_crash_appends_rather_than_replaces(tmp_path, monkeypatch) -> None:
    # Overwriting would destroy the first failure, which is usually the one
    # that explains the second.
    log = tmp_path / "logs" / "crash.log"
    monkeypatch.setattr(dd, "crash_log_path", lambda: log)

    dd.write_crash_log(*_raise(ValueError("第一次")))
    dd.write_crash_log(*_raise(KeyError("第二次")))

    text = log.read_text(encoding="utf-8")
    assert "第一次" in text and "第二次" in text


def test_an_unwritable_log_does_not_raise(tmp_path, monkeypatch) -> None:
    # The dialog still has to appear. Failing here would turn a recoverable
    # error into a second, invisible one.
    blocked = tmp_path / "a-file"
    blocked.write_text("", encoding="utf-8")
    monkeypatch.setattr(dd, "crash_log_path", lambda: blocked / "logs" / "crash.log")

    assert dd.write_crash_log(*_raise(ValueError("壞掉了"))) is None


def test_a_permission_error_is_explained_in_plain_terms() -> None:
    message = dd.summarise(PermissionError(13, "Permission denied"))

    assert "權限" in message
    assert "PermissionError" not in message
    assert "Traceback" not in message


def test_a_missing_file_names_the_file() -> None:
    error = FileNotFoundError(2, "No such file", r"C:\Users\example\task.md")

    assert r"C:\Users\example\task.md" in dd.summarise(error)


def test_an_unrecognised_error_still_says_something_useful() -> None:
    # No mapping exists for this one, so it falls through to the exception's own
    # message. Still better than a traceback, and it does not pretend to know
    # more than it does.
    assert dd.summarise(RuntimeError("索引重建失敗")) == "索引重建失敗"


def test_an_error_with_no_message_names_its_type() -> None:
    assert "RuntimeError" in dd.summarise(RuntimeError())


def test_the_handler_leaves_keyboard_interrupt_alone(monkeypatch) -> None:
    # Ctrl+C is the developer stopping the app, not a crash. Popping a modal
    # would make it impossible to quit from a terminal.
    import sys

    seen: list[str] = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: seen.append("previous"))
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    dd.install_crash_handler(QApplication.instance())

    sys.excepthook(*_raise(KeyboardInterrupt()))

    assert seen == ["previous"]
