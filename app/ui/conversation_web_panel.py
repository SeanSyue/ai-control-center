"""The conversation lists — shared plumbing, no page of its own.

This was the conversation library, rendering conversation_library_v2.html. That
page was retired in ROADMAP P10: the line-up composer took the slot (the
`conversations` key in shell_panel.PAGES became `lineup`), and nothing has
instantiated this class since. The page file has now gone with it. The class
stays because everything below is what the line-up panel assigns *from* -
scanning local Claude and Codex sessions, the per-hit entry shape, and the
binding that writes sessions.json - and because two tests drive it directly:
test_session_binding compares `_bind_selected` against the native panel's, and
test_web_panel_escaping asserts it declares no HTML fields.

`ABSTRACT` is what says the missing PAGE is deliberate rather than forgotten.
Restoring the page means putting the constant back here and the filename back
into build_pages.PAGES; tools/page_wiring.py will then see it as mounted.

Searching and filtering happen in the page (per the handoff), so this pushes the
full list once. Only the top "重新掃描本機" button re-runs
`ConversationSearchService`, because that one really does touch the disk.

`title_source` is passed through as "official" or "local" so the page can badge
a guessed name. A guessed title that looks like a real one is worse than one
that admits it - and the reverse is just as true, which is why `session_index`
counts as official: that title comes from the Claude desktop app's own sidebar
entry and is frequently the string the user typed. Badging it 本機推測名稱
would be the same lie in the other direction.
"""
from __future__ import annotations

from app.i18n import t

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from app.services.conversation_search import ConversationHit, ConversationSearchService
from app.services.session_binding import bind_hits, suggest_project_name
from app.services.session_registry import SessionRegistry
from app.core.bound_slot import BoundSlot
from app.ui import shutdown
from app.ui.web_panel import WebViewPanel
from app.services import relative_time

MAX_PER_PROVIDER = 60


def _relative(iso: str) -> tuple[str, str]:
    if not iso:
        return "", ""
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return "", str(iso)
    seconds = max(0.0, (datetime.now(moment.tzinfo) - moment).total_seconds())
    return relative_time.describe_age(seconds), moment.strftime("%Y-%m-%d %H:%M")


def _short_path(path: str, keep: int = 2) -> str:
    parts = [p for p in (path or "").replace("/", "\\").split("\\") if p]
    return "…\\" + "\\".join(parts[-keep:]) if len(parts) > keep else (path or "")


# 還在跑的掃描執行緒登記在 `app/ui/shutdown.py`。**那個集合是它們唯一的
# 擁有者。**
#
# QThread 如果掛在面板底下，面板被丟掉的時候 Qt 會連著解構它——而解構一條
# 還在跑的 QThread，Qt 會直接以 0xC0000409 中止整個行程，沒有例外、沒有堆疊。
# 測試會大量建立又丟掉面板，所以那不是邊角情況，是每次都會發生。
#
# 不掛在面板底下的話，又會變成 Python 這邊沒有人指著它，跑到一半被回收——
# 同樣的崩潰，換一條路。
#
# 所以：誰都不擁有它，除了那個集合；跑完自己退出。面板死掉時，接收端是
# 掛在面板底下的 BoundSlot，Qt 會自動把連接斷掉，結果就靜靜地被丟棄。
#
# **這裡本來有一份自己的集合**，`credentials_web_panel` 也有一份。
# 兩份都沒有人在關閉的時候去等它們，於是使用者按下叉叉、視窗消失之後，
# 行程以 0xC0000409 死掉——`app.exec()` 明明已經乾淨地回來了。
# 搬到一個共用的地方是為了讓收尾只要問一個地方：見 `app/ui/shutdown.py`。


class ConversationScanWorker(QObject):
    """在背景把本機對話掃一遍。

    2026-08-22 之前 `rescan()` 是同步的，而它會讀使用者**全部**的本機對話檔
    ——這台機器上 28 個，最大那個 50 MB。它跑在畫面的執行緒上，所以讀完之前
    整個視窗是凍的。

    使用者的原話：「從陣容切換到其他頁面再回來的時候，會感覺陣容的頁面載入
    很慢，或者感覺卡住無法載入」。**不是載入慢，是畫面真的被鎖住。**

    結果帶著 `project` 一起送回去。掃到一半使用者切了專案的話，回來的那份
    就是別的專案的清單——而那種錯誤在畫面上看起來完全正常。
    """

    completed = Signal(object)
    finished = Signal()

    def __init__(
        self,
        service: ConversationSearchService,
        project: Path,
        query: str,
        limit: int,
    ) -> None:
        super().__init__()
        self.service = service
        self.project = project
        self.query = query
        self.limit = limit

    @Slot()
    def run(self) -> None:
        # **關視窗的時候，是這個 callable 讓這一趟停得下來。**
        #
        # `stop_background_threads()` 收尾的順序是 `requestInterruption()` →
        # `quit()` → `wait()`。`quit()` 對這條執行緒沒有作用：它正卡在底下那
        # 個讀滿硬碟的 `search()` 裡，事件迴圈沒在轉，`quit()` 只是排隊。所以
        # 真正會被看到的只有這個旗子。
        #
        # 換成 `lambda: False`、或忘記把它傳進 `search()`，這一趟就會固定跑完
        # 約 10 秒，而收尾只等 `timeout_ms`——等不到就放著一條還在跑的執行緒
        # 讓 `~QApplication` 撞上去，Qt 直接中止行程（0xC0000409）。
        thread = QThread.currentThread()

        def should_stop() -> bool:
            return thread is not None and thread.isInterruptionRequested()

        hits: dict[str, list] = {"claude": [], "codex": []}
        for provider in ("claude", "codex"):
            if should_stop():
                break
            # 一欄一個字。`query` 是字串＝兩欄同一個（對話頁那一種），
            # 是 dict＝一欄一個（陣容頁那一種，老闆 2026-09-01：「兩個框」）。
            wanted = (
                self.query.get(provider, "")
                if isinstance(self.query, dict)
                else self.query
            )
            try:
                # `query` 一定要傳。少了它，`score_session()` 那條「比對到
                # 80 則訊息內容」的路整條沒有人走——**而使用者記得一段對話的
                # 方式，通常是裡面談了什麼，不是它那個自動產生的標題**。
                # 實測：搜「開戶」，內容比對找得到 1 則，標題比對 0 則。
                hits[provider] = self.service.search(
                    self.project,
                    provider=provider,
                    query=wanted,
                    limit=self.limit,
                    should_stop=should_stop,
                )
            except Exception:
                # 一邊的本機格式變了，不准把另一邊一起拖下水。
                hits[provider] = []
        # 被中途叫停的那一份是**半套的**——少掉的是還沒讀到的對話，而畫面上
        # 那看起來就只是「這個人本機比較少對話」。送出去的話，使用者看到的
        # 是一份殘缺卻毫無異狀的清單。停下來的唯一時機是關視窗，那時候沒有
        # 人要看結果，所以直接不送。
        if not should_stop():
            self.completed.emit({"project": self.project, "hits": hits})
        self.finished.emit()


class ConversationWebPanel(WebViewPanel):
    # No PAGE. Only ever rendered through a subclass's - see the module
    # docstring.
    ABSTRACT = True

    # Deliberately the same two signals ConversationLibraryPanel exposes, with
    # the same shapes: this panel replaced it in the tab bar, and MainWindow
    # connects them to the same slots. sessions_bound carries nothing because
    # the binding has already happened by the time it fires - it means "the
    # session list on disk changed, go and re-read it".
    sessions_bound = Signal()
    create_project_requested = Signal(str)

    def __init__(self, project_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.service = ConversationSearchService()
        self.registry: SessionRegistry | None = None
        self._project: Path | None = None
        self._hits: dict[str, list[ConversationHit]] = {"claude": [], "codex": []}
        self._selected = {"claude": None, "codex": None}
        self._scanned = False
        # 掃描搬到背景之後才需要的三格：現在有沒有在掃、以及那條執行緒本身。
        # 執行緒要留著參照，不然 Python 這邊沒有人指著它，它會在跑到一半的
        # 時候被回收。
        self._scanning = False
        self._scan_thread: QThread | None = None
        self._scan_worker: ConversationScanWorker | None = None
        # 使用者打的搜尋字。**在這之前它從來沒有離開過瀏覽器**——頁面把它存在
        # 自己的 `DATA.query` 裡、自己過濾標題，Python 收到的永遠是空字串。
        # 見 `rescan()`。
        self._query = ""
        if project_path is not None:
            self.set_project(project_path)

    def set_project(self, project_path: Path) -> None:
        """Record the project; the scan itself waits until the page is shown.

        Scanning walks every local Claude and Codex session on disk. Doing it
        here made MainWindow construction pay for it whether or not anyone ever
        opened this panel - the test suite went from 20s to 43s because it
        builds MainWindow dozens of times.
        """
        self._project = Path(project_path)
        # Rebuilt, not reused: a registry still pointing at the previous project
        # would write the next binding into the wrong .aicc directory.
        self.registry = SessionRegistry(self._project)
        self._selected = {"claude": None, "codex": None}
        self._scanned = False
        self.push_state()

    def _query_for_scan(self) -> str | dict:
        """要拿去搜的字。

        回**字串**＝兩欄同一個（這一頁）。
        回 **dict**＝一欄一個（陣容頁 2026-09-01 起是兩個框，老闆的原話就是
        「兩個框」，而他貼的示意圖本來就是兩欄各一塊）。

        分成一支是為了**不讓那個 dict 漏進這一頁**：這一頁只有一個框，
        推一個 dict 過來的話它會靜靜地兩欄都搜空字串。
        """
        return self._query

    def rescan(self) -> None:
        """開始掃，**立刻回來**。結果之後從背景送回。

        同一時間只跑一條。使用者連按兩次「重新掃描」的話，第二次直接忽略
        ——排隊只會讓他等更久，而畫面上兩次的結果一模一樣。
        """
        if self._project is None:
            return
        self._scanned = True
        if self._scanning:
            return

        self._scanning = True
        self.push_state()   # 先讓頁面顯示「正在找」，不要留白畫面

        thread = QThread()   # 刻意不掛在 self 底下，見 app/ui/shutdown.py
        worker = ConversationScanWorker(
            self.service, self._project, self._query_for_scan(), MAX_PER_PROVIDER
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # 見 app/core/bound_slot.py：PySide6 底下 functools.partial 不能安全地
        # 用在跨執行緒的訊號連接上。
        worker.completed.connect(
            BoundSlot(self._on_scan_done, parent=self).run
        )
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(BoundSlot(self._on_scan_thread_done, parent=self).run)

        # **等它真的離開，不是等它說「我要離開了」。**
        #
        # `QThread.finished` 是在執行緒**還沒完全退出**的時候發出的，而
        # `QThread` 的解構子撞到一個還在跑的執行緒不是丟例外，是 `abort()`
        # ——整個行程當場消失，沒有 traceback、沒有 Qt 訊息。
        #
        # 這裡本來是 `thread.finished.connect(thread.deleteLater)`，也就是
        # 「一聽到它說要走，就排隊拆掉它」。多數時候那個排隊夠久，但不是
        # 每次都夠：2026-08-24 量到單獨跑那個檔案十次會當一次，而死的位置
        # 永遠是**下一次** `processEvents()`——也就是那個延後刪除真正執行的
        # 那一刻。（那就是 N9。）
        #
        # `wait()` 在這裡幾乎不花時間：它已經發過 `finished` 了，就差最後
        # 那幾個指令。給一個上限是因為**卡在這裡會讓畫面凍住**，而那比
        # 晚一點回收一個執行緒物件糟得多。
        def _retire(t=thread) -> None:
            t.wait(2000)
            shutdown.release(t)
            t.deleteLater()

        # 先登記再啟動：啟動之後才登記，中間那一瞬間沒有人擁有它。
        shutdown.adopt(thread)
        thread.finished.connect(_retire)
        self._scan_thread = thread
        self._scan_worker = worker
        try:
            thread.start()
        except Exception:
            # 起不來的話要把旗子放下，否則之後每一次 rescan 都會被上面那個
            # `if self._scanning` 擋掉，而畫面會永遠停在「正在找」。
            shutdown.release(thread)
            self._scanning = False
            self._scan_thread = None
            self._scan_worker = None
            self.push_state()
            raise

    def _on_scan_done(self, payload) -> None:
        """背景掃完了。

        掃到一半使用者切了專案的話，這份結果屬於**上一個**專案——丟掉它。
        用進去的話，畫面會顯示另一個專案的對話清單，而那看起來完全正常。
        """
        if not isinstance(payload, dict):
            return
        if payload.get("project") != self._project:
            return
        hits = payload.get("hits") or {}
        for provider in ("claude", "codex"):
            self._hits[provider] = hits.get(provider) or []

    def showEvent(self, event) -> None:
        """真的被看到才去讀硬碟。

        掃描要走過使用者全部的本機對話（這台機器 28 個檔案，最大 50 MB）。
        在建構的時候做，等於每一個從來沒被打開的面板都付這筆錢。
        """
        super().showEvent(event)
        if not self._scanned:
            self.rescan()

    def hideEvent(self, event) -> None:
        """收起來之前把執行緒收乾淨。

        **QThread 在還跑著的時候被解構，Qt 會直接中止整個行程。**沒有例外、
        沒有堆疊、什麼都不會留下——只有一個 0xC0000409。所以這裡不是保險，
        是必要條件。
        """
        self._stop_scan()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._stop_scan()
        super().closeEvent(event)

    def deleteLater(self) -> None:
        """被刪掉之前也要收掃描。

        `hideEvent` 與 `closeEvent` 收得到「使用者切走」跟「使用者關視窗」，
        **但收不到「這個面板從來沒有被顯示過就被刪掉」**——那兩個事件都不會來。

        測試裡那條路每次都是這樣（建一個面板、跑一次、`deleteLater()`），
        而在產品裡它也存在：視窗關閉的時候子面板是被銷毀的，不是被 close 的。

        收乾淨的代價是最多等 3 秒；不收的代價是一條還在跑的執行緒，
        對著一個正在被拆掉的物件送訊號。
        """
        self._stop_scan()
        super().deleteLater()

    def _stop_scan(self) -> None:
        thread = self._scan_thread
        if thread is None:
            return
        try:
            thread.quit()
            # 有上限的等待：掃描是純讀檔，不會卡住；真的卡住的話，卡在這裡
            # 讓使用者關不掉視窗，比讓它繼續在背景跑更糟。
            thread.wait(3000)
        except RuntimeError:
            # Qt 那一側已經被解構了。沒有東西要收。
            pass

    def _on_scan_thread_done(self) -> None:
        self._scanning = False
        self._scan_thread = None
        self._scan_worker = None
        self.push_state()


    # ---- data -----------------------------------------------------------
    def collect_state(self) -> dict:
        # **這裡不啟動掃描。**
        #
        # 原本是「沒掃過就掃一次」，而 collect_state 會在面板還沒顯示、甚至
        # 在它馬上要被丟掉的時候被呼叫。掃描搬到背景之後，那等於在一個活不
        # 過幾毫秒的物件上起一條執行緒——Qt 會直接以 0xC0000409 中止整個
        # 行程，連 Python 的堆疊都不會留下。2026-08-22 實測過。
        #
        # 啟動的時機改成 `showEvent`：真的被看到才值得去讀硬碟。
        return {
            "mode": self._mode,
            # 頁面靠這一格決定要畫「正在找⋯」還是畫清單。沒有它的話，
            # 掃描期間畫面會顯示一份空清單——而「還沒找完」跟「找不到東西」
            # 在畫面上長得一模一樣。
            "scanning": self._scanning,
            # 推回去讓輸入框在重畫之後還留著字。原本這裡寫死空字串，
            # 而那正好掩蓋了「Python 從來不知道使用者搜了什麼」這件事。
            "query": self._query,
            "project_name": self._project.name if self._project else "",
            "selected": dict(self._selected),
            "claude": [self._entry(h) for h in self._hits["claude"]],
            "codex": [self._entry(h) for h in self._hits["codex"]],
        }

    def _entry(self, hit: ConversationHit) -> dict:
        rel, absolute = _relative(hit.updated_at)
        in_project = False
        if self._project is not None and hit.project_path:
            try:
                in_project = (
                    Path(hit.project_path).resolve() == self._project.resolve()
                )
            except OSError:
                in_project = False
        return {
            "id": hit.session_id,
            "title": hit.display_title or hit.title or hit.session_id[:8],
            # "official" only when the provider gave a real name; anything
            # derived from message text is "local" so the page can say so.
            # session_index is a real name - the desktop app's own sidebar
            # entry, which is what the user reads and often what they typed.
            "title_source": (
                "official"
                if hit.title_source in ("official", "session_index")
                else "local"
            ),
            "source_label": hit.source_label,
            "resume": "resumable" if hit.resume_status == "resumable" else "context",
            "updated_rel": rel,
            "updated_abs": absolute,
            "project_path": _short_path(hit.project_path),
            "in_project": in_project,
            "snippet": hit.snippet or "",
        }

    # ---- actions --------------------------------------------------------
    def handle_action(self, kind: str, arg: str) -> None:
        if kind == "set_query":
            text = str(arg)
            if text == self._query:
                # 同一個字重掃一次要 0.7 秒，而畫面不會有任何不同。
                return
            self._query = text
            self.rescan()
        elif kind == "scan":
            self.rescan()
        elif kind.startswith("select:"):
            provider = kind.split(":", 1)[1]
            if provider in self._selected:
                self._selected[provider] = arg or None
                self.push_state()
        elif kind == "bind":
            self._bind_selected()
        elif kind == "create_project":
            self.create_project_requested.emit(
                suggest_project_name(self._selected_hits())
            )

    def _selected_hits(self) -> tuple[ConversationHit | None, ConversationHit | None]:
        """The chosen conversations themselves, not just their ids.

        The page sends session ids back, but binding needs the whole hit -
        title, source, resume status. They are looked up in the scan results
        rather than re-read from disk, so a selection that predates a rescan
        simply no longer resolves instead of binding stale data.
        """
        found: list[ConversationHit | None] = []
        for provider in ("claude", "codex"):
            wanted = self._selected.get(provider)
            found.append(
                next(
                    (h for h in self._hits[provider] if h.session_id == wanted),
                    None,
                )
                if wanted
                else None
            )
        return found[0], found[1]

    def _bind_selected(self) -> None:
        if self.registry is None or self._project is None:
            return
        hits = self._selected_hits()
        if not any(hits):
            QMessageBox.information(
                self, t("library.nothing_picked.title"), t("library.nothing_picked.body")
            )
            return

        bind_hits(self.registry, self._project, hits)
        self.sessions_bound.emit()
        QMessageBox.information(
            self, t("library.bound.title"), t("library.bound.body")
        )
