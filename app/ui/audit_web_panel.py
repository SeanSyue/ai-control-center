"""Decision record panel — renders audit_v1.html (ROADMAP P8「稽核日誌」).

The one page in AICC that is not for the person using it. It exists for the
moment someone else asks 「你怎麼證明這個判斷有人看過？」, which is why the
design puts it last in the sidebar behind a divider: rarely opened, and urgent
when it is.

Two rules this panel enforces rather than trusts the page to remember:

* **`blocked` blocks the export, not the view.** Reading your own record on
  your own machine needs no permission; leaving the machine does. The page
  assumed this in `回覆給系統開發-15.md` and it is the right reading.
* **The preview is generated from the same renderer the export writes.** A
  preview produced by a second code path is a preview of something else, and
  the whole point of showing it before a `sensitive_kyc` authorisation is that
  the person sees what they are about to release.
"""
from __future__ import annotations

from app.i18n import t

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from app.services.decision_export import (
    DecisionExportBlocked,
    DecisionExportService,
    ExportAuthorisation,
)
from app.services.long_task import (
    ChainStatus,
    DecisionLogStore,
    DecisionLogUnreadable,
)
from app.services.project_registry import ProjectRegistry
from app.ui.web_panel import WebViewPanel
from app.services import relative_time


def format_waited(asked_at: str, answered_at: str) -> str:
    """How long the run sat waiting, as a string the page prints as-is.

    Formatted here rather than in the page at the design session's request:
    the same reasoning behind `elapsed` and `updated_rel`. Two formatters for
    the same kind of value drift apart, and the one in the browser is the one
    nobody runs tests against.

    A duration, not an age - the five `_relative()` copies across the panels
    all say 「N 分鐘前」, which is a different sentence. Returns "" when either
    end is missing or unparseable; the page then omits the phrase rather than
    printing 「等了 0 分鐘」, which would read as a fact about a wait that was
    never measured.
    """
    try:
        start = datetime.fromisoformat(asked_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(answered_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ""

    seconds = (end - start).total_seconds()
    if seconds < 0:
        # Answered before asked. Clock changes and hand-edited logs can both do
        # this; inventing a duration for it would be worse than saying nothing.
        return ""
    return relative_time.describe_span(seconds)


def format_moment(created_at: str) -> str:
    """`2026-08-08T15:51:16.827981+00:00` → `08-08 23:51`, in local time.

    The page prints `created_at` verbatim, so an unformatted value goes on
    screen exactly as stored - a full ISO string with microseconds and a UTC
    offset, on the page a person is meant to hand to someone else. Formatted
    here for the same reason as `waited`: one set of rules, in the language the
    rest of the app already speaks.

    Unparseable input is returned unchanged rather than blanked: a timestamp we
    cannot read is still evidence of when something happened, and dropping it
    from an audit record to keep the column tidy is the wrong trade.
    """
    try:
        moment = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return created_at or ""
    return moment.astimezone().strftime("%m-%d %H:%M")


def merge_question_and_answer(entries) -> list[dict]:
    """One card per decision, not one per log line.

    The log stores two entries for an answered question - `ASK_USER` when the
    gate fired and `ANSWERED` when the person replied - because the two
    timestamps are both audit facts (the gap between them is how long the run
    sat waiting). The page's contract is one entry per decision: it renders an
    `ASK_USER` with no answer as 「尚未回答」 and anything else with an answer as
    a human card.

    Pushed unmerged, every answered question appeared **twice** - once falsely
    marked as unanswered. On an audit page that is not a cosmetic bug: it
    reports decisions as outstanding that were made, and doubles the count in
    the 「N 個節點」 summary.

    Paired on `(run_id, question)` against the most recent unanswered match, so
    a question asked twice in one run pairs with its own answer rather than the
    earlier one. An `ANSWERED` with nothing to pair to is kept as its own card -
    dropping it would lose a real decision, which is worse than showing it
    without its question.
    """
    out: list[dict] = []
    for entry in entries:
        row = {
            "action": entry.action,
            "question": entry.question,
            "reason": entry.reason,
            "answer": entry.answer,
            "answered_by": entry.answered_by,
            "created_at": format_moment(entry.created_at),
            # When the person replied, kept separate from `created_at` (when
            # the gate fired). The page does not show it yet; the wait between
            # the two is a real audit fact and the data should not lose it.
            "answered_at": (
                format_moment(entry.created_at) if entry.action == "ANSWERED" else ""
            ),
            # The raw values stay available for the wait calculation below and
            # for anything that needs to sort or diff - formatting is for the
            # page, not a replacement for the data.
            "_asked_iso": entry.created_at,
            # Filled in when a question and its answer are merged - an entry
            # that arrives already answered has no measured wait.
            "waited": "",
            "run_id": entry.run_id,
        }
        if entry.action != "ANSWERED":
            out.append(row)
            continue

        for candidate in reversed(out):
            if (
                candidate["action"] == "ASK_USER"
                and not candidate["answer"]
                and candidate["run_id"] == entry.run_id
                and candidate["question"] == entry.question
            ):
                candidate["action"] = "ANSWERED"
                candidate["answer"] = entry.answer
                candidate["answered_by"] = entry.answered_by
                candidate["answered_at"] = format_moment(entry.created_at)
                # From the raw values, not the display strings - 「08-08 23:51」
                # has no year and no timezone, and a wait computed from it
                # would be wrong across a month boundary.
                candidate["waited"] = format_waited(
                    candidate["_asked_iso"], entry.created_at
                )
                break
        else:
            out.append(row)
    return out


class AuditWebPanel(WebViewPanel):
    PAGE = "audit_v1.html"

    def __init__(self, project_path: Path, parent=None) -> None:
        self.project_path = Path(project_path)
        super().__init__(parent)
        self._preview: dict | None = None

    def set_project(self, project_path: Path) -> None:
        self.project_path = Path(project_path)
        self._preview = None
        self.push_state()

    # ---- state -----------------------------------------------------------
    @staticmethod
    def _integrity(status: ChainStatus) -> dict | None:
        """那一格要說什麼，**沒得說的時候回 `None`**。

        頁面的 `integrityHTML()` 只有三格（`bad`／`part`／`ok`），而 `ok`
        是 `else`——所以**一筆紀錄都沒有**的專案拿到的是薄荷綠的
        「全部通過驗證 · 0 / 0 筆已驗證」。那是這個產品的**預設狀態**，
        不是邊角：紀錄檔要等第一次 Decision Gate 才會被建出來。

        零筆的時候沒有任何一句關於「驗證」的話是真的，所以這裡什麼都不說，
        走頁面自己那條 `if(!ig) return ''`。**這不是把事情藏起來**——同一頁
        另外兩處照樣會印「0 個節點」與「這個範圍內沒有紀錄。」，空是看得到
        的；被拿掉的只有那個沒有根據的綠勾勾。

        讀不到的時候一樣不說。**但那一半只做了一半**，要寫在這裡而不是
        只寫在報告裡：讀不到的專案在畫面上仍然長得像「還沒有紀錄」——
        「有三筆但我讀不到」這句話沒有地方可以講。真正的修法是
        `AICC-視覺概念/audit_v1.html` 的 `integrityHTML()` 補第四格
        （rose，字面講「這份紀錄讀不到，不能當稽核依據」），改完跑
        `app/ui/web/build_pages.py`；同一輪要把 `integrity.unreadable`
        推出去（推之前那個欄位會是
        `tests/test_no_field_is_pushed_into_the_void.py` 的孤兒）。
        """
        if status.unreadable or status.nothing_to_verify:
            return None
        return {
            "total": status.total,
            "verified": status.verified,
            "unchained": status.unchained,
            "broken_at": status.broken_at,
        }

    def collect_state(self) -> dict:
        store = DecisionLogStore(self.project_path)
        status = store.verify()
        try:
            entries = merge_question_and_answer(store.list_entries())
        except DecisionLogUnreadable:
            # 讀不到就沒有清單可畫。**不會靜靜地變成綠燈**——`_integrity()`
            # 已經把那一格關掉了，所以畫面不會替一份沒讀到的紀錄背書。
            entries = []
        sensitive = (
            ProjectRegistry.get_sensitivity(self.project_path) == "sensitive_kyc"
        )
        return {
            "mode": self._mode,
            "project_name": self.project_path.name,
            "sensitive": sensitive,
            "entries": entries,
            "integrity": self._integrity(status),
            # Only ever about the export. See the module docstring.
            "blocked": (
                {
                    "reason": (
                        t("audit.kyc_warning")
                    )
                }
                if sensitive
                else None
            ),
            "preview": self._preview,
        }

    # ---- actions ---------------------------------------------------------
    def handle_action(self, kind: str, arg: str) -> None:
        if kind == "export_preview":
            self._build_preview()
        elif kind == "export_confirm":
            self._export(arg)
        elif kind == "export_close":
            self._preview = None
            self.push_state()

    def _refuse(self, unreadable: DecisionLogUnreadable) -> None:
        """紀錄檔讀不到：說一句話，然後把畫面重推一次。

        兩件一起做，理由跟 `WebViewPanel.reject` 一樣——只彈一個警告框的話，
        頁面上那個匯出視窗還停在使用者按下去的那一刻，關掉警告看到的是一個
        「好像可以再按一次」的畫面。

        訊息裡帶 OS 那句原話（含路徑）：讀不到的原因有好幾種——防毒隔離、
        另一個 AICC 視窗開著、資料夾權限——而**能不能自己處理，取決於是哪
        一種**。只說「讀不到」等於把查的工作原封不動退回去。
        """
        QMessageBox.warning(
            self,
            t("audit.export_refused"),
            f"{t('file.unreadable')}\n\n{unreadable}",
        )
        self._preview = None
        self.push_state()

    def _build_preview(self) -> None:
        """Show exactly what the export would write, before anything is written.

        Rendered by the export service itself rather than re-summarised here:
        a preview built by a second code path is a preview of a different
        document, and for a `sensitive_kyc` authorisation that difference is
        the whole safeguard.
        """
        service = DecisionExportService(self.project_path)
        store = DecisionLogStore(self.project_path)
        try:
            entries = store.list_entries()
        except DecisionLogUnreadable as unreadable:
            # 讀不到就沒有預覽——**而且要當場說**。回一個空預覽的話，使用者
            # 看到的是一份空文件加一顆能按的匯出鍵，然後匯出一份空檔案，
            # 而那份空檔案看起來就跟一個乾淨的專案匯出來的一模一樣。
            self._refuse(unreadable)
            return
        markdown = service._render(  # noqa: SLF001 - same object, same document
            entries, store.verify(), t("audit.export_filename")
        )
        stamp = datetime.now().strftime("%Y%m%d")
        base = f"決策紀錄_{self.project_path.name}_{stamp}"
        # Each file gets the unit its own job makes meaningful, rather than a
        # shared 「大小」 column - the design session's answer, and it removes
        # the last placeholder here: both numbers are known before anything is
        # written, so neither is an estimate.
        self._preview = {
            "md": markdown,
            "files": [
                {
                    "name": f"{base}.md",
                    "role": t("audit.for_people"),
                    "amount": f"{len(markdown):,} 字",
                },
                {
                    "name": f"{base}.jsonl",
                    "role": t("audit.for_machines"),
                    "amount": f"{len(entries):,} 筆原始紀錄",
                },
            ],
        }
        self.push_state()

    def _export(self, reason: str) -> None:
        destination = QFileDialog.getExistingDirectory(self, t("audit.pick_destination"))
        if not destination:
            return

        sensitive = (
            ProjectRegistry.get_sensitivity(self.project_path) == "sensitive_kyc"
        )
        authorisation = (
            ExportAuthorisation(reason=reason, acknowledged_contents=True)
            if sensitive
            else None
        )
        # `acknowledged_contents=True` is carried by the flow, not assumed: the
        # page shows the full text first and the button reads 「授權匯出（含原始
        # 問答內容）」. Reaching here means that was on screen and pressed. The
        # service still refuses an empty reason, so an unlabelled release
        # cannot happen from any caller.
        try:
            result = DecisionExportService(self.project_path).export(
                Path(destination), authorisation=authorisation
            )
        except DecisionExportBlocked as blocked:
            QMessageBox.warning(self, t("audit.export_refused"), str(blocked))
            return
        except DecisionLogUnreadable as unreadable:
            # 讀不到的時候**不准匯出**。原本這條路會寫出兩個檔案：一個空的
            # .jsonl（`export()` 拿 `is_file()` 問，而它把權限錯誤也讀成
            # 「沒有這個檔」）＋一份 markdown 寫著「紀錄筆數：0」——**跟一個
            # 乾淨的新專案匯出來的一模一樣**，而那正是要交給別人的那份檔案。
            self._refuse(unreadable)
            return

        self._preview = None
        self.push_state()
        self._report_export(result)

    def _report_export(self, result) -> None:
        """匯出完成的訊息要把完整性檢查的結果講出來。

        `verify()` 在每一次匯出都會跑，結果也寫進了 markdown——但這個對話框
        原本只講檔名和位置。**於是一份雜湊鏈斷掉的紀錄，匯出時看到的是一個
        打勾的「已匯出」**，而使用者要自己去打開檔案才會發現。

        會在意這件事的那一刻，正是「我要把這個檔案交給別人」的那一刻：稽核、
        客戶、律師。雜湊鏈存在的唯一理由就是證明沒有被動過手腳，那個結論
        必須出現在交出去之前，不是藏在交出去的東西裡面。

        斷掉的時候用 `warning` 而不是 `information`：一個綠色打勾旁邊寫著
        「第 12 筆對不上」，兩個訊號互相抵消，而人記得的是那個打勾。

        **零筆是第三種收場，2026-08-24 補的。**原本它落在最後那一行，印出
        「0 筆，0 筆通過完整性驗證」——那是一句**關於驗證的結論**，而根本
        沒有東西被驗過。使用者剛把一份空檔案交出去，而對話框跟他說驗過了。
        """
        where = (
            f"已寫出兩個檔案：\n{result.markdown_path.name}\n{result.jsonl_path.name}\n\n"
            f"位置：{result.markdown_path.parent}"
        )
        if result.broken_at is not None:
            QMessageBox.warning(
                self,
                t("audit.exported_but_unverified"),
                f"{where}\n\n"
                f"**第 {result.broken_at + 1} 筆的指紋對不上。**\n"
                "這份紀錄不能拿來證明內容沒有被改過——交出去之前先確認原因。\n"
                "詳細情形寫在匯出的 .md 檔開頭。",
            )
            return

        if not result.entries:
            # 措辭沿用匯出的 markdown 自己那一句（`export.no_records`）：
            # 同一次匯出的兩個出口對同一件事不該有兩種說法，而那一句
            # 2026-08-10 就已經定案了——只是這個對話框沒有跟上。
            QMessageBox.information(
                self, t("audit.exported"), f"{where}\n\n{t('export.no_records')}"
            )
            return

        detail = f"{result.entries} 筆，{result.verified} 筆通過完整性驗證"
        if result.unchained:
            # 「無法驗證」不是「驗證失敗」。指紋機制上線前寫的紀錄本來就沒有
            # 指紋，把它算成問題會讓每一次匯出都帶著一個永遠不會消失的警告。
            detail += f"，另有 {result.unchained} 筆是指紋機制上線前寫入的，無法驗證"
        QMessageBox.information(self, t("audit.exported"), f"{where}\n\n{detail}。")
