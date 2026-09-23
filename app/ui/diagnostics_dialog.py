"""The first-run checklist, and the crash message that replaces a traceback.

Both live here because they are the same job: telling a non-developer what went
wrong in words they can act on. A traceback and a missing-CLI silence are the
two ways AICC currently fails without explaining itself.
"""
from __future__ import annotations

from app.i18n import t

import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import ui_preferences
from app.services.startup_diagnostics import Check, Level, blocking, run_diagnostics

SETTINGS_KEY = "diagnostics/skip_when_clean"

MARK = {Level.OK: "✓", Level.BLOCKED: "✕", Level.OFF: "—"}


def _drop_layout(layout) -> None:
    """Take a nested layout apart so the rows can be rebuilt.

    `takeAt` on the parent hands back the layout but leaves its widgets
    parented to the dialog - they stay on screen, stacked behind the new rows.

    `setParent(None)` before `deleteLater()`, and that order matters:
    `deleteLater` is **deferred to the next event loop pass**, so on its own it
    removes nothing at the moment the new rows go in. Measured: two rechecks of
    a one-row dialog left 46 labels alive instead of 4.
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _drop_layout(item.layout())


class DiagnosticsDialog(QDialog):
    """One row per check, with the fix for anything that is wrong."""

    def __init__(self, checks: list[Check], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("diag.title"))
        self.setMinimumWidth(520)
        self._checks = checks

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self._headline = QLabel()
        self._headline.setObjectName("titleLabel")
        layout.addWidget(self._headline)

        # 這幾列會被重畫（指了一個位置之後要立刻重驗），所以放在自己的容器裡
        # 而不是直接塞進對話框的 layout。
        self._rows = QWidget()
        self._rows_layout = QVBoxLayout(self._rows)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(12)
        layout.addWidget(self._rows)

        self.skip_box = QCheckBox(t("diag.skip_when_fine"))
        # Only offered when nothing is wrong. Letting someone permanently
        # dismiss a checklist that is currently failing hides the one thing
        # they need to see.
        self.skip_box.setChecked(True)
        layout.addWidget(self.skip_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        # 掃描是使用者主動觸發的，不是啟動時跑的：`discover_all()` 實測約 4.7 秒，
        # 而啟動檢查全部只有 1.12 秒。放在按鈕後面才不會讓每次開啟都付這個代價。
        self._scan_button = QPushButton(t("diag.scan_machine"))
        self._scan_button.setToolTip(
            t("diag.scan_machine.tip")
        )
        self._scan_button.clicked.connect(self._scan)
        buttons.addButton(self._scan_button, QDialogButtonBox.ActionRole)

        # `_recheck()` 從一開始就在，但**只有「選擇位置…」內部叫得到它**——
        # 也就是說，一個照著畫面指示去終端機把問題修好的人，回到這個對話框
        # 只會看到同一份過期的紅字，而唯一的出路是把整個對話框關掉再開。
        # 接上「帶我去登入」的時候才發現這件事：登入完成之後**需要有地方按**。
        recheck = QPushButton(t("diag.recheck"))
        recheck.setToolTip(t("diag.recheck.tip"))
        recheck.clicked.connect(self._recheck)
        buttons.addButton(recheck, QDialogButtonBox.ActionRole)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._paint()

    def _paint(self) -> None:
        problems = blocking(self._checks)
        self._headline.setText(
            t("diag.has_blockers")
            if problems
            else t("diag.all_ready")
        )
        self.skip_box.setVisible(not problems)

        # `_drop_layout` 已經同時處理 widget 與嵌套 layout，所以這裡直接交給它。
        # 原本這邊自己又寫了一份「是 widget 就刪、是 layout 就遞迴」——而每一列
        # 都是用 `addLayout` 加進來的，那個 widget 分支**一行都跑不到**。
        # 負向驗證是這樣抓到的：把那個分支改壞，12 條破壞裡只有它是綠的。
        _drop_layout(self._rows_layout)
        for check in self._checks:
            self._rows_layout.addLayout(self._row(check))

    def _row(self, check: Check):
        row = QVBoxLayout()
        line = QHBoxLayout()
        mark = QLabel(MARK[check.level])
        mark.setFixedWidth(18)
        line.addWidget(mark)
        line.addWidget(QLabel(f"<b>{check.name}</b>"))
        detail = QLabel(check.detail)
        detail.setObjectName("subtitleLabel")
        line.addWidget(detail, 1)
        row.addLayout(line)
        if check.fix:
            fix = QLabel(check.fix)
            fix.setObjectName("subtitleLabel")
            fix.setWordWrap(True)
            fix.setContentsMargins(24, 0, 0, 0)
            fix.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(fix)
        if check.action:
            row.addLayout(self._action_row(check))
        if check.pick_provider:
            row.addLayout(self._pick_row(check))
        return row

    def _action_row(self, check: Check):
        """「帶我去登入」——`Check.action` 從 2026-08-10 空到 2026-08-24 的那個位置。

        在這之前，沒登入的人在這個對話框裡拿到的是**一句話**：「在終端機執行：
        claude.cmd setup-token」。那句話是對的，但它要求他離開 AICC、找到終端機、
        把一段他看不懂的字打對——而那正是這個對話框存在的理由的反面。

        **AICC 只負責把那支 CLI 開起來。**瀏覽器是它開的、同意是他自己點的、
        憑證進的是它自己的 store——AICC 從頭到尾沒有碰到任何一把 key。
        """
        line = QHBoxLayout()
        line.setContentsMargins(24, 0, 0, 0)
        button = QPushButton(check.action_label or t("common.run"))
        button.clicked.connect(lambda: self._start_action(check))
        line.addWidget(button)
        line.addStretch(1)
        return line

    def _start_action(self, check: Check) -> None:
        from app.services import credential_entry

        route = credential_entry.route_for(check.key)
        if route is None:
            # 走到這裡表示 `Check.action` 有值但 `credential_entry` 不認得它
            # ——兩邊對不上。不猜一個指令跑下去。
            QMessageBox.warning(self, "AICC", f"不知道要怎麼登入 {check.name}。")
            return
        try:
            credential_entry.launch_login(route)
        except credential_entry.CannotStartLogin as exc:
            # **不吞掉。**吞掉的樣子是：他按了按鈕，什麼都沒發生，然後再按一次。
            QMessageBox.warning(self, "AICC", str(exc))
            return
        QMessageBox.information(
            self,
            "AICC",
            f"已經替你開好 {check.name} 的登入視窗。\n\n"
            f"{route.why}\n\n"
            "登入完成後回到這裡按「重新檢查」。",
        )

    def _pick_row(self, check: Check):
        """「選擇位置」——`cli_discovery` 的第四層，終於有入口了。

        上面每一層猜測都可能在沒人預料到的機器上失效（不同安裝方式、可攜版、
        另一個 OS），所以「找不到」必須能變成「那你指給我看」。**沒有這顆按鈕
        的探索功能只是把死路換了個地方**。
        """
        line = QHBoxLayout()
        line.setContentsMargins(24, 0, 0, 0)
        button = QPushButton(t("diag.pick_location"))
        button.clicked.connect(lambda: self._pick(check))
        line.addWidget(button)
        current = self._chosen_label(check.pick_provider)
        if current:
            forget = QPushButton(t("diag.forget_location"))
            forget.clicked.connect(lambda: self._forget(check))
            line.addWidget(forget)
            label = QLabel(current)
            label.setObjectName("subtitleLabel")
            line.addWidget(label, 1)
        else:
            line.addStretch(1)
        return line

    @staticmethod
    def _chosen_label(provider: str) -> str:
        from app.services import cli_discovery

        chosen = cli_discovery.chosen_path(provider)
        return f"目前指定：{chosen}" if chosen else ""

    def _pick(self, check: Check) -> None:
        from app.services import cli_discovery

        chosen, _ = QFileDialog.getOpenFileName(
            self,
            f"選擇 {check.name} 的執行檔",
            "",
            t("diag.executable_filter"),
        )
        if not chosen:
            return
        cli_discovery.remember_chosen_path(check.pick_provider, Path(chosen))
        self._recheck()

    def _forget(self, check: Check) -> None:
        from app.services import cli_discovery

        cli_discovery.clear_chosen_path(check.pick_provider)
        self._recheck()

    def _scan(self) -> None:
        """掃過整台機器，把找到的副本列出來讓使用者挑。

        兩件事刻意這樣做：

        **一次掃兩個 provider。** 使用者有兩支 CLI，缺一支的機器常常兩支都缺，
        所以一次 4.7 秒把兩邊都掃完、所有列一起更新，比分兩次各等 2.4 秒好。

        **掃完沒找到也要講。** 一個按下去沒反應的按鈕，跟一個按下去發現真的
        沒有別的副本，在畫面上長得一模一樣——而後者是有用的資訊（「不是我沒找，
        是真的沒有」）。這是這個專案反覆出現的那一條。
        """
        from app.services import cli_discovery

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            found = cli_discovery.discover_all()
        finally:
            QApplication.restoreOverrideCursor()

        # 已經在 PATH 上的那些不列：它們不是使用者需要挑的東西，而且會把真正
        # 有用的那幾行擠掉。
        options: list[tuple[str, str, Path]] = []
        for provider, candidates in found.items():
            for candidate in candidates:
                if candidate.on_path:
                    continue
                signed = {True: t("diag.signed_in"), False: t("diag.signed_out"), None: t("diag.sign_in_unknown")}[
                    candidate.signed_in
                ]
                options.append(
                    (
                        provider,
                        f"{provider} · {candidate.version} · {signed} · {candidate.path}",
                        candidate.path,
                    )
                )

        if not options:
            QMessageBox.information(
                self,
                t("diag.scan_done_title"),
                t("diag.scan.nothing_else"),
            )
            return

        labels = [label for _, label, _ in options]
        picked, accepted = QInputDialog.getItem(
            self,
            t("diag.scan_done_title"),
            f"找到 {len(options)} 個不在 PATH 上的副本。要讓 AICC 用哪一個？",
            labels,
            0,
            False,
        )
        if not accepted:
            return
        provider, _, path = options[labels.index(picked)]
        cli_discovery.remember_chosen_path(provider, path)
        self._recheck()

    def _recheck(self) -> None:
        """重跑檢查並重畫。

        不只存起來就算了：使用者剛指了一個位置，**唯一能回答「這樣對了嗎」的
        就是再檢查一次**。存完卻讓那一列繼續寫著「找不到指令」，會讓人以為
        自己指錯了，然後再指一次。
        """
        self._checks = run_diagnostics()
        self._paint()

    def remember_choice(self) -> None:
        if self.skip_box.isVisible():
            ui_preferences.store().setValue(
                SETTINGS_KEY, self.skip_box.isChecked()
            )


def should_show(checks: list[Check]) -> bool:
    """Show on the first run, whenever something is wrong, or if asked to.

    A checklist that appears every launch with six ticks on it gets dismissed
    without reading, and then it is not there when it matters.
    """
    if blocking(checks):
        return True
    settings = ui_preferences.store()
    if settings.value(SETTINGS_KEY) is None:
        return True   # never answered: this is the first run
    return not _as_bool(settings.value(SETTINGS_KEY))


def _as_bool(value) -> bool:
    # QSettings hands back the string "true"/"false" on Windows, which is
    # truthy either way if taken at face value.
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def run_startup_checks(parent=None) -> bool:
    """Run the checks and show them if they need showing. Always returns True.

    Nothing here blocks start-up. Someone with no Codex installed can still
    open AICC to read a transcript or edit shared memory, and refusing to
    launch would take that away to enforce a requirement they may not have yet.
    """
    checks = run_diagnostics()
    if should_show(checks):
        dialog = DiagnosticsDialog(checks, parent)
        dialog.exec()
        dialog.remember_choice()
    return True


# ---------------------------------------------------------------------------
# Crash reporting


def crash_log_path() -> Path:
    return Path.home() / "Documents" / "AI Control Center" / "logs" / "crash.log"


def write_crash_log(exc_type, value, tb) -> Path | None:
    path = crash_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n=== {datetime.now().isoformat()} ===\n")
            handle.write("".join(traceback.format_exception(exc_type, value, tb)))
        return path
    except OSError:
        # If even the log cannot be written, the dialog still has to appear -
        # it just says so rather than pointing at a file that is not there.
        return None


class CrashDialog(QDialog):
    """A readable failure, with the traceback available but not shouted."""

    def __init__(self, summary: str, details: str, log_path: Path | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("crash.title"))
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(t("crash.body"))
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        message = QLabel(summary)
        message.setWordWrap(True)
        layout.addWidget(message)

        if log_path is not None:
            where = QLabel(f"完整記錄已寫到：{log_path}")
            where.setObjectName("subtitleLabel")
            where.setWordWrap(True)
            where.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(where)

        self.details = QPlainTextEdit(details)
        self.details.setReadOnly(True)
        self.details.setVisible(False)
        self.details.setMinimumHeight(200)
        layout.addWidget(self.details)

        row = QHBoxLayout()
        self.toggle = QPushButton(t("crash.show_details"))
        self.toggle.setProperty("flatTool", True)
        self.toggle.clicked.connect(self._toggle_details)
        row.addWidget(self.toggle)
        row.addStretch(1)
        close = QPushButton(t("common.close"))
        close.setObjectName("primaryButton")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

    def _toggle_details(self) -> None:
        showing = not self.details.isVisible()
        self.details.setVisible(showing)
        self.toggle.setText(t("crash.hide_details") if showing else t("crash.show_details"))
        self.adjustSize()


def summarise(value: BaseException) -> str:
    """One sentence about what happened, in the user's terms.

    The mapping covers the failures that are actually reachable from the UI.
    Anything else falls through to the exception's own message, which is still
    better than a traceback and does not pretend to know more than it does.
    """
    if isinstance(value, PermissionError):
        return t("crash.reason.permission")
    if isinstance(value, FileNotFoundError):
        return f"找不到需要的檔案或資料夾：{value.filename or '（未指明）'}"
    if isinstance(value, OSError) and getattr(value, "winerror", None) == 112:
        return t("crash.reason.disk_full")
    if isinstance(value, UnicodeDecodeError):
        return t("crash.reason.encoding")
    text = str(value).strip()
    return text or f"發生未預期的錯誤（{type(value).__name__}）。"


def install_crash_handler(app) -> None:
    """Replace Python's default traceback-to-stderr with a dialog.

    A packaged Windows build has no console, so an unhandled exception today is
    completely silent - the window simply stops responding to whatever was
    clicked, with nothing written down anywhere.
    """
    import sys

    previous = sys.excepthook

    def handler(exc_type, value, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, value, tb)
            return
        log_path = write_crash_log(exc_type, value, tb)
        details = "".join(traceback.format_exception(exc_type, value, tb))
        try:
            CrashDialog(summarise(value), details, log_path).exec()
        except Exception:  # pragma: no cover - the dialog itself failed
            previous(exc_type, value, tb)

    sys.excepthook = handler
