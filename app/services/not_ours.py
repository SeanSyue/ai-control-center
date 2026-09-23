r"""**這幾個資料夾不是這個程式的，一行程式碼都不准刪它們。**

## 為什麼有這個檔案

2026-08-21，一個負向驗證的破壞把 `tests/conftest.py` 的沙箱路徑指到使用者
真實的 `%APPDATA%`。那正是破壞驗證該做的事——把一行指到別的地方，看什麼
會變紅。測試如期變紅了。**然後行程結束，一個 `atexit` 註冊的
`shutil.rmtree` 把使用者整個 roaming 設定檔刪掉了。**

那一次刪掉的東西裡有 `Microsoft\Protect\`——**DPAPI 的主金鑰**。

那個後果四天之後才被看見，而且看起來完全不像「檔案被刪了」：

| 2026-08-25 使用者看到的 | 真正的原因 |
|---|---|
| 桌布設定不見了 | `Themes\TranscodedWallpaper` 8/21 就沒了 |
| Edge 的 Google 要重新登入 | cookie 金鑰是 7/26 用**舊的** DPAPI 主金鑰加密的 |
| Edge 的 Twitch 要重新登入 | 同上 |
| Steam 要重新登入 | 同上 |
| 工作列四個空白圖示 | 開始功能表的捷徑 8/21 就沒了 |

**那些 App 沒有立刻壞，是因為它們一直在跑、金鑰在記憶體裡。**
使用者 8/24 晚上登出、8/25 中午登入，它們重新啟動、必須重新解密，
才一起失敗。

> **判準**：一次刪除的代價**不會在刪除的當下顯現**。
> 「跑完之後看起來一切正常」證明不了任何事。

## 為什麼守衛要在這裡，不在作業系統

Windows 的檔案權限**擋不住使用者自己的帳號**——這個程式跑在他的帳號底下，
他有權限刪自己的東西，所以程式也有。設一個「拒絕自己」的 ACL 會讓 Windows
自己也動不了那些資料夾。

**所以唯一擋得住的地方，是做刪除的那一段程式碼。**而那一段程式碼**會被
負向驗證改壞**——那是它的工作。所以守衛不准信任呼叫端給的路徑，
它要**自己重新推導**一次。

## 這個檔案擋得住什麼、擋不住什麼

**擋得住**：這個 repo 裡任何一個走 `remove_tree()`／`remove_file()` 的刪除，
指到使用者的真實資料夾。也擋得住破壞驗證把某一行的路徑改掉——因為判斷
不是看那個路徑「本來應該是什麼」，是看它**現在指到哪裡**。

**擋不住**：有人繞過這裡直接呼叫 `shutil.rmtree`。那一半由
`tests/test_nothing_deletes_outside_its_own_sandbox.py` 守——它掃整個
repo，找沒有走這裡的刪除呼叫。**兩層缺一不可**：這一支管「攔得住」，
那一支管「沒有人繞過去」。

也擋不住 `subprocess` 跑出去的 `rm`／`Remove-Item`。那是另一個入口，
現在沒有機制，只有規矩。**寫出來是因為只寫擋得住的部分，
下一個人會以為那一塊已經安全了。**
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


class NotOursToDelete(PermissionError):
    """有人要求刪一個不屬於這個程式的東西。

    自成一個型別，因為呼叫端要**看得到**，不是吞掉。而且它繼承
    `PermissionError`：一個到處都寫著 `except OSError` 的 codebase 裡，
    這個例外會被那些 except 接住並記錄，而不是把整個行程打掉。
    """


def _real_home() -> Path:
    """使用者真實的家目錄。

    **不看 `%APPDATA%`**：那個環境變數就是測試會改掉的東西
    （`tests/conftest.py` 把它指到沙箱）。要判斷「這是不是使用者的真東西」，
    必須從一個測試**不會**改的地方推。`USERPROFILE` 同理不可靠，
    所以用 `Path.home()`，而它在 Windows 上讀的是 `USERPROFILE`⋯⋯

    ——所以三個都收，取能取到的：**寧可把太多路徑判成「不是我們的」**，
    也不要漏掉一個真的。誤判的代價是一個暫存資料夾沒被清掉；
    漏判的代價是 2026-08-21。
    """
    for value in (
        os.environ.get("USERPROFILE"),
        os.environ.get("HOME"),
    ):
        if value:
            return Path(value)
    return Path.home()


def protected_roots() -> tuple[Path, ...]:
    """**一行程式碼都不准刪的地方。**

    每一次呼叫都重新算，不在模組層算好——模組層算好的話，
    它會在 import 的當下凍住，而測試會在那之後才改環境變數。
    """
    home = _real_home()
    roots = [
        home,
        home / "AppData",
        home / "AppData" / "Roaming",
        home / "AppData" / "Local",
        home / "Documents",
        home / "Desktop",
        home / "Downloads",
        Path(os.environ.get("ProgramData", r"C:\ProgramData")),
        Path(os.environ.get("SystemRoot", r"C:\Windows")),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    ]
    # 這幾個是那一次真正付出代價的：DPAPI 金鑰、開始功能表、桌布。
    # 明著列出來，因為**它們被刪掉的症狀完全不像檔案被刪**
    # ——四天後才以「要重新登入」的樣子出現。
    roaming = home / "AppData" / "Roaming"
    roots += [
        roaming / "Microsoft",
        roaming / "Microsoft" / "Protect",
        roaming / "Microsoft" / "Crypto",
        roaming / "Microsoft" / "Credentials",
        roaming / "Microsoft" / "Windows",
        roaming / "Microsoft" / "Windows" / "Start Menu",
        roaming / "Microsoft" / "Windows" / "Themes",
    ]
    out: list[Path] = []
    for root in roots:
        try:
            out.append(root.resolve())
        except OSError:
            continue
    return tuple(out)


def why_it_is_not_ours(target: Path | str) -> str:
    """這個路徑不能刪的理由，能刪就回空字串。

    **回一句話不回布林**：呼叫端要把它寫進紀錄，而「False」寫進紀錄
    等於沒寫。
    """
    try:
        path = Path(target).expanduser().resolve()
    except OSError as exc:
        return f"這個路徑算不出來（{exc}），所以不動它"

    for root in protected_roots():
        if path == root:
            return f"「{path}」是使用者的資料夾本身，不是這個程式的東西"
        if root in path.parents and not _inside_our_own_area(path):
            return (
                f"「{path}」在使用者的「{root}」底下，"
                "而它不在這個程式自己的暫存或設定區裡"
            )
    return ""


def _inside_our_own_area(path: Path) -> bool:
    """這個路徑是不是**這個程式自己造出來的**。

    兩種算是自己的：

    * 系統暫存目錄底下（`mkdtemp` 造的那些）
    * `%APPDATA%\\AIControlCenter`——但**只有當 `%APPDATA%` 已經被重導到
      沙箱的時候**。沒有重導的話那是使用者真的設定資料夾，一樣不准刪。

    第二點那個但書是這整個檔案的重點：2026-08-21 那次，路徑**看起來**
    完全合法（它就是 `%APPDATA%`），錯的是 `%APPDATA%` 本身被指到了
    真的地方。
    """
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
    except OSError:
        return False
    if path == temp_root:
        return False
    if temp_root in path.parents:
        return True

    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return False
    try:
        sandbox = Path(appdata).resolve()
    except OSError:
        return False
    # `%APPDATA%` 沒有被重導的話，它就是使用者的真資料夾——不算自己的。
    if not (temp_root in sandbox.parents or sandbox == temp_root):
        return False
    return sandbox == path or sandbox in path.parents


def remove_tree(target: Path | str, *, missing_ok: bool = True) -> None:
    """刪一整個資料夾，**先問這是不是我們的**。

    不是的話丟 `NotOursToDelete`，**不是靜靜地不做**：靜靜不做的話，
    一個指錯地方的呼叫看起來會像成功，而那正好是這個專案最在意的那個病。

    （`tests/conftest.py` 的 `atexit` 那一支刻意相反——它在直譯器收尾時
    跑，那裡丟例外只會變成一段沒有人讀的 traceback，所以那一支選擇
    安靜地不刪。**兩種選擇的判準都是「哪一種比較不會出事」，不是一致性。**）
    """
    reason = why_it_is_not_ours(target)
    if reason:
        raise NotOursToDelete(reason)
    path = Path(target)
    if not path.exists():
        if missing_ok:
            return
        raise FileNotFoundError(str(path))
    shutil.rmtree(path, ignore_errors=True)


def remove_file(target: Path | str, *, missing_ok: bool = True) -> None:
    """刪一個檔案，**先問這是不是我們的**。"""
    reason = why_it_is_not_ours(target)
    if reason:
        raise NotOursToDelete(reason)
    Path(target).unlink(missing_ok=missing_ok)
