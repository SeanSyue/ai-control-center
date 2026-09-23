r"""畫面偏好住的那個家：Windows 登錄檔 `Sean\AI Control Center`。

## 為什麼要有這一個檔案

2026-08-24 量到 `ai_settings_panel.py` 用的是
`QSettings("Sean", "AIControlCenter")`（**沒有空格**），而 app 裡其他每一處
都是 `"AI Control Center"`。在 Windows 登錄檔裡**那是兩個不同的位置**。

功能上它只影響一個布林值（那個面板有沒有展開），不痛。但它證明了
【一層一個家】那條定案底下那一句：

> 否決：靠約定俗成保持整齊。
> **不變式：沒有那支會變紅的檢查就不算做到。**

**約定俗成撐不過一次打錯字，而打錯字不會有人發現**——那個面板的展開狀態
在另一個登錄檔位置躺了不知道多久，而畫面上完全正常。

所以組織名與程式名寫在這裡，一份。誰要開 `QSettings` 就叫 `store()`。

## 為什麼服務層擺得下一個 QSettings

`QSettings` 是 `QtCore`，不是 `QtWidgets`——它不需要畫面，也不需要
`QApplication`。真正不該發生的是反過來：**系統精靈去 import `app.ui`**
才會把服務層綁在畫面層上。
"""
from __future__ import annotations

from PySide6.QtCore import QSettings

# 這台機器上唯一合法的那一個。改這裡等於搬家，**每一個既有使用者的設定
# 都會在那一刻消失**（舊位置的東西還在，只是沒有人再去讀）。
ORGANISATION = "Sean"
APPLICATION = "AI Control Center"

# 畫面模式。值要跟 `web_panel._ThemeBus` 認得的一樣。
MODE_KEY = "ui/mode"
MODE_NIGHT = "night"
MODE_DAY = "day"
MODES = (MODE_NIGHT, MODE_DAY)
DEFAULT_MODE = MODE_NIGHT


def store() -> QSettings:
    """那個唯一的家。**不要自己 `QSettings(...)`。**"""
    return QSettings(ORGANISATION, APPLICATION)


def mode() -> str:
    """現在是亮還是暗。看不懂的值回預設——**這裡的預設是對的**：

    登錄檔可能被手改、被舊版寫過、被另一台機器的設定同步過來。
    一個看不懂的字串不該讓畫面畫不出來。
    """
    stored = str(store().value(MODE_KEY, DEFAULT_MODE))
    return stored if stored in MODES else DEFAULT_MODE


def set_mode(value: str) -> None:
    if value not in MODES:
        raise ValueError(f"不認得的畫面模式：{value!r}")
    store().setValue(MODE_KEY, value)
