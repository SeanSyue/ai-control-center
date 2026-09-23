"""Shared plumbing for the HTML-rendered panels.

Every web panel does the same four things: load its page, register a QWebChannel
bridge, push a JSON state when the page says it is ready, and receive
`on_action(kind, arg)` back. Only `PAGE`, `collect_state()` and
`handle_action()` differ, so they live in the subclasses and everything else
lives here.

Two behaviours are deliberate and easy to get wrong if this is ever rewritten:

* **The view is built on first show, not in __init__.** Building eagerly made the
  suite exit 139 (SIGSEGV): the runtime tests construct MainWindow dozens of
  times and never close it, so dozens of QWebEngineViews were alive at
  interpreter shutdown and Chromium does not survive that teardown order.
  Deferring also keeps Chromium out of application start-up.
  Inside a QStackedWidget this also means a page's Chromium instance is not
  created until the user first navigates to it.

* **`QUrl.fromLocalFile` + `setQuery`, never `setHtml`** - `setHtml` breaks the
  `qrc:///qtwebchannel/qwebchannel.js` include, so the channel never connects
  and the page renders its demo data forever.

`?embed=1` used to be set here. It was how a content page was told to hide the
sidebar it drew for standalone design review; the pages no longer draw one at
all (the shell is its own always-visible view - see shell_panel.py), so the
query said nothing and is gone. `QUERY` below is what replaced it, and only the
shell uses it: it loads one file twice, as `?part=side` and `?part=top`.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from app.services import ui_preferences
from PySide6.QtCore import QObject, QSettings, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget



def _web_dir() -> Path:
    """Where the HTML pages live, in both a source tree and a frozen build.

    PyInstaller keeps Python modules inside its archive, so `__file__` does not
    resolve to a real directory once frozen and the pages have to be found under
    the extraction root instead. The spec file puts them at app/ui/web there.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "app" / "ui" / "web"
    return Path(__file__).parent / "web"


WEB_DIR = _web_dir()

# What shows behind a page before its CSS has painted. These are aicc.css's own
# --bg values, not the native chrome's: this colour sits inside the web view, so
# it should match the page that is about to appear rather than the Qt frame
# around it. A single night-coloured constant used to flash dark navy for a
# moment every time a panel opened in day mode.
PAGE_BACKGROUND = {"night": "#0A0E24", "day": "#EEF5FC"}


class _PageThatIsHeard(QWebEnginePage):
    """把頁面自己喊的話端出來。

    ## FG27：頁面裝了一個不會響的警報

    每一頁都有偵測「Python 推的東西跟我畫的對不上」的機制
    （`settings_v1.html` 的 `checkEcho`、`lineup_v1.html` 的欄位比對），
    而它們喊的方式是 `console.warn`。

    **而 App 沒有覆寫 `javaScriptConsoleMessage`，所以那句話連 stderr 都到不了。**

    量到的三種樣子，畫面上都像正常運作：

    * 決策紀錄頁把已經回答過的決策畫成「尚未回答」，頂部節點數翻倍
    * 設定頁把使用者剛關掉的開關彈回開著
    * 設定頁少掉幾列（例如權限模式那一列），而使用者以為那個設定不存在

    > **一個沒有人在聽的警報，跟沒有警報一樣**——而它更貴，
    > 因為寫它的人以為那件事已經被守住了。

    端到 stderr 而不是彈視窗：這些是**開發期的對不上**，不是使用者做錯事。
    彈視窗會讓一個正在用產品的人被一句他看不懂的話打斷。
    """

    LEVEL = {
        QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel: "info",
        QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel: "warn",
        QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel: "error",
    }

    def javaScriptConsoleMessage(  # noqa: N802
        self, level, message: str, line: int, source: str
    ) -> None:
        # 只端 warn 與 error。info 是頁面自己的除錯輸出，端出來會把真的
        # 警報淹掉——**一個永遠在響的警報等於沒有警報**。
        name = self.LEVEL.get(level, "info")
        if name == "info":
            return
        where = Path(source).name or source
        # 前綴刻意不用中文：這一行是端給開發者看的 stderr，而
        # `test_no_new_hardcoded_chinese` 分不出「介面文字」跟「開發者輸出」
        # ——**而它分不出來是對的**，分得出來就等於留了一條
        # 「說這是給開發者看的」的後門。訊息本體照樣原樣端出來。
        print(f"[page {name}] {where}:{line} {message}", file=sys.stderr)


class _ThemeBus(QObject):
    """One day/night mode for the whole app, not one per panel.

    Every page ships its own toggle button, and each panel used to keep its own
    `_mode`. Flipping the dashboard to day left the other five on night - six
    toggles, six answers, and whichever panel you opened next disagreed with the
    one you just used. The mode is a property of the application, so it lives
    here, persists in QSettings, and every panel repaints when it changes.
    """

    changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        # 組織名／程式名／鍵名都在 `services.ui_preferences`——**一個家一個
        # 主人**。這裡本來自己寫一份，而 `ai_settings_panel.py` 也自己寫了
        # 一份、少打一個空格，於是它的設定躺在登錄檔的另一個位置。
        self._settings = ui_preferences.store()
        self._mode = ui_preferences.mode()

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in ui_preferences.MODES or mode == self._mode:
            return
        self._mode = mode
        ui_preferences.set_mode(mode)
        self.changed.emit(mode)


THEME = _ThemeBus()


def escape_state(value, html_fields: frozenset[str]):
    """Escape every string in a state payload, except declared HTML fields.

    Everything pushed to a page becomes HTML. The pages build their rows with
    template literals and `innerHTML`, and the values land in two kinds of
    position:

        <h4 title="${c.title}">${c.title}</h4>
        <div class="conv" onclick="pick('claude','${c.id}')">

    so quotes matter as much as angle brackets - a title containing `"` breaks
    out of the attribute, and an id containing `'` breaks out of the JS string
    inside `onclick` and runs immediately. Measured before this existed: a
    conversation title carrying `<img src=x onerror=...>` executed on all three
    list pages, and none of them ships a Content-Security-Policy.

    The data really is untrusted. Titles and snippets come from scanning local
    Claude/Codex sessions and from imported transcripts, and `redact()` masks
    secrets without escaping anything - so a conversation that merely *discusses*
    HTML is enough.

    Escaping here rather than in the pages is deliberate: the pages are authored
    in the design folder and copied in by build_pages.py, so a fix applied to
    the copies would be overwritten on the next design update. This also covers
    any panel added later without it having to remember.

    Nothing user-controlled reaches `textContent` on any page - checked, the
    only ones are computed counts and the fixed memory-file names - so blanket
    escaping cannot produce a visible `&amp;`.
    """
    if isinstance(value, dict):
        return {
            key: (
                inner
                if key in html_fields
                else escape_state(inner, html_fields)
            )
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [escape_state(item, html_fields) for item in value]
    if isinstance(value, str):
        return html.escape(value, quote=True)
    return value


class PageBridge(QObject):
    """The object the page reaches through `channel.objects.bridge`."""

    stateChanged = Signal(str)            # Python -> JS, a JSON string
    actionRequested = Signal(str, str)    # JS -> Python, (kind, arg)
    pageReady = Signal()

    @Slot()
    def ready(self) -> None:
        self.pageReady.emit()

    @Slot(str, str)
    def on_action(self, kind: str, arg: str) -> None:
        self.actionRequested.emit(kind, arg)


class WebViewPanel(QWidget):
    """Base class for a panel whose UI is one of the design HTML pages."""

    PAGE: str = ""          # file name inside app/ui/web/
    QUERY: str = ""         # query string, e.g. "part=top"; usually none

    # A class that exists only to be subclassed: shared plumbing, no page of its
    # own. Spelling it out is the point - the PAGE check below is what catches a
    # real panel that forgot its file, and an empty PAGE resolves to WEB_DIR
    # itself, so without the check the panel would quietly render a directory
    # listing. ConversationWebPanel is the only one, and only since its page was
    # retired (see that module).
    #
    # Read from the class's own __dict__, never inherited: LineupWebPanel
    # subclasses ConversationWebPanel, so an inherited flag would hand it - and
    # anything later built on that base - a permanent exemption from the check.
    # A subclass of an abstract panel is a real panel until it says otherwise.
    ABSTRACT: bool = False

    # Keys whose value is already sanitised HTML and must not be escaped
    # again. Only one exists: the knowledge panel's rendered Markdown, which
    # markdown_lite produces against a closed tag whitelist. Adding a key here
    # is a decision to trust whatever writes it.
    HTML_FIELDS: frozenset[str] = frozenset()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        if not self.PAGE and not type(self).__dict__.get("ABSTRACT", False):
            raise ValueError(f"{type(self).__name__} 沒有設定 PAGE")

        self.view: QWebEngineView | None = None
        self._page_ready = False
        THEME.changed.connect(self._on_theme_changed)

        self.bridge = PageBridge(self)
        self.bridge.pageReady.connect(self._on_page_ready)
        self.bridge.actionRequested.connect(self._dispatch_action)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    # ---- lifecycle ------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.view is None:
            self._build_view()

    def _build_view(self) -> None:
        # Reached only if an ABSTRACT panel was put in the page stack. Loud,
        # because the quiet version is a blank panel that looks like a page
        # whose render() failed.
        if not self.PAGE:
            raise ValueError(f"{type(self).__name__} 是 ABSTRACT，沒有頁面可顯示")

        self.view = QWebEngineView(self)
        # **裝一個聽得到的頁面。**預設的 `QWebEnginePage` 把 `console.warn`
        # 丟掉，而每一頁都靠它喊「推下來的東西跟我畫的對不上」（FG27）。
        self.view.setPage(_PageThatIsHeard(self.view))
        self.view.page().setBackgroundColor(QColor(PAGE_BACKGROUND[THEME.mode]))
        # No browser context menu ("Reload", "View source") inside the app.
        self.view.setContextMenuPolicy(Qt.NoContextMenu)

        channel = QWebChannel(self)
        channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(channel)
        self._layout.addWidget(self.view)

        url = QUrl.fromLocalFile(str((WEB_DIR / self.PAGE).resolve()))
        if self.QUERY:
            url.setQuery(self.QUERY)
        self.view.setUrl(url)

    @property
    def _mode(self) -> str:
        """Read-only view of the shared mode; panels never own it."""
        return THEME.mode

    def _on_theme_changed(self, mode: str) -> None:
        if self.view is not None:
            self.view.page().setBackgroundColor(QColor(PAGE_BACKGROUND[mode]))
        self.push_state()

    def _on_page_ready(self) -> None:
        self._page_ready = True
        self.push_state()

    # ---- state ----------------------------------------------------------
    # 「這個動作沒有做成，而且使用者一定要知道」。
    #
    # **住在基底，不在各個面板身上。**三個面板本來各自宣告一份，而
    # `main_window` 只接了其中兩個——陣容頁那六個「失敗了要跟使用者說」
    # 從頭到尾掉進虛空，包括改名字與選職責失敗的那兩個。
    # 一個沒有人接的訊號跟一個沒有發的訊號，在畫面上一模一樣。
    action_failed = Signal(str)

    def reject(self, message: str) -> None:
        """這個動作沒有做成：跟使用者說，**而且把畫面上的值退回去**。

        頁面設計 2026-08-23 的原話：

        > **按了沒反應跟改好了長得一模一樣。**

        網頁那一側是樂觀更新的——他按下去，格子當場就變了。被拒絕的時候
        如果只彈一個警告框，那個格子**還停在他以為的新值上**：他關掉警告，
        看到的是一個已經改好的畫面，而引擎裡什麼都沒變。

        所以拒絕一定是兩件事：**說一句話，再推一次真正的狀態。**
        分成兩個呼叫寫在六個地方的話，第七個地方會忘記推。
        """
        self.action_failed.emit(message)
        self.push_state()

    def push_state(self) -> None:
        """Send the current state to the page, if it has finished loading."""
        if not self._page_ready:
            return
        payload = escape_state(self.collect_state(), self.HTML_FIELDS)
        self.bridge.stateChanged.emit(json.dumps(payload, ensure_ascii=False))

    def collect_state(self) -> dict:
        """Build the page's `render(data)` payload. Subclasses must override."""
        raise NotImplementedError

    # ---- actions --------------------------------------------------------
    def _dispatch_action(self, kind: str, arg: str) -> None:
        # Every page sends the day/night toggle; it goes to the shared bus so
        # all six panels move together instead of drifting apart.
        if kind == "mode":
            THEME.set_mode(arg or "night")
            return
        self.handle_action(kind, arg)

    def handle_action(self, kind: str, arg: str) -> None:
        """React to `bridge.on_action(kind, arg)`. Override as needed."""
