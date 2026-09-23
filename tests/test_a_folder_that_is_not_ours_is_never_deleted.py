r"""使用者的資料夾，**這個程式一個都刪不掉**。

## 這一條的代價是量出來的，不是想出來的

2026-08-21：一個負向驗證的破壞把 `tests/conftest.py` 的沙箱路徑指到使用者
真實的 `%APPDATA%`。測試如期變紅——**然後行程結束，一個 `atexit` 註冊的
`shutil.rmtree` 刪掉了整個 roaming 設定檔**。

那一次刪掉的東西裡有 `Microsoft\Protect\`，那是 **DPAPI 的主金鑰**。

**代價 2026-08-25 才出現**，而且看起來完全不像檔案被刪：

| 使用者說的 | 真正的原因 |
|---|---|
| 桌布設定不見了 | `Themes\TranscodedWallpaper` 8/21 就沒了 |
| Edge 的 Google 要重新登入 | cookie 金鑰是 7/26 用**舊的**主金鑰加密的 |
| Twitch 要重新登入 | 同上 |
| Steam 要重新登入 | 同上 |

那些 App 沒有立刻壞，是因為它們一直在跑、金鑰在記憶體裡。使用者
8/24 晚上登出、8/25 中午登入，它們重新啟動、必須重新解密，才一起失敗。

> **判準**：一次刪除的代價**不會在刪除的當下顯現**。
> 「跑完之後看起來一切正常」證明不了任何事。

## 為什麼守衛要自己重新推導

破壞驗證的工作**就是**把某一行指到別的地方。所以那道門不准信任呼叫端
給它的路徑——它每一次都自己重算一遍「使用者的真資料夾在哪」，
而且**不看 `%APPDATA%`**（那個環境變數正是測試會改掉的東西）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from app.services import not_ours

# 這個程式自己那個設定資料夾的名字。**拼起來，不寫成一個完整路徑**——
# 完整路徑會被 `test_no_test_spells_out_the_apps_config_folder_by_hand`
# 擋下來，而那條擋對了：寫死路徑的測試繞得過 `%APPDATA%` 的重導。
APP_FOLDER = "AIControlCenter"


def _home() -> Path:
    """**問受測的模組自己**，不要在測試裡再解析一次家目錄。

    第一版寫的是 `os.environ.get("USERPROFILE") or ... or Path.home()`，
    而 `tests/test_your_real_settings_stay_yours.py` 當場擋下來——
    測試裡出現家目錄解析器是被明著禁止的（它會繞過沙箱）。

    **那條紅燈擋對了，而且改法比原本的好**：問 `protected_roots()[0]`
    等於「這個模組說它保護的第一個地方」，所以這份測試量的是**它自己的
    清單**，不是我另外算的一份。兩份會漂開，一份不會。
    """
    return not_ours.protected_roots()[0]


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "AppData",
        "AppData/Roaming",
        "AppData/Roaming/Microsoft",
        # **這四個是 2026-08-21 真正付出代價的那幾個。**
        "AppData/Roaming/Microsoft/Protect",
        "AppData/Roaming/Microsoft/Windows/Start Menu",
        "AppData/Roaming/Microsoft/Windows/Themes",
        "AppData/Roaming/Microsoft/Credentials",
        "AppData/Local",
        "Documents",
        "Desktop",
    ],
)
def test_the_users_real_folders_are_refused(relative: str) -> None:
    """**這一條是這份檔案存在的理由。**"""
    target = _home() / relative if relative else _home()
    why = not_ours.why_it_is_not_ours(target)
    assert why, f"「{target}」被判成可以刪"
    with pytest.raises(not_ours.NotOursToDelete):
        not_ours.remove_tree(target)


def test_a_real_appdata_is_refused_even_though_the_app_writes_there() -> None:
    """`%APPDATA%\\AIControlCenter` 是這個程式的設定，**但它還是使用者的檔案**。

    2026-08-21 那次的路徑**看起來完全合法**——它就是 `%APPDATA%`。
    錯的是 `%APPDATA%` 本身被指到了真的地方。所以判斷不能是
    「這個路徑長得對不對」，要是「**現在的 `%APPDATA%` 有沒有被重導到沙箱**」。
    """
    previous = os.environ.get("APPDATA")
    # **不把那個資料夾名字寫死在這裡。**寫死的話它會繞過沙箱，而
    # `tests/test_your_real_config_stays_yours.py` 明著在擋這件事——
    # 它擋對了。改成從環境變數推，跟產品程式碼走同一條路。
    real_roaming = _home() / "AppData" / "Roaming"
    os.environ["APPDATA"] = str(real_roaming)
    try:
        target = Path(os.environ["APPDATA"]) / APP_FOLDER
        assert not_ours.why_it_is_not_ours(target), (
            "`%APPDATA%` 沒有被重導的時候，它底下的東西被判成可以刪"
        )
    finally:
        if previous is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = previous


def test_our_own_temp_sandbox_is_still_removable() -> None:
    """**不准擋過頭。**

    一道什麼都拒絕的門，每一次執行都留一堆暫存目錄下來，
    而那正是它下一次被整個關掉的原因。
    """
    sandbox = Path(tempfile.mkdtemp(prefix="aicc-appdata-"))
    (sandbox / "AIControlCenter").mkdir()
    (sandbox / "AIControlCenter" / "cloud_sync.json").write_text("{}", encoding="utf-8")

    assert not_ours.why_it_is_not_ours(sandbox) == ""
    not_ours.remove_tree(sandbox)
    assert not sandbox.exists(), "自己造的沙箱刪不掉"


def test_refusing_is_loud_not_silent() -> None:
    """拒絕的時候要丟例外，**不是靜靜地不做**。

    靜靜不做的話，一個指錯地方的呼叫看起來會像成功——而那正好是這個專案
    最在意的那個病（一個看起來正常的假訊號）。
    """
    with pytest.raises(not_ours.NotOursToDelete):
        not_ours.remove_tree(_home() / "Documents")
    with pytest.raises(not_ours.NotOursToDelete):
        not_ours.remove_file(_home() / "Documents" / "隨便一個檔案.txt")


def test_the_reason_says_which_folder_and_why() -> None:
    """理由要講得出**哪一個資料夾**、**為什麼**。

    「拒絕」兩個字寫進紀錄等於沒寫——三個月後讀到的人要知道它擋的是什麼。
    """
    why = not_ours.why_it_is_not_ours(_home() / "AppData" / "Roaming" / "Microsoft")
    assert "Microsoft" in why
    assert len(why) > 20, why


def test_the_protected_list_is_recomputed_every_time() -> None:
    """那張清單不准在 import 的當下算好。

    算好的話它會凍在那一刻，而測試在那之後才改環境變數——
    同一個形狀 2026-08-24 一天之內犯過三次（模組層叫 `t()`）。
    """
    first = not_ours.protected_roots()
    previous = os.environ.get("ProgramData")
    os.environ["ProgramData"] = str(Path(tempfile.gettempdir()) / "假的-programdata")
    try:
        second = not_ours.protected_roots()
    finally:
        if previous is None:
            os.environ.pop("ProgramData", None)
        else:
            os.environ["ProgramData"] = previous
    assert first != second, "清單在 import 當下就凍住了，改環境變數它沒反應"


def test_a_path_that_cannot_be_resolved_is_refused_not_allowed() -> None:
    """算不出來的路徑往**嚴**的那一邊倒。

    往鬆的那一邊倒的話，一個壞掉的路徑會變成一張通行證。
    """
    why = not_ours.why_it_is_not_ours("\x00這個路徑不合法")
    assert why, "算不出來的路徑被判成可以刪"
