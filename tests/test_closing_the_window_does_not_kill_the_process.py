r"""關掉視窗之後，行程要乾淨地結束——不准當。

## 改壞的樣子

`app.exec()` 回來之後就直接 `return`，沒有人去等還在跑的背景執行緒。
陣容頁的本機對話掃描是一條 `QThread`，它的擁有者是一個模組層的集合
（`app/ui/shutdown.py`），所以**面板死掉、視窗死掉都不會讓它停下來**。
接著 `QApplication` 被解構，Qt 撞到一條還在跑的 QThread，
**直接中止整個行程**——不是丟例外，沒有 traceback，沒有 Qt 訊息。

## 使用者會看到什麼

他走完一整輪五節點測試，按右上角的叉叉。**視窗消失了，然後 Windows 跳出
「python.exe 已停止運作」**，事件檢視器記一筆 `0xC0000409`。
`app.exec() returned 0` 早就印出來了，程式其實從頭到尾做對了每一件事
——但他最後記得的是「這個產品會當」，而那是他對整個產品的最後一個印象。

同一條路在測試這一側的樣子是：`2057 passed` 印完之後行程以 `0xC0000005`
死掉（2026-08-26 量到，離開碼 `-1073741819`）。**全過**，而離開碼是當機
——`NEXT_STEPS.md` 那條「看最後一行，不要看 exit code」一直在替它打圓場。

## 這一支怎麼證明

「關閉不會當」在 pytest 行程**裡面**證不了：真的當的話，當掉的是 pytest
自己，報告根本印不出來，而那看起來像「整套測試消失了」，不像一條失敗。

所以量的是**另一個行程的離開碼**——開一個子行程，把視窗開了又關，
斷言它的離開碼是 0。當掉的行程回的是 `0xC0000409`（Python 這邊看到
`-1073740791`），跟 0 差得很遠，不會有誤判空間。

兩個情境：

* **product**：真的 `MainWindow`，走 `app/main.py` 用的同一支
  `shutdown.run_until_closed()`。最貼近使用者，但它有沒有牙齒要看這台
  機器上有沒有本機對話可掃。
* **contract**：自己起一條**一定還在跑**的背景執行緒，登記進同一個集合。
  這一條不看機器狀態，每次都咬得到。

`test_the_crash_is_still_real` 是這份檔案的自我檢查：把收尾拿掉，那個
子行程**必須**當。它綠不了的那天，上面兩條就不再是證據了。
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"

# 行程被 Qt 中止時 Windows 給的那個碼：`0xC0000409`
# （STATUS_STACK_BUFFER_OVERRUN）。
#
# **同一個崩潰，兩個工具印出來的數字不一樣**，而這一格唯一的用途是被塞進
# 底下那幾句失敗訊息裡，印在 `result.returncode` 旁邊給人對照。所以它必須是
# `subprocess` 那一側的寫法：
#
# * `subprocess.run(...).returncode`（這份檔案量的就是它）——Windows 的
#   exit code 是無號 DWORD，Python 原樣回傳，所以是 **3221226505**。
# * PowerShell 的 `$LASTEXITCODE`、以及大多數文件裡的寫法——當成有號
#   32 位元，所以是 **-1073740791**。同一個數字，同一個崩潰。
#
# 2026-08-26 更正：這裡原本寫的是負的那一個（並附一句「Python 的
# `returncode` 是有號的」——在 Windows 上那句話是錯的，實測 3221226505）。
# 留著的話，下一個看到這條測試變紅的人會讀到「3221226505」配上一句
# 「-1073740791 是 Qt 中止行程的那個碼」，然後以為自己撞到的是**另一種**
# 當法，往錯的方向查。
CRASHED = 3221226505

PROBE = '''
import os
import sys
from pathlib import Path

REPO = sys.argv[1]
PROJECT = sys.argv[2]
SCENARIO = sys.argv[3]
TEARDOWN = sys.argv[4] == "1"
sys.path.insert(0, REPO)

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QWidget

from app.ui import shutdown

seen = {"live": 0, "running": 0}


class SlowWork(QObject):
    """A background job that is guaranteed to still be running at close."""

    finished = Signal()

    @Slot()
    def run(self):
        QThread.msleep(3000)
        self.finished.emit()


def build_product():
    from app.ui.main_window import MainWindow, create_application

    app = create_application()
    return app, MainWindow(Path(PROJECT))


def build_contract():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    thread = QThread()
    worker = SlowWork()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    # The same front door the panels use. Registering through it is what
    # this scenario is measuring.
    shutdown.adopt(thread)
    thread.start()
    window._keep = (thread, worker)
    return app, window


app, window = build_product() if SCENARIO == "product" else build_contract()

tries = {"n": 0}


def close_once_something_is_running():
    live = shutdown.live_threads()
    running = [t for t in live if t.isRunning()]
    tries["n"] += 1
    if running or tries["n"] > 300:
        seen["live"] = len(live)
        seen["running"] = len(running)
        app.quit()
        return
    QTimer.singleShot(10, close_once_something_is_running)


QTimer.singleShot(10, close_once_something_is_running)

if TEARDOWN:
    code = shutdown.run_until_closed(app, window)
else:
    # What app/main.py used to do: show, run, return. Nothing waits.
    window.show()
    code = app.exec()

print("LIVE_AT_CLOSE=%d" % seen["live"])
print("RUNNING_AT_CLOSE=%d" % seen["running"])
print("LEFT_AFTER=%d" % len(shutdown.live_threads()))
sys.exit(code)
'''


def _probe(tmp_path: Path, scenario: str, teardown: bool = True):
    """Open a window in a child process, close it, and report how it died."""
    script = tmp_path / "probe.py"
    script.write_text(PROBE, encoding="utf-8")

    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    appdata = tmp_path / "appdata"
    appdata.mkdir(exist_ok=True)

    env = dict(os.environ)
    # 沒有螢幕也要跑得起來（CI、遠端、以及跑完整套的時候）。
    env["QT_QPA_PLATFORM"] = "offscreen"
    # 子行程不准碰使用者真正的設定，理由見 tests/conftest.py。
    env["APPDATA"] = str(appdata)

    return subprocess.run(
        [
            sys.executable,
            str(script),
            str(REPO),
            str(project),
            scenario,
            "1" if teardown else "0",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env=env,
        cwd=str(REPO),
        # SHOWS_A_WINDOW_ON_PURPOSE 不需要：offscreen 底下沒有視窗，
        # 而這裡本來就是要看一個 GUI 行程怎麼結束的。
        creationflags=(
            subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        ),
    )


def _marker(result, name: str) -> int:
    for line in result.stdout.splitlines():
        if line.startswith(name + "="):
            return int(line.split("=", 1)[1])
    raise AssertionError(
        f"子行程沒有印出 {name}=，代表它在量到之前就死了。\n"
        f"離開碼 {result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-2000:]}\n"
        f"--- stderr ---\n{result.stderr[-2000:]}"
    )


def test_a_background_thread_still_running_does_not_take_the_process_down(
    tmp_path,
) -> None:
    """**這一條是這份檔案的主檢查。**

    一條登記過的背景執行緒還在跑的時候關掉視窗，行程要乾淨地離開。
    """
    result = _probe(tmp_path, "contract")

    assert _marker(result, "RUNNING_AT_CLOSE") >= 1, (
        "關掉的那一刻沒有任何背景執行緒在跑——**這個案例沒有咬到東西**。"
        "它會永遠是綠的，不管收尾在不在。"
    )
    assert result.returncode == 0, (
        f"視窗關掉之後行程以 {result.returncode} 死掉"
        f"（{CRASHED} 是 Qt 撞到一條還在跑的 QThread 時中止行程的那個碼）。\n"
        "使用者看到的是「按下叉叉，然後 Windows 說程式已停止運作」。\n"
        f"--- stdout ---\n{result.stdout[-2000:]}\n"
        f"--- stderr ---\n{result.stderr[-2000:]}"
    )
    assert _marker(result, "LEFT_AFTER") == 0, (
        "收尾跑完了，集合裡卻還留著執行緒。留著的那幾條會活到直譯器收尾，"
        "而那時候 QApplication 已經不在了。"
    )


def test_the_crash_is_still_real(tmp_path) -> None:
    """**自我檢查：把收尾拿掉，它必須當。**

    上面那一條量的是「離開碼是 0」，而在一個根本不會當的環境裡，
    那句話不需要任何修正就會成立——**一條恆真的檢查跟一條沒有的檢查
    長得一模一樣**。

    所以這裡明著走一次沒有收尾的舊路（`show()` → `exec()` → `return`），
    並且要求它真的死。這一條紅了不代表產品壞了，代表**上面那一條不再是
    證據**——可能是 PySide6 換了版本、可能是探針沒把執行緒起起來。
    先查清楚，不要直接把它刪掉。
    """
    result = _probe(tmp_path, "contract", teardown=False)

    assert result.returncode != 0, (
        "沒有收尾的舊路居然乾淨地離開了。這代表這份檔案量不到它要量的東西："
        "上面那條「離開碼是 0」變成恆真。\n"
        "先查是什麼變了（PySide6 版本？探針的執行緒沒起來？），"
        "再決定這幾條要怎麼改——不要直接刪。\n"
        f"--- stdout ---\n{result.stdout[-2000:]}"
    )


def test_closing_the_real_window_leaves_a_clean_exit_code(tmp_path) -> None:
    """使用者真正走的那一條路：真的 `MainWindow`，開了又關。

    這一條用的是 `app/main.py` 呼叫的**同一支** `run_until_closed()`。
    分成兩份寫的那天，測試會綠著而產品照樣當。
    """
    result = _probe(tmp_path, "product")

    assert result.returncode == 0, (
        f"開一個真的 MainWindow 再關掉，行程以 {result.returncode} 死掉"
        f"（{CRASHED} = Qt 中止行程）。\n"
        f"--- stdout ---\n{result.stdout[-2000:]}\n"
        f"--- stderr ---\n{result.stderr[-3000:]}"
    )


def test_main_hands_the_window_to_the_shared_shutdown_path(
    monkeypatch, tmp_path
) -> None:
    """`app/main.py` 不准自己 `show()` + `exec()` 然後就回去。

    子行程那幾條證明的是**收尾這件事有效**；這一條證明**產品真的走那條路**。
    少了它，有人把 `main()` 改回 `return app.exec()`，上面三條全都還是綠的
    ——因為它們跑的是 `run_until_closed()`，而 `main()` 已經不叫它了。
    """
    import app.main as product

    handed: dict = {}

    def fake_run_until_closed(app, window):
        handed["app"] = app
        handed["window"] = window
        return 0

    monkeypatch.setattr(product, "create_application", lambda: "THE-APP")
    monkeypatch.setattr(product, "install_crash_handler", lambda app: None)
    monkeypatch.setattr(product, "run_startup_checks", lambda: True)
    monkeypatch.setattr(product, "ProjectRegistry", lambda: object())
    monkeypatch.setattr(
        product, "resolve_initial_project_path", lambda registry: tmp_path
    )
    monkeypatch.setattr(product, "MainWindow", lambda path: "THE-WINDOW")
    monkeypatch.setattr(product, "run_until_closed", fake_run_until_closed)

    assert product.main() == 0
    assert handed == {"app": "THE-APP", "window": "THE-WINDOW"}, (
        "`main()` 沒有把視窗交給 `app/ui/shutdown.run_until_closed()`。"
        "自己 `show()` + `exec()` 的話，收尾那幾行不會跑，"
        "而使用者按下叉叉之後會看到當機。"
    )


def _parentless_qthread_functions(tree: ast.AST) -> list[ast.FunctionDef]:
    """哪幾支函式裡起了一條**沒有 parent** 的 QThread。

    用語法樹不用字串比對：`QThread(self)` 跟 `QThread()` 差一個字，
    而那個字就是「Qt 會不會替我收掉它」的分水嶺。
    """
    owners: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else ""
            )
            if name != "QThread":
                continue
            if inner.args or inner.keywords:
                continue        # 掛在別人底下，Qt 自己會處理
            owners.append(node)
            break
    return owners


def _calls_adopt(node: ast.AST) -> bool:
    for inner in ast.walk(node):
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "adopt"
        ):
            return True
    return False


def test_a_parentless_qthread_is_registered_where_the_teardown_can_find_it() -> None:
    """起了一條沒有 parent 的 QThread，就要在同一支函式裡登記它。

    這一條守的是**下一個面板**。今天有兩個地方這樣做（對話掃描、憑證檢查），
    兩個都登記了；第三個加進來的時候，忘記登記的樣子跟修好之前一模一樣
    ——程式照跑、畫面照對、測試照綠，只有使用者關掉視窗的那一刻會當。

    「沒有 parent」是刻意的（掛在面板底下的話，面板被丟掉時 Qt 會連著解構
    一條還在跑的執行緒，同樣是中止行程）。代價是**沒有人會自動收它**，
    所以登記不是保險，是那個選擇的另一半。
    """
    missing: list[str] = []

    for source in sorted(APP.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for owner in _parentless_qthread_functions(tree):
            if not _calls_adopt(owner):
                rel = source.relative_to(REPO).as_posix()
                missing.append(f"{rel}:{owner.lineno} {owner.name}()")

    assert not missing, (
        "這幾支起了一條沒有 parent 的 QThread，卻沒有登記進 "
        "`app/ui/shutdown.py`：\n  "
        + "\n  ".join(missing)
        + "\n\n沒有登記的執行緒，關閉的時候沒有人會等它，"
        "而 Qt 撞到一條還在跑的 QThread 會直接中止整個行程。\n"
        "起它之前先 `shutdown.adopt(thread)`，退休時 `shutdown.release(thread)`。"
    )


def test_a_widget_told_to_delete_itself_is_really_gone_after_the_teardown() -> None:
    """`deleteLater()` 只是**排隊**，而排隊跟刪掉長得一模一樣。

    第二種當法就是這個：`close()` + `deleteLater()` + 一百次
    `processEvents()` 之後，那個視窗連同它九個 web 面板**一個都沒被刪**
    ——因為 `processEvents()` 刻意不處理 `DeferredDelete`。它們留到直譯器
    收尾才被刪，而那時候 QtWebEngine 已經在拆自己了：`0xC0000005`。

    畫面一樣、測試報告一樣、`allWidgets()` 的數字一樣，**只有離開碼不一樣**。
    所以這一條先證明「`processEvents()` 真的沒刪它」，再要求收尾把它刪掉。
    """
    import shiboken6
    from PySide6.QtWidgets import QApplication, QWidget

    from app.ui import shutdown

    application = QApplication.instance() or QApplication([])

    widget = QWidget()
    widget.deleteLater()

    application.processEvents()
    assert shiboken6.isValid(widget), (
        "`processEvents()` 居然把它刪掉了——那這一條就不再是證據了。"
        "先查是什麼變了（Qt 版本？）再決定怎麼改，不要直接刪。"
    )

    shutdown.tear_down(application)

    assert not shiboken6.isValid(widget), (
        "收尾跑完了，排隊等著被刪的元件還在。"
        "它會留到直譯器收尾才被刪，而那時候 QApplication 與 QtWebEngine "
        "都已經在拆自己了——量到的是 0xC0000005。"
        "`tear_down()` 要叫 `sendPostedEvents(None, DeferredDelete)`。"
    )


def test_a_thread_that_refuses_to_stop_is_not_reported_as_stopped() -> None:
    """`wait()` 逾時的時候，收尾**不准說自己收乾淨了**。

    這一條守的是 2026-08-26 那個 0xC0000409 的真正病根。當時
    `stop_background_threads()` 不看 `wait()` 的回傳值：逾時了照樣
    `_LIVE.discard(thread)`、照樣把它算進回傳的數字裡。於是

    * `live_threads()` 回空的（測試那一側印出來的 `LEFT_AFTER=0`），
    * `tear_down()` 回一個漂亮的數字，

    而那條執行緒**其實還在跑**——接著 `~QApplication` 撞上去，Qt 直接中止
    整個行程。**「收乾淨了」跟「等不到就放棄了」在那兩個回傳值上一模一樣。**

    為什麼要另外寫這一條，而不是靠上面那幾條子行程測試：修好之後，產品那條
    路的執行緒**每次都停得下來**（掃描會看中斷旗子），所以那個 `if not
    stopped` 分支在真實流程裡永遠走不到——把它改壞，上面每一條都還是綠的。
    一段沒有任何測試走得到的防線，跟沒有那段防線長得一模一樣。

    所以這裡刻意養一條**不理會中斷、就是要跑滿**的執行緒，直接問收尾那兩個
    回傳值誠不誠實。
    """
    from PySide6.QtCore import QObject, QThread, Signal, Slot
    from PySide6.QtWidgets import QApplication

    from app.ui import shutdown

    QApplication.instance() or QApplication([])

    class Stubborn(QObject):
        done = Signal()

        @Slot()
        def run(self) -> None:
            # 刻意不看 `isInterruptionRequested()`：這一條就是要逾時。
            QThread.msleep(1500)
            self.done.emit()

    thread = QThread()
    worker = Stubborn()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.done.connect(thread.quit)
    shutdown.adopt(thread)
    thread.start()

    try:
        assert thread.isRunning(), "探針的執行緒沒起來，這一條量不到東西"

        stopped = shutdown.stop_background_threads(timeout_ms=100)

        assert stopped == 0, (
            "`wait()` 逾時了，收尾卻把它算進「停下來了」的數字裡。"
            "呼叫端看到一個非零的數字，會以為背景都收乾淨了。"
        )
        assert thread in shutdown.live_threads(), (
            "`wait()` 逾時代表那條執行緒**還在跑**，而收尾把它從集合裡丟掉了。"
            "丟掉之後沒有任何人知道它還在——行程會帶著它走到 "
            "`~QApplication`，然後以 0xC0000409 當場消失，"
            "stderr 一個字都沒有。"
        )
    finally:
        thread.quit()
        thread.wait(5000)
        shutdown.release(thread)


def test_the_suite_itself_ends_by_calling_the_shared_teardown() -> None:
    """整套測試跑完之後，pytest 這個行程也要乾淨地離開。

    這件事在 pytest **裡面**只能量到一半：真的當的話，當掉的是 pytest 自己。
    所以這裡量的是那一半——`tests/conftest.py` 的收尾 hook 有沒有真的走
    `app/ui/shutdown.tear_down()`，**兩件事都做**。

    少了它的樣子：`2057 passed` 印完，離開碼 `-1073741819`（0xC0000005）。
    **測試全過而行程當機**，兩者在報告上分不出來——那就是
    `NEXT_STEPS.md` 那條「看最後一行不要看 exit code」的來源。
    """
    from types import SimpleNamespace

    import shiboken6
    from PySide6.QtCore import QObject, QThread, Signal, Slot
    from PySide6.QtWidgets import QApplication, QWidget

    import conftest
    from app.ui import shutdown

    QApplication.instance() or QApplication([])

    class Sleeper(QObject):
        done = Signal()

        @Slot()
        def run(self) -> None:
            QThread.msleep(400)
            self.done.emit()

    thread = QThread()
    worker = Sleeper()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.done.connect(thread.quit)
    shutdown.adopt(thread)
    thread.start()

    doomed = QWidget()
    doomed.deleteLater()

    try:
        assert thread.isRunning(), "探針的執行緒沒起來，這一條量不到東西"

        conftest.pytest_sessionfinish(SimpleNamespace(exitstatus=0), 0)

        assert not thread.isRunning(), (
            "整場結束的 hook 跑完了，背景執行緒還在跑。"
            "它會活到直譯器收尾，而那時候 QApplication 已經不在了"
            "——Qt 撞到一條還在跑的 QThread 會直接中止整個行程。"
            "`tests/conftest.py` 的 `pytest_sessionfinish` 要叫 "
            "`_tear_down_qt()`。"
        )
        assert not shiboken6.isValid(doomed), (
            "整場結束的 hook 跑完了，排隊等著被刪的元件還在。"
            "那一批會留到直譯器收尾才刪，而 QtWebEngine 那時候已經在拆自己了"
            "——量到的是 0xC0000005，而測試報告上寫的是「全過」。"
        )
    finally:
        thread.quit()
        thread.wait(5000)
        shutdown.release(thread)
