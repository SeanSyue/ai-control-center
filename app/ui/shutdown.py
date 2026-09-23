r"""關掉視窗之後，行程要**乾淨地**結束。

## 使用者看到的那件事

他走完一整輪五節點測試，按右上角的叉叉。視窗消失了，然後
`python.exe` 以 `0xC0000409` 死掉，Windows 把它記成「這個程式當掉了」。
`app/main.py` 已經印完 `app.exec() returned 0`——**跑得完，但他記得的是當機**，
而那是他對整個產品的最後一個印象。

## 病根（2026-08-26 二分法量出來的，不是推測）

`QThread` 在**還跑著**的時候活過 `~QApplication`，Qt 就直接中止整個行程。

量法與結果（`QT_QPA_PLATFORM=offscreen`，PySide6 6.11.1）：

| 做什麼 | 離開碼 |
|---|---|
| 只有 `QApplication` | 0 |
| 一個 `QWebEngineView` 開了又關 | 0 |
| `MainWindow(...)` **不 show** | 0 |
| `MainWindow(...)` **show 了**，1.2 秒後關 | **0xC0000409** |
| 同上，但把 `_build_view` 改成什麼都不做（**一個 QWebEngineView 都沒有**）| **0xC0000409** |
| 同上，但等 4 秒再關（背景掃描自己跑完了）| 0 |
| 同上，1.2 秒就關，但關之前把背景執行緒 `quit()` + `wait()` | 0 |

最後兩列是判決：**跟 QWebEngineView 沒有關係**，是那條背景執行緒。

`w.show()` 會讓 `LineupWebPanel.showEvent` 叫 `rescan()`，起一條掃本機對話的
`QThread`（`app/ui/conversation_web_panel.py`）。那條執行緒的**唯一擁有者是
一個模組層的集合**——刻意的，因為掛在面板底下的話，面板被丟掉時 Qt 會連著
解構一條還在跑的執行緒，同樣是 `0xC0000409`。

但「誰都不擁有它」的代價是：**關閉的路上沒有任何人負責等它**。
`app.exec()` 回來的時候它還在讀硬碟，接著 `QApplication` 被解構，
Qt 中止行程。掃描約 0.7～3 秒，而人按叉叉是在切到陣容頁之後的幾秒內
——所以這不是邊角情況，是**照著走完流程就會發生**。

## 同一個當法的第二半：**等不到就放棄，然後說自己收乾淨了**

上面那張表最後一列（「關之前把背景執行緒 `quit()` + `wait()`」）在真的
`MainWindow` 上**不成立**——2026-08-26 當天稍晚量到的：

| 收尾寫成什麼 | `tear_down()` 花多久 | 收完還在跑的 QThread | 離開碼 |
|---|---|---|---|
| `quit()` + `wait(5000)`（原本）| 5.00 秒（**逾時**）| **1** | **0xC0000409** |
| 同上，但 `wait(30000)` | 5.96 秒（等到了）| 0 | 0 |
| `requestInterruption()` + `quit()` + `wait(5000)`（現在）| **0.21 秒** | 0 | 0 |

兩件事同時錯：

1. **`quit()` 叫不動一條卡在長 slot 裡的執行緒。** 它只是請事件迴圈收工，
   而對話掃描正在讀滿硬碟（這台機器 41 個 session、約 10 秒），事件迴圈
   根本沒在轉。所以 `wait(5000)` 必然逾時。解法是
   `requestInterruption()`，掃描那一側在**每一個 session 之前**看一次旗子
   （`ConversationScanWorker.run()` → `ConversationSearchService.search()`
   的 `should_stop`）。

2. **`wait()` 的回傳值沒有人看。** 逾時了照樣 `_LIVE.discard(thread)`、
   照樣算進回傳的數字裡，於是 `live_threads()` 回空的、`tear_down()` 回一個
   漂亮的數字——**而那條執行緒還在跑**。子行程印出來的
   `LIVE_AT_CLOSE=1 / RUNNING_AT_CLOSE=1 / LEFT_AFTER=0` 三行看起來像
   「抓到了，收掉了」，實際是「抓到了，等不到，騙你說收掉了」。

第 2 點是為什麼這個病躲得掉第一輪診斷：**收乾淨了跟放棄了，在所有回傳值上
一模一樣**，差別只在離開碼。

**不要用「把 `timeout_ms` 調大」解決。** 上表第二列證明那會過——但那只是
把同一刀往後挪到下一台硬碟更慢、對話更多的機器上。

## 第二種當法：**排了隊卻從來沒被送出去的刪除**（`0xC0000005`）

同一天量到的第二件，症狀是完整測試印完 `2057 passed` 之後行程以
`0xC0000005` 死掉。二分法縮到**一條**測試
（`test_main_window_runtime.py::test_on_thread_finished_never_auto_opens_answer_dialog`），
再縮到那條測試裡的一行：

| 收尾寫成什麼 | 收完還活著的元件 | 離開碼 |
|---|---|---|
| `close()` | 294 | 0 |
| `close()` + `deleteLater()` + 100 次 `processEvents()` | **294** | **0xC0000005** |
| 同上，再加一次 `sendPostedEvents(None, DeferredDelete)` | **0** | 0 |

中間那一列是重點：**`processEvents()` 刻意不處理 `DeferredDelete`**，
所以那一百次迴圈一個元件都沒刪到——那個視窗連同它九個 web 面板全部留到
直譯器收尾，而那時候 QtWebEngine 已經在拆自己了。

「刪了」跟「排了隊但沒刪」在畫面上、在測試報告上、在 `allWidgets()` 的
數字上**都一樣**，差別只出現在離開碼。

## 這一支做什麼

1. **背景執行緒集中在這裡登記**（`adopt()` / `release()`）。原本兩個面板
   各自有一份私有集合（`_LIVE_SCANS`、`_LIVE_JOBS`），而收尾的人要記得
   兩個都掃——第三個面板加進來的那天會漏掉一個，而漏掉的樣子跟修好之前
   **一模一樣**。
2. `tear_down(app)`：`app.exec()` 回來之後叫一次。等完還在跑的執行緒，
   **並且把排了隊的刪除真的送出去**。
3. `run_until_closed(app, window)`：產品的收尾路徑就是這一支，
   所以測試可以跑**同一支**，而不是跑一份長得很像的複製品。

`tests/conftest.py` 的 `pytest_sessionfinish` 叫的也是 `tear_down()`
——同一支，不是一份長得很像的。

## 同型的其他位置（2026-08-26 逐一找過）

**「一條 QThread 可能活過拆除」**在這個 repo 出現在五個地方：

| 位置 | 狀態 |
|---|---|
| `conversation_web_panel.rescan()` | **已處理**——改成 `adopt()`／`release()` |
| `credentials_web_panel._start()` | **已處理**——同上 |
| `main_window.py:1024` `QThread(self)`（雲端同步）| **沒動**，見下 |
| `main_window.py:2307` `QThread(self)`（工作流程）| **沒動**，見下 |
| `main_window.py:2841` `QThread(self)`（LINE 通知）| **沒動**，見下 |

後三個掛在 `MainWindow` 底下，所以 Qt 會連著解構它們——**而解構一條還在
跑的 QThread 正是上面那個當法**。`closeEvent` 會問使用者、會去砍 CLI 的
子行程，但**沒有任何一個地方 `wait()`**。

沒動的理由是**還沒量過**：要重現得真的跑一輪工作流程再中途關視窗，
而那需要真的叫 CLI。這一條寫在這裡不是為了放過它，是為了讓下一個人
知道**這裡還有三個同型的洞，只是沒有量到**——「找過了，只有這一處」
跟「還沒找」要分得出來。

## 不做什麼（也是量出來的）

**沒有**逐一去 `deleteLater()` 每一個 `QWebEngineView`。上面第一張表的
第五列說了：一個 web 面板都沒有的時候照樣當；第二張表則說，該刪的東西
早就排好隊了，缺的只是把隊伍送出去。多做一段沒有量到效果的收尾，看起來
像修好了，而下一個人會以為那一段是必要的——**做不到事的收尾跟做得到的
長得一模一樣**。
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent

# 還在跑的背景 QThread。**這個集合是它們唯一的擁有者。**
#
# 為什麼不掛在面板底下：面板被丟掉時 Qt 會連著解構它，而解構一條還在跑的
# QThread 不是丟例外，是 `abort()`——整個行程當場消失，沒有 traceback、
# 沒有 Qt 訊息。測試會大量建立又丟掉面板，所以那不是邊角情況。
#
# 為什麼不是完全不掛：那樣 Python 這邊沒有人指著它，它會在跑到一半的時候
# 被回收，同樣的崩潰換一條路。
#
# 為什麼是**一個**集合而不是每個面板一份：收尾的時候要有一個地方可以問
# 「還有誰在跑」。散成兩份的時候，`tear_down` 要記得兩個都掃，而第三個
# 面板加進來的那天沒有人會記得。
_LIVE: set = set()


def adopt(thread) -> None:
    """登記一條剛要啟動的背景執行緒。**先登記再 `start()`。**

    反過來的話，`start()` 與 `adopt()` 之間有一瞬間誰都不擁有它。
    """
    _LIVE.add(thread)


def release(thread) -> None:
    """這條已經退休了，不用再等它。"""
    _LIVE.discard(thread)


def live_threads() -> list:
    """現在還登記著幾條。**測試靠這一格確定案例真的咬到東西。**

    「關掉的時候沒有背景執行緒在跑」跟「修好了」在離開碼上分不出來，
    所以釘住這件事的測試要先證明它量的那一刻真的有一條在跑。
    """
    return list(_LIVE)


def stop_background_threads(timeout_ms: int = 5000) -> int:
    """把還在跑的等完，回傳**真的停下來**的幾條。

    回的不是「試著等了幾條」：逾時的那幾條不算，而且會留在集合裡。
    兩者的差別就是 0xC0000409 那個離開碼。

    `quit()` 之後一定要 `wait()`：**`QThread.finished` 是在執行緒還沒完全
    退出的時候發出來的**，只聽訊號就往下走，等於在賭那幾個指令跑完了沒。

    有上限的等待，**真的停下來的**才從集合裡放掉——放掉之後 Python 這邊的
    參照消失，C++ 物件在 `QApplication` **還活著的時候**被解構，那是安全的
    順序；留到直譯器收尾才丟的話，`QApplication` 已經走了。

    所以放掉的條件是「它停了」，不是「我等過了」。逾時的那幾條留在集合裡，
    `live_threads()` 才講得出實話。

    `RuntimeError` 要吞：Qt 那一側可能已經解構了（`shiboken` 會這樣告訴
    我們），而那代表沒有東西要等——不是失敗。
    """
    waited = 0
    # 先拍一張快照：`_retire` 會在背景執行緒的脈絡裡動這個集合，
    # 而邊走邊改一個 set 會直接丟 RuntimeError。
    for thread in list(_LIVE):
        try:
            # **`quit()` 之前一定要先 `requestInterruption()`。**
            #
            # `quit()` 只叫得動一條**正在跑事件迴圈**的執行緒。背景工作卡在
            # 一個長 slot 裡面的時候（對話掃描要讀滿硬碟，這台機器約 10 秒），
            # 事件迴圈根本沒在轉，`quit()` 只是排隊等那一趟自己跑完——底下的
            # `wait(timeout_ms)` 於是必然逾時。
            #
            # 拿掉這一行，換來的不是「關得慢一點」：逾時的那條執行緒會活過
            # `~QApplication`，Qt 直接中止行程（0xC0000409）。而 stdout 上
            # 三行標記照印、stderr 一個字都沒有。
            thread.requestInterruption()
            thread.quit()
            stopped = thread.wait(timeout_ms)
        except RuntimeError:
            # Qt 那一側已經解構了，沒有東西要等——不是失敗。
            _LIVE.discard(thread)
            continue
        if not stopped:
            # **這裡不准 `discard()`。**
            #
            # 這條路走到這裡代表 `wait()` 逾時了，也就是那條執行緒**還在跑**。
            # 原本的寫法不看 `wait()` 的回傳值，逾時照樣把它從集合裡丟掉，於是
            # `live_threads()` 回空的、`tear_down()` 回一個漂亮的數字，而行程
            # 接著以 0xC0000409 死掉——**收乾淨了跟放棄了長得一模一樣**，
            # 差別只在離開碼。留著它，至少 `LEFT_AFTER` 會把這件事講出來。
            continue
        _LIVE.discard(thread)
        waited += 1
    return waited


def tear_down(app=None, timeout_ms: int = 5000) -> int:
    """`app.exec()` 回來之後叫這一支，然後才可以讓 `QApplication` 消失。

    兩件事，兩個當法，見模組 docstring 的兩張表。
    """
    waited = stop_background_threads(timeout_ms)
    if app is not None:
        # **這一行不是保險，是第二種當法的解藥。**
        #
        # `deleteLater()` 只是排隊，而 `processEvents()` **刻意不處理**
        # `DeferredDelete`——排了隊沒送出去的話，那些元件會留到直譯器收尾
        # 才被刪，而那時候 `QApplication` 與 QtWebEngine 都已經在拆自己了
        # （量到 `0xC0000005`）。
        #
        # 只有這一支靜態函式送得動那個佇列。拿掉它，`allWidgets()` 從 0
        # 變回 294，而畫面、測試報告、每一條斷言**全都一模一樣**。
        try:
            QCoreApplication.sendPostedEvents(
                None, QEvent.Type.DeferredDelete
            )
            app.processEvents()
        except RuntimeError:
            pass
    return waited


def run_until_closed(app, window) -> int:
    """開視窗、跑到使用者關掉、**收乾淨**，回傳離開碼。

    產品（`app/main.py`）與釘住這件事的測試跑的是**同一支**。分成兩份寫的
    那天，測試會綠著而產品照樣當——因為紅的那一條量的是它自己那一份。
    """
    window.show()
    code = app.exec()
    tear_down(app)
    return code
