"""登入與安裝（`credentials_v1.html`）：這一頁只回答一個問題。

> **我現在按得下「開始執行」嗎？**

掃到幾份、在哪個路徑、哪個版本，預設一律不顯示——那些不幫他做這個決定。
頁面規格與四種狀態的分法在
`交接給PM-2026-08-25-C-憑證入口做完了.md` §二〜§四。

## 這個面板不進側邊欄（2026-08-26 老闆拍板 A）

掛成 `MainWindow` 的一個屬性，跟啟動時那個原生檢查視窗並存（B 也拍了：
原生殼留著）。`tools/page_wiring.py` 認得這個形狀——`shell` 就是這樣掛的
——所以這一頁**既不在側邊欄，也不是孤兒**。

## `state` 是接來的，**這裡不推導**

五種狀態要用結構分（`level` ／ `action` ／ `pick_provider` ／ 登入狀態
問不出來），而那份推導**住在引擎那一側**：`app/services/setup_state.py`，
2026-08-26 另一輪做完的。這個檔案只伸手拿名字，兩種形狀都接：

    app.services.startup_diagnostics.provider_state(check) -> str   ← 現在接的是這個
    Check.state                                            -> str   ← 備援，引擎那側刻意沒做

兩個都拿不到的時候，`collect_state()` **整個 `setup` 都不推**，頁面會照
它自己的空狀態說「還沒有收到這台機器的檢查結果」。

**不是「少推一個欄位」而已，那件事有牙齒**：頁面的結論句數的是
`BLOCKING = ['signed_out','missing','not_runnable']`，一列沒有 `state`
就不算擋人，於是一台一支都沒登入的機器會被寫成**「可以開始了」**——
而這一頁存在的全部理由就是不要編出那一句。所以寧可不畫，也不畫一半。

（同一個理由：**不准在這裡自己寫第二份推導**。這個專案為「同一件事兩份
實作」付過五次學費，而兩份推導漂開的那天，畫面上看起來完全正常。）

## 三件會在做完之後才變紅的事（交接 §六之三）

1. **`collect_state()` 裡不准呼叫 `run_diagnostics()`。** 它同步阻塞
   2.1〜2.5 秒，而 `push_state()` 跑在 GUI 執行緒上；更糟的是
   `WebViewPanel` 把 `THEME.changed` 接到 `_on_theme_changed`，那一支
   **無條件**呼叫 `push_state()`——這一頁只要被打開過一次，之後在任何
   一頁按日夜切換都會再跑一次全部檢查（＝多開四個子行程）。
   所以：狀態存面板欄位、`collect_state` 只讀欄位、慢的事丟背景執行緒。
2. **一段寫死的中文都不能有**（docstring 與註解除外）。介面文字走
   `app/i18n.py` 的 `t()`，而 `t()` 只准在函式裡叫。
3. **同一串中文不准有兩個 key。** 這一頁想講的幾句已經有主人
   （`diag.signed_in` ／ `diag.signed_out` ／ `diag.sign_in_unknown` ／
   `diag.scan_done_title` ／ `diag.scan.nothing_else`），一律重用。

## `CannotStartLogin` 不准吞掉

那個型別的 docstring 自己寫著吞掉的樣子：「他按了按鈕，什麼都沒發生，
然後他再按一次」。這裡走 `reject()`——它一次做兩件事（跟使用者說 ＋
把畫面上樂觀更新的值退回去）。

**而這個面板不經過 `_add_page`**，也就是不經過那個「每一個面板都一定會
經過的路口」。`action_failed` 因此是在 `MainWindow` 裡明著接的
（`_wire_failures`），
`tests/test_the_credentials_page_is_reachable_and_never_invents_a_green_light.py`
釘著這一條。

## AICC 一把 key 都不碰

這個面板**沒有、也不准有**任何一個收得下憑證的入口。
`services/credential_entry.py` 刻意沒有任何函式收得下一把 key，而這裡只是
把使用者送到那支 CLI 自己的登入流程前面就放手。要開 API key 那條路
**先問老闆**（`OPEN_QUESTIONS` ❓10 還開著）。

檔名裡有 `credentials` 是因為它畫的是 `credentials_v1.html`，**不是因為
它碰得到憑證**。守著那個差別的有兩條：這個檔案裡沒有任何一個參數收得下
一把 key（`test_no_function_here_can_even_accept_a_key`，見上面那支測試），
以及頁面那一側「一格輸入欄位都不准有」（`tools/verify_web_pages.py` 的
`CREDENTIALS_PROBE` ①）。

## 「開終端機幫我跑」刻意沒有真的跑

見 `_install()`。下載與安裝在 `SECURITY_MODEL.md` 的 Gate 裡是「要問」那一欄。
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from app.core.bound_slot import BoundSlot
from app.i18n import t
from app.services import cli_discovery
from app.services import credential_entry
from app.services import relative_time
from app.services import startup_diagnostics
from app.ui import shutdown
from app.ui.web_panel import WebViewPanel

# 還在跑的背景工作登記在 `app/ui/shutdown.py`。**那個集合是它們唯一的
# 擁有者。**
#
# 理由與對話掃描那一條同一件事：QThread 掛在面板底下的話，面板被丟掉時
# Qt 會連著解構一條還在跑的執行緒，而那不是例外，是 0xC0000409 直接中止
# 整個行程；完全不掛的話又會被 Python 回收掉。
#
# **這裡本來有一份自己的集合**（`_LIVE_JOBS`），對話面板也有一份。
# 兩份都沒有人在關閉的時候去等它們，於是使用者按下叉叉之後行程以
# 0xC0000409 死掉。集中登記是為了讓收尾只要問一個地方。


class _BackgroundJob(QObject):
    """把一段慢的工作丟到背景，**結果與例外都送回來**。

    例外不吞：吞掉的樣子是使用者按了「重新檢查」，按鈕轉一下就回去了，
    而畫面上的內容一個字都沒變——那跟「檢查完了，一切照舊」長得一模一樣。
    """

    completed = Signal(object)
    finished = Signal()

    def __init__(self, work) -> None:
        super().__init__()
        self._work = work

    @Slot()
    def run(self) -> None:
        try:
            result = self._work()
        except Exception as exc:   # noqa: BLE001 - 送回去畫出來，不是吞掉
            result = exc
        self.completed.emit(result)
        self.finished.emit()


def state_of(check) -> str:
    """這一列在畫面上算哪一種狀態。**這裡不推導，只去接引擎那一側的答案。**

    兩種形狀都接（見模組 docstring）：`startup_diagnostics.provider_state()`
    ——那一支 2026-08-26 做好了，推導在 `app/services/setup_state.py`——
    或者 `Check` 自己帶的 `state` 欄位。**兩個都拿不到就回空字串**，
    而空字串會讓 `collect_state()` 整個 `setup` 都不推
    ——**不畫，而不是畫一半**。
    """
    derive = getattr(startup_diagnostics, "provider_state", None)
    if derive is not None:
        return str(derive(check) or "")
    return str(getattr(check, "state", "") or "")


def row_for(check, state: str) -> dict:
    """一列 provider 的完整資料，欄位照交接 §四。

    `state` 是**傳進來的**，不是在這裡算的——這個函式沒有任何一行知道
    四種狀態怎麼分。

    這裡會叫到 `cli_discovery.searched_locations()`，而它第一次跑會開一個
    `npm prefix -g` 子行程。**所以這支只准在背景執行緒上跑。**
    """
    provider = check.key
    route = credential_entry.route_for(provider)
    return {
        "key": provider,
        "name": check.name,
        "state": state,
        "detail": check.detail,
        "action_label": check.action_label,
        # 可以自己複製去終端機貼的那一行。按鈕壞掉的時候他還有一條路，
        # 而那條路不需要 AICC。
        "typed": route.typed if route is not None else "",
        "why": route.why if route is not None else "",
        "fix": check.fix,
        "install_command": startup_diagnostics.install_command(provider),
        "searched": cli_discovery.searched_locations(provider),
        "how_to_find": cli_discovery.how_to_find_it(provider),
        "can_pick_path": bool(check.pick_provider),
    }


def provider_rows() -> list[dict]:
    """跑一次檢查，把兩支 CLI 那兩列端出來。**這支是阻塞的。**"""
    checks = [
        check
        for check in startup_diagnostics.run_diagnostics()
        if check.key in credential_entry.SUPPORTED
    ]
    return [row_for(check, state_of(check)) for check in checks]


def scan_for_copies() -> list[tuple[str, str, Path]]:
    """把整台機器翻一遍，回**不在 PATH 上**的那幾份。

    已經在 PATH 上的不列：它們不是使用者需要挑的東西，而且會把真正有用
    的那幾行擠掉。這支同樣是阻塞的（實測數秒），只准在背景跑。
    """
    signed_key = {
        True: "diag.signed_in",
        False: "diag.signed_out",
        None: "diag.sign_in_unknown",
    }
    options: list[tuple[str, str, Path]] = []
    for provider, candidates in cli_discovery.discover_all().items():
        for candidate in candidates:
            if candidate.on_path:
                continue
            signed = t(signed_key[candidate.signed_in])
            options.append(
                (
                    provider,
                    f"{provider} · {candidate.version} · {signed} · {candidate.path}",
                    candidate.path,
                )
            )
    return options


class CredentialsWebPanel(WebViewPanel):
    """登入與安裝。掛在 `MainWindow` 上，不進側邊欄。"""

    PAGE = "credentials_v1.html"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 量到的那幾列。**只有這裡存**，`collect_state()` 只讀它。
        self._providers: list[dict] = []
        self._checked_at: float | None = None
        # 兩顆按鈕、兩個成本、**兩個旗標**。合成一個的話，慢的那顆在跑的
        # 時候快的那顆會寫著「檢查中…」——而它根本沒有在跑。
        self._rechecking = False
        self._scanning = False
        self._thread: QThread | None = None
        self._job: _BackgroundJob | None = None

    # ---- lifecycle ------------------------------------------------------
    def showEvent(self, event) -> None:
        """真的被看到才去問這台機器。

        在建構的時候問，等於每一個從來沒被打開的面板都付 2.1 秒與四個
        子行程——而 `MainWindow` 在測試裡被建幾十次。
        """
        super().showEvent(event)
        if self._checked_at is None:
            self.recheck()

    def hideEvent(self, event) -> None:
        self._stop_job()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._stop_job()
        super().closeEvent(event)

    def deleteLater(self) -> None:
        """被刪掉之前也要收背景工作。

        `hideEvent` 與 `closeEvent` 收得到「切走」與「關視窗」，**收不到
        「從來沒有被顯示過就被刪掉」**——而那條路在測試裡每次都走。
        """
        self._stop_job()
        super().deleteLater()

    def _stop_job(self) -> None:
        thread = self._thread
        if thread is None:
            return
        try:
            thread.quit()
            # 有上限的等待：這幾件事都是讀檔與子行程，不會卡住；真的卡住
            # 的話，卡在這裡讓使用者關不掉視窗比讓它在背景跑完更糟。
            thread.wait(3000)
        except RuntimeError:
            # Qt 那一側已經解構了，沒有東西要收。
            pass

    # ---- 背景 ------------------------------------------------------------
    def _start(self, work, done) -> bool:
        """把 `work` 丟到背景，跑完在 GUI 執行緒上叫 `done(結果)`。

        同一時間只跑一件。**已經有一件在跑的時候回 `False`**，呼叫端負責
        把畫面推回真實的旗標——頁面是樂觀更新的，那顆按鈕會自己彈回去，
        而彈回去是誠實的：那一下真的沒有開始。
        """
        if self._thread is not None:
            return False

        thread = QThread()          # 刻意不掛在 self 底下，見 app/ui/shutdown.py
        job = _BackgroundJob(work)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        # 見 `app/core/bound_slot.py`：PySide6 底下 functools.partial 不能
        # 安全地用在跨執行緒的訊號連接上。
        job.completed.connect(BoundSlot(done, parent=self).run)
        job.finished.connect(thread.quit)
        job.finished.connect(job.deleteLater)
        thread.finished.connect(BoundSlot(self._on_job_thread_done, parent=self).run)

        # **等它真的離開，不是等它說「我要離開了」**：`QThread.finished`
        # 在執行緒還沒完全退出時就發出來了，而解構一條還在跑的 QThread
        # 是 `abort()`，不是例外。
        def _retire(t=thread) -> None:
            t.wait(2000)
            shutdown.release(t)
            t.deleteLater()

        shutdown.adopt(thread)      # 先登記再啟動
        thread.finished.connect(_retire)
        self._thread = thread
        self._job = job
        try:
            thread.start()
        except Exception:
            # 起不來就要把旗子放下，否則之後每一次都會被上面那個 `if` 擋掉，
            # 而畫面會永遠停在「檢查中…」。
            shutdown.release(thread)
            self._thread = None
            self._job = None
            self._rechecking = False
            self._scanning = False
            self.push_state()
            raise
        return True

    def _on_job_thread_done(self) -> None:
        self._thread = None
        self._job = None

    # ---- 重新檢查 --------------------------------------------------------
    def recheck(self) -> None:
        """重跑檢查，**立刻回來**。結果之後從背景送回。"""
        if not self._start(provider_rows, self._on_checked):
            self.push_state()
            return
        self._rechecking = True
        self.push_state()

    def _on_checked(self, result) -> None:
        self._rechecking = False
        if isinstance(result, Exception):
            # 檢查本身壞掉的時候**不留著上一輪的答案**：一份過期的清單
            # 讀起來跟一份新鮮的一模一樣。
            self._providers = []
            self._checked_at = None
            self.reject(str(result))
            return
        self._providers = list(result)
        self._checked_at = time.monotonic()
        self.push_state()

    # ---- 資料 ------------------------------------------------------------
    def collect_state(self) -> dict:
        """**只讀欄位。** 這裡不呼叫 `run_diagnostics()`——見模組 docstring。"""
        state = {"mode": self._mode}
        rows = self._providers
        # 一列沒有 `state`，頁面就不會把它算成擋人的，而結論那一句會寫
        # 「可以開始了」。**沒量到就不要畫結論**，這一頁的全部意義在此。
        if not rows or any(not row.get("state") for row in rows):
            return state
        state["setup"] = {
            "scanning": self._scanning,
            "rechecking": self._rechecking,
            "checked_at": self._checked_label(),
            "providers": [dict(row) for row in rows],
        }
        return state

    def _checked_label(self) -> str:
        if self._checked_at is None:
            return ""
        return relative_time.describe_age(
            max(0.0, time.monotonic() - self._checked_at)
        )

    # ---- 動作 ------------------------------------------------------------
    def handle_action(self, kind: str, arg: str) -> None:
        if kind == "setup_recheck":
            self.recheck()
        elif kind == "setup_login":
            self._login(arg)
        elif kind == "setup_install":
            self._install(arg)
        elif kind == "setup_pick_path":
            self._pick_path(arg)
        elif kind == "setup_scan":
            self._scan()

    def _known(self, provider: str) -> str:
        """認得的 provider 才做事，**不認得的說出來**。

        頁面送回來的是它自己畫出去的那個 key，所以正常情況一定認得。
        擋在這裡有兩個作用：不認得的 CLI 不會拿到一句猜出來的指令
        （`credential_entry.route_for` 的同一條理由），而安裝指令那條路
        **不會拿一個沒有人檢查過的字串去組指令**。
        """
        name = (provider or "").strip().lower()
        if name in credential_entry.SUPPORTED:
            return name
        self.reject(t("credential.error.unsupported", provider=provider))
        return ""

    def _login(self, provider: str) -> None:
        """帶他去登入，然後**放手**。AICC 從頭到尾碰不到任何一把 key。"""
        name = self._known(provider)
        if not name:
            return
        route = credential_entry.route_for(name)
        if route is None:
            self.reject(t("credential.error.unsupported", provider=provider))
            return
        try:
            credential_entry.launch_login(route)
        except credential_entry.CannotStartLogin as exc:
            # **不准吞掉。** 吞掉的樣子是：他按了按鈕，什麼都沒發生，
            # 然後他再按一次。
            self.reject(str(exc))

    def _install(self, provider: str) -> None:
        """**AICC 不替他下載、不替他安裝**——說出來，不要靜靜地什麼都不做。

        頁面上那顆按鈕寫著「開終端機幫我跑」，而這裡刻意沒有跑它。三件事
        擋在前面，任何一件都不是我可以自己決定的：

        * `SECURITY_MODEL.md` 的 Gate 把**下載與安裝**列在「要問」那一欄；
        * `tests/test_aicc_runs_nothing_destructive.py` 的動態組指令清單
          （新增一個地方就得有人回頭讀一次安全模型）；
        * `npm install -g` 會從網路抓一個套件裝進使用者的機器——那是這個
          產品到今天為止一次都沒做過的事。

        所以這顆按鈕做的是**把工作明確地退回去**：指令就在它上面那一行
        （`install_command()`，跟啟動檢查講的是同一句），複製、貼上、自己跑。
        走 `reject()` 是因為那一支一次做兩件事：跟他說一句話，再把畫面上
        樂觀更新的值推回去。

        > 要開這條路的話，那是老闆的一句話，不是我的。見交接的待決清單。
        """
        name = self._known(provider)
        if not name:
            return
        self.reject(t("credential.install.do_it_yourself"))

    def _pick_path(self, provider: str) -> None:
        """`cli_discovery` 的第四層：四層猜測全部失敗時，「那你指給我看」。

        沒有這一條，「找不到」就只是把死路換了個位置。
        """
        name = self._known(provider)
        if not name:
            return
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            t("credential.pick.title", name=self._display_name(name)),
            "",
            t("diag.executable_filter"),
        )
        if not chosen:
            return
        cli_discovery.remember_chosen_path(name, Path(chosen))
        # 存完就算了不行：他剛指了一個位置，**唯一能回答「這樣對了嗎」的
        # 就是再檢查一次**。
        self.recheck()

    def _display_name(self, provider: str) -> str:
        for row in self._providers:
            if row.get("key") == provider:
                return str(row.get("name") or provider)
        return provider

    def _scan(self) -> None:
        """把整台機器翻一遍，包含不在 PATH 上的副本。

        選單本身還是原生的（`QInputDialog`），照 2026-08-26 拍板的「由面板
        從 Python 那側開原生對話框」。網頁這一側只負責按鈕、進行中狀態，
        以及掃完之後把新的 providers 推回來。
        """
        if not self._start(scan_for_copies, self._on_scanned):
            self.push_state()
            return
        self._scanning = True
        self.push_state()

    def _on_scanned(self, result) -> None:
        self._scanning = False
        if isinstance(result, Exception):
            self.reject(str(result))
            return
        self.push_state()
        options = list(result)
        if not options:
            # **掃完沒找到也要講。** 一個按下去沒反應的按鈕，跟一個按下去
            # 發現真的沒有別的副本，在畫面上長得一模一樣——而後者是有用的
            # 資訊（「不是我沒找，是真的沒有」）。
            QMessageBox.information(
                self, t("diag.scan_done_title"), t("diag.scan.nothing_else")
            )
            return
        labels = [label for _, label, _ in options]
        picked, accepted = QInputDialog.getItem(
            self,
            t("diag.scan_done_title"),
            t("credential.scan.pick_one", count=len(options)),
            labels,
            0,
            False,
        )
        if not accepted:
            return
        provider, _, path = options[labels.index(picked)]
        cli_discovery.remember_chosen_path(provider, path)
        self.recheck()
