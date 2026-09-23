r"""Windows 上叫外部指令，不要彈出黑色主控台視窗。

## 為什麼要有這一個檔案

AICC 是桌面程式，但它做事的方式是去叫命令列工具（`claude`、`codex`、`git`、
`taskkill`）。在 Windows 上，從 GUI 程式啟動一個主控台程式，作業系統會**替它
開一個黑視窗**——除非你明著說不要。

2026-08-22 使用者的原話：「開啟時很慢，會閃過很多 CMD 視窗，這不行，體驗
很差。」查出來是五個啟動點裡只有一個記得加那個旗標，其餘四個漏了。

## 為什麼是常數不是各寫各的

`getattr(subprocess, "CREATE_NO_WINDOW", 0)` 抄四遍，第五個地方就會漏——
**而漏掉的樣子不是報錯，是使用者的畫面上多閃一個黑框**，沒有任何測試會紅。

所以：一個常數、一支守衛（`tests/test_no_black_windows_flash.py`）掃過整個
`app/`，任何一個啟動外部程式的地方沒帶它就變紅。

`getattr` 是因為這個常數只有 Windows 的 `subprocess` 有；在別的系統上它不
存在，取 0 等於「沒有額外旗標」，行為不變。
"""
from __future__ import annotations

import subprocess

# 傳給 `subprocess.Popen` / `subprocess.run` 的 `creationflags`。
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 明著說「這一個就是要開視窗」的地方。守衛看到這個名字就放行。
#
# 目前有兩個：用檔案總管把某個檔案框起來給使用者看——那件事的**全部意義**
# 就是開一個視窗，加上 NO_WINDOW 會讓它變成什麼都沒發生。
SHOWS_A_WINDOW_ON_PURPOSE = "SHOWS_A_WINDOW_ON_PURPOSE"

# 「開一個看得見的主控台，而且那正是重點」。
#
# 憑證入口用它：登入是 OAuth，那支 CLI 會印出一段網址、開瀏覽器、等他貼回
# 一組碼——**整件事都發生在那個視窗裡**。加上 NO_WINDOW 的話，使用者會看到
# 一顆按鈕按下去什麼都沒發生，而登入流程正在一個他看不到的地方等他回答。
#
# 跟 NO_WINDOW 是互斥的，不要兩個一起傳。
NEW_CONSOLE: int = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
