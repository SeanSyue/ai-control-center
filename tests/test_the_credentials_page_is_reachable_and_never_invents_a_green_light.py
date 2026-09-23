"""登入與安裝那一頁：**有人開得了它，而且它不會編一個綠燈出來。**

這一份守四件事，四件的失敗都長得像一個正常畫面。

## ① 頁面在，面板不在（假綠燈的原型）

改壞的樣子：把 `credentials_v1.html` 放進 `app/ui/web/`、加進
`build_pages.PAGES`，但沒有任何面板指名它。
**使用者會看到什麼**：什麼都看不到——那一頁永遠不會出現在畫面上，而
`verify_web_pages` 會把它算進「全部 N 頁通過」。
`conversation_library_v2.html` 就是這樣過了整個 ROADMAP P10。

反過來也一樣：面板在、`CHECKS` 裡沒有那一頁，那一輪的綠燈**沒有看過它**。

## ② 推不出 `state` 的時候畫「可以開始了」

四種狀態的推導住在引擎那一側（另一輪在做）。這裡只接，不推——見
`app/ui/credentials_web_panel.py` 的模組 docstring。

改壞的樣子：接不到推導的時候，照樣把那幾列推出去（只是少一個 `state`）。
**使用者會看到什麼**：頁面的結論句數的是
`BLOCKING = ['signed_out','missing','not_runnable']`，沒有 `state` 的列
一列都不算擋人，於是一台**一支都沒登入**的機器會被寫成
**「可以開始了」**——然後他按下開始執行，在跑到一半的時候死在 CLI 那一步，
而那個訊息藏在執行紀錄裡。

## ③ 「帶我去登入」開不起來卻不講

`CannotStartLogin` 的 docstring 自己寫著吞掉的樣子：
「他按了按鈕，什麼都沒發生，然後他再按一次」。
而這個面板**不經過 `_add_page`**——也就是不經過那個「每個面板都一定會
經過的路口」，所以 `action_failed` 是在 `MainWindow` 裡明著接的。
改壞的樣子：那一行不接。**使用者會看到什麼**：按了按鈕，什麼都沒發生。

## ④ 檢查跑在畫面的執行緒上

`run_diagnostics()` 同步阻塞 2.1〜2.5 秒。改壞的樣子：在
`collect_state()` 裡叫它，或者讓「重新檢查」同步跑完才回來。
**使用者會看到什麼**：整個視窗凍住兩秒半——而且 `THEME.changed` 會
無條件呼叫 `push_state()`，所以這一頁被打開過一次之後，**在任何一頁按
日夜切換都會再凍一次**。
"""
from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.services import credential_entry, startup_diagnostics  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.startup_diagnostics import Check, Level  # noqa: E402
from app.ui import credentials_web_panel  # noqa: E402
from app.ui.credentials_web_panel import CredentialsWebPanel  # noqa: E402

PAGE = "credentials_v1.html"


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _rows(state: str = "signed_out") -> list[dict]:
    """兩列，照 `row_for()` 真的組出來的形狀——不是手寫一份假的。

    手寫一份的話，`row_for()` 裡打錯一個欄位名這裡也看不出來。
    """
    checks = [
        Check("claude", "Claude Code", Level.BLOCKED, "2.1.226，但尚未登入"),
        Check("codex", "Codex CLI", Level.OK, "codex-cli 0.147.0"),
    ]
    return [credentials_web_panel.row_for(check, state) for check in checks]


# --- ① 有人開得了它 -------------------------------------------------------

def test_the_page_is_built_verified_and_opened_by_a_panel() -> None:
    """四個註冊點，少一個就製造一個假綠燈。

    改壞的樣子（任一）：`build_pages.PAGES` 少一行、`verify_web_pages.CHECKS`
    少一筆、`main_window` 少那一行 `self.credentials_panel = ...`、
    或者面板忘了設 `PAGE`。
    """
    from app.ui.web import build_pages
    from tools import page_wiring
    from tools import verify_web_pages

    assert CredentialsWebPanel.PAGE == PAGE, "面板沒有指名它要畫哪一頁"
    assert PAGE in build_pages.PAGES, (
        "設計稿不會被複製進 app/ui/web —— 產品裡沒有這一頁"
    )
    assert PAGE in verify_web_pages.CHECKS, (
        f"{PAGE} 不在 CHECKS 裡：那一輪會「看起來全綠、其實沒驗到這一頁」"
    )

    mounted = page_wiring.mounted_pages()
    assert mounted.get(PAGE) == "self.credentials_panel", (
        f"{PAGE} 沒有掛在 MainWindow 上（現在是 {mounted.get(PAGE)!r}）。"
        "拍板的形狀是掛成一個屬性、不進側邊欄；建在別的地方不算數。"
    )
    assert PAGE not in page_wiring.orphan_pages(), "這一頁沒有面板會開啟它"


# --- ② 推不出 state 就不畫結論 --------------------------------------------

def test_without_the_engine_derivation_the_page_is_told_nothing(
    tmp_path, monkeypatch
) -> None:
    """**接不到推導就整個 `setup` 都不推。**

    改壞的樣子：`collect_state()` 照樣推那幾列，只是少一個 `state`。
    使用者會看到「可以開始了」，而他一支都沒登入。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    _app()
    panel = CredentialsWebPanel()
    try:
        panel._providers = _rows(state="")
        panel._checked_at = 0.0
        assert "setup" not in panel.collect_state(), (
            "沒有 state 的那幾列被推出去了 —— 頁面會把它們算成不擋人，"
            "然後在一台什麼都沒登入的機器上寫「可以開始了」"
        )
    finally:
        panel.deleteLater()


def test_the_state_is_whatever_the_engine_says_it_is(tmp_path, monkeypatch) -> None:
    """`state` 是接來的，**這個檔案裡沒有第二份推導**。

    改壞的樣子：面板自己照 `level` / `action` / `pick_provider` 推一份。
    兩份推導漂開的那天，畫面上看起來完全正常——這個專案為「同一件事兩份
    實作」付過五次學費。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    _app()

    # 引擎那一側的介面：一支叫 `provider_state` 的函式。
    monkeypatch.setattr(
        startup_diagnostics, "provider_state", lambda check: "not_runnable",
        raising=False,
    )
    check = Check("claude", "Claude Code", Level.BLOCKED, "找得到但無法執行")
    assert credentials_web_panel.state_of(check) == "not_runnable"

    # 沒有那支函式的時候，退回去看 `Check` 自己帶的欄位——兩種形狀都接，
    # 而**兩種都沒有就是空字串**，不是猜一個。
    monkeypatch.delattr(startup_diagnostics, "provider_state", raising=False)
    assert credentials_web_panel.state_of(check) == ""
    check.state = "missing"          # type: ignore[attr-defined]
    assert credentials_web_panel.state_of(check) == "missing"


def test_this_file_does_not_derive_the_states_itself() -> None:
    """面板檔案裡不准出現那四個狀態名的判斷。

    只認字面：`signed_out` / `not_runnable` / `missing` 這幾個字只該從引擎
    那一側流過來，不該在這個檔案裡被算出來。
    """
    import ast
    from pathlib import Path

    source = Path("app/ui/credentials_web_panel.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    named = sorted(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and node.value in {"signed_out", "not_runnable", "missing", "ok", "unknown"}
    )
    assert not named, (
        f"面板自己寫了狀態名 {named} —— 那是第二份推導的起點。"
        "推導住在引擎那一側，這裡只接。"
    )


def test_no_function_in_the_panel_can_even_accept_a_key() -> None:
    """**這個檔案的名字有 `credentials`，而它一把都收不下。**

    `services/credential_entry.py` 有一條一模一樣的檢查，理由逐字是
    「收得下就代表某一天會有人存它」。這一條是它在畫面那一側的另一半：
    面板是唯一能把使用者打的字帶進引擎的東西，所以**形狀**要擋在這裡，
    不是靠紀律。

    改壞的樣子：有人為了「方便」加一個 `save_api_key(provider, api_key)`。
    使用者會看到什麼：畫面上多一格輸入框，而 AICC 從「帶路的手」變成
    「保管的人」——`tools/verify_web_pages.py` 的 CREDENTIALS_PROBE ①
    守著頁面那一半（一個 `<input>` 都不准有），這一條守 Python 這一半。
    """
    import ast
    from pathlib import Path

    smells = ("key", "token", "secret", "password", "credential", "auth")
    tree = ast.parse(
        Path("app/ui/credentials_web_panel.py").read_text(encoding="utf-8")
    )
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                names.add(arg.arg)
            for extra in (args.vararg, args.kwarg):
                if extra is not None:
                    names.add(extra.arg)
    # 前提檢查：一個參數都掃不到的話，下面那條是恆真的。
    assert len(names) >= 5, f"參數掃描只找到 {sorted(names)}，這條等於空轉"

    offenders = sorted(
        name for name in names if any(smell in name.lower() for smell in smells)
    )
    assert not offenders, (
        f"憑證入口的面板出現了收得下憑證的參數：{offenders}\n"
        "AICC 是把人送到登入那一步的手，不是保管的人。"
        "真的要開這條路，先去問老闆（OPEN_QUESTIONS ❓10 還開著）。"
    )


def test_aicc_does_not_download_or_install_anything_for_him(
    tmp_path, monkeypatch
) -> None:
    """「開終端機幫我跑」**不准變成 AICC 替他跑 `npm install -g`**。

    `SECURITY_MODEL.md` 的 Gate 把下載與安裝列在「要問」那一欄，而這件事
    今天還沒有問過。所以那顆按鈕做的是把工作明確地退回去——**說一句話**，
    不是靜靜地什麼都不做。

    改壞的樣子：在這裡開一個子行程把安裝指令跑起來。
    使用者會看到什麼：一個終端機視窗自己冒出來，開始從網路抓東西裝進他的
    機器——而他按的那顆按鈕上一次做的事只是「開瀏覽器讓你自己同意」。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    _app()

    import subprocess

    launched: list[object] = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: launched.append(a) or None
    )

    panel = CredentialsWebPanel()
    said: list[str] = []
    panel.action_failed.connect(said.append)
    try:
        panel.handle_action("setup_install", "claude")
        assert not launched, "AICC 自己去跑安裝指令了 —— 那件事還沒有問過"
        assert said, "按了「開終端機幫我跑」，畫面上什麼都沒發生"
    finally:
        panel.deleteLater()


# --- ③ 失敗要看得見 -------------------------------------------------------

def test_a_login_that_cannot_start_is_not_swallowed(tmp_path, monkeypatch) -> None:
    """`CannotStartLogin` 要走 `reject()`：說一句話 ＋ 把畫面上的值退回去。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    _app()

    def explode(route):
        raise credential_entry.CannotStartLogin("claude 啟動失敗")

    monkeypatch.setattr(credential_entry, "launch_login", explode)

    panel = CredentialsWebPanel()
    said: list[str] = []
    pushed: list[int] = []
    panel.action_failed.connect(said.append)
    monkeypatch.setattr(
        type(panel), "push_state", lambda self: pushed.append(1), raising=False
    )
    try:
        panel.handle_action("setup_login", "claude")
        assert said == ["claude 啟動失敗"], "開不起來卻沒有跟使用者說"
        assert pushed, "說了，但沒有把畫面上樂觀更新的值退回去"
    finally:
        panel.deleteLater()


def test_an_unknown_cli_gets_nothing_rather_than_a_guess(
    tmp_path, monkeypatch
) -> None:
    """頁面送回一個不認得的 provider，不准猜一個指令出來。

    改壞的樣子：直接把它交給 `install_command()`，於是終端機裡跑的是
    `npm install -g gemini` —— 一句 AICC 自己編出來的指令。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    _app()
    started: list[object] = []
    monkeypatch.setattr(
        credential_entry, "launch_login", lambda route: started.append(route)
    )

    panel = CredentialsWebPanel()
    said: list[str] = []
    panel.action_failed.connect(said.append)
    try:
        panel.handle_action("setup_login", "gemini")
        assert not started, "不認得的 CLI 也被拿去啟動了"
        assert said, "不認得的 CLI 被靜靜地忽略了 —— 按了沒反應"
    finally:
        panel.deleteLater()


def test_the_failure_reaches_the_user_in_the_real_window(
    tmp_path, monkeypatch
) -> None:
    """在真的組起來的視窗裡發一次，看使用者收不收得到。

    這個面板**不進 `_add_page`**，所以它不會自動接上那個路口。
    改壞的樣子：`main_window` 裡少那一行 `_wire_failures(...)`。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = _app()

    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)

    from app.ui.main_window import MainWindow

    window = MainWindow(project)
    try:
        seen: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *args, **kwargs: seen.append(args[-1])
        )
        window.credentials_panel.action_failed.emit("開不起來")
        assert seen == ["開不起來"], (
            "憑證入口的失敗訊息掉進虛空了 —— 它不經過 `_add_page`，"
            "所以要自己接"
        )
    finally:
        window.deleteLater()
        application.processEvents()


# --- ④ 不准凍住畫面 -------------------------------------------------------

def test_collecting_the_state_never_runs_the_checks(tmp_path, monkeypatch) -> None:
    """`collect_state()` 只讀欄位。

    改壞的樣子：在裡面叫 `run_diagnostics()`。使用者會看到的不只是打開這
    一頁時卡兩秒半——`THEME.changed` 無條件呼叫 `push_state()`，所以之後
    **在任何一頁按日夜切換都會再跑一次全部七項檢查**。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    _app()

    def forbidden() -> list:
        raise AssertionError("collect_state() 跑了整套檢查 —— 畫面會凍住")

    monkeypatch.setattr(startup_diagnostics, "run_diagnostics", forbidden)

    panel = CredentialsWebPanel()
    try:
        panel._providers = _rows()
        panel._checked_at = 0.0
        state = panel.collect_state()
        assert state["setup"]["providers"], "前提檢查：這一輪根本沒有東西可推"
    finally:
        panel.deleteLater()


def test_the_recheck_happens_off_the_interface_thread(
    tmp_path, monkeypatch
) -> None:
    """「重新檢查」要**立刻回來**，工作在別條執行緒上做完。

    改壞的樣子：`_start()` 直接把工作跑完再回來。使用者會看到整個視窗
    凍住約一秒——而按下去沒反應的按鈕會被按第二次。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = _app()

    main_thread = threading.get_ident()
    ran_on: list[int] = []

    def work() -> list[dict]:
        ran_on.append(threading.get_ident())
        return _rows()

    monkeypatch.setattr(credentials_web_panel, "provider_rows", work)

    panel = CredentialsWebPanel()
    try:
        panel.recheck()
        assert panel._rechecking, (
            "`recheck()` 回來的時候旗子沒有立起來 —— 頁面不會顯示「檢查中…」"
        )
        assert not ran_on, "檢查在呼叫的當下就跑完了 —— 那條路上畫面是凍的"

        import time

        deadline = time.time() + 10
        while time.time() < deadline and panel._rechecking:
            application.processEvents()
            time.sleep(0.01)

        assert ran_on, "背景那條從來沒有跑起來"
        assert ran_on[0] != main_thread, "檢查跑在畫面的執行緒上"
        assert panel._providers, "跑完了，結果卻沒有回到面板上"
    finally:
        panel.deleteLater()
        application.processEvents()
