r"""**這個 repo 裡沒有第二個刪東西的入口。**

## 為什麼有這一支

2026-08-21 的事故不是「有人寫了危險的程式碼」，是**負向驗證把一行安全的
程式碼指到了別的地方**——那正是破壞驗證的工作。所以守衛不能是「不要寫
危險的程式碼」，必須是「**每一個刪除都要先問一次這是不是我們的**」。

`app/services/not_ours.py` 是那一問。這一支守的是**沒有人繞過它**。

## 四天之後才看得見的代價

2026-08-25 使用者說：桌布不見、Edge 的 Google 要重新登入、Twitch 要重新
登入、Steam 要重新登入。查出來全部是同一件事——8/21 刪掉的東西裡有
`%APPDATA%\Microsoft\Protect\`，那是 **DPAPI 的主金鑰**。

那些 App 沒有立刻壞，因為它們一直在跑、金鑰在記憶體裡。**登出再登入之後
才一起失敗。**

> **判準**：一次刪除的代價**不會在刪除的當下顯現**。
> 「跑完之後看起來一切正常」證明不了任何事。

## 這一支擋得住什麼、擋不住什麼

**擋得住**：`app/`、`tools/`、`tests/` 裡新寫的、沒有走那道門的刪除呼叫。

**擋不住**：
* `subprocess` 跑出去的 `rm`／`Remove-Item`——那是另一個入口，沒有機制。
* 放行清單裡那幾筆自己被改壞。放行是**逐個檔案逐個理由**寫的，
  所以改壞它至少要動到這裡，而動到這裡的人會讀到上面那段。

**只寫擋得住的部分，下一個人會以為那一塊已經安全了。**
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEARCHED = ("app", "tools", "tests")

# 會把東西從硬碟上弄掉的呼叫。
DESTRUCTIVE = {
    "rmtree": "shutil.rmtree",
    "unlink": "Path.unlink / os.unlink",
    "remove": "os.remove",
    "rmdir": "os.rmdir / Path.rmdir",
}

# **放行，逐個檔案逐個理由。**
#
# 放行的單位是「檔案 + 那個呼叫的名字」，不是整個檔案——整個檔案放行的話，
# 那個檔案裡**之後新加的**刪除也會一起被放掉（同一個形狀
# 2026-08-24 在 `tools/i18n_scan.py` 已經修過一次）。
ALLOWED: dict[tuple[str, str], str] = {
    ("app/services/not_ours.py", "rmtree"):
        "**這就是那道門本身**——`remove_tree()` 先問 `why_it_is_not_ours()`，拿到理由就丟例外，拿到空字串才刪。",
    ("app/services/not_ours.py", "unlink"):
        "同上，`remove_file()` 那一半。它們是這個 repo 裡唯二被允許直接呼叫作業系統刪除的地方。",
    ("tests/conftest.py", "rmtree"):
        "`_remove_sandbox`——它自己重新推導沙箱長什麼樣（在系統暫存底下、"
        "帶著 `mkdtemp` 給的前綴），不信任呼叫端給的路徑。它跑在直譯器"
        "收尾的時候，那裡丟例外只會變成沒人讀的 traceback，所以它選擇"
        "**安靜地不刪**——跟 `not_ours.remove_tree()` 丟例外的選擇相反，"
        "而兩邊的判準都是「哪一種比較不會出事」。",
    ("tests/test_two_negative_controls_do_not_wreck_the_tree.py", "unlink"):
        "刪的是 `.aicc/runtime/negative_control.lock`，**一個模組層級的常數**，不是呼叫端給的值"
        "——2026-08-21 那一次的病根正是信任了傳進來的路徑。而且那個檔案就是這一支測試自己"
        "剛剛放上去的（它跑之前先斷言那裡是空的）。它必須真的刪：不收乾淨的話，"
        "下一次跑負向驗證的人會被這一次的鎖擋在門外。",
    ("tests/test_your_real_config_stays_yours.py", "rmtree"):
        "那一支測試**在測 `_remove_sandbox` 會不會亂刪**，所以它要自己造"
        "幾個資料夾出來讓那道門拒絕。收尾清掉自己造的那個，路徑來自"
        "同一支測試裡的 `mkdtemp`。",
    ("app/services/project_dashboard.py", "unlink"):
        "刪掉自己造的執行鎖（專案資料夾的 `.aicc/` 底下）。"
        "使用者的專案資料夾不在保護清單上——那是他交給這個程式管的東西。",
    ("app/services/project_knowledge.py", "unlink"):
        "刪掉自己匯出的清單檔，同樣在專案的 `.aicc/` 底下。",
    ("tools/negative_control.py", "unlink"):
        "刪掉過期的 `.pyc`，路徑由 `path.parent / \"__pycache__\"` 算出來，"
        "而 `path` 是配方指名要破壞的那個原始碼檔——一定在 repo 裡。",
}

# 測試裡刪掉自己用 `tmp_path` 造的檔案，不算。pytest 保證那是它自己的。
TMP_FIXTURE_HINTS = ("tmp_path", "tmp_path_factory", "mkdtemp", "TemporaryDirectory")


# `remove` 與 `rmdir` 這兩個名字**太常見**——`list.remove()`、佇列的
# `store.remove()` 都叫這個。所以這兩個要看**接收者是誰**：只有
# `os.remove` / `os.rmdir` 算。
#
# 這是量出來的，不是想出來的：第一版不看接收者，當場誤判
# `store.remove(Path(arg))`（佇列移除一個專案）與
# `self._line_notify_threads.remove(entry)`（從 list 拿掉一個元素）。
# **一條會誤報的檢查比沒有更貴**——它會訓練人把紅燈當雜訊。
NEEDS_A_KNOWN_RECEIVER = {"remove": {"os"}, "rmdir": {"os"}}


def _calls_in(path: pathlib.Path) -> set[str]:
    """這個檔案裡出現過哪幾種刪除呼叫。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
            receiver = getattr(func.value, "id", "")
        else:
            name = getattr(func, "id", "")
            receiver = ""
        if name not in DESTRUCTIVE:
            continue
        allowed_receivers = NEEDS_A_KNOWN_RECEIVER.get(name)
        if allowed_receivers is not None and receiver not in allowed_receivers:
            continue
        found.add(name)
    return found


def _files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for area in SEARCHED:
        out += sorted((ROOT / area).rglob("*.py"))
    return [p for p in out if "__pycache__" not in p.parts]


def test_every_delete_goes_through_the_one_door() -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是 2026-08-21：某個地方直接 `shutil.rmtree(某個變數)`，
    而那個變數被一行破壞指到了使用者的 `%APPDATA%`。
    """
    offenders: list[str] = []
    for path in _files():
        rel = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        for call in sorted(_calls_in(path)):
            if (rel, call) in ALLOWED:
                continue
            # 測試裡刪自己 `tmp_path` 造的東西，不算——那是 pytest 的地盤。
            if rel.startswith("tests/") and any(
                hint in source for hint in TMP_FIXTURE_HINTS
            ):
                continue
            offenders.append(f"{rel}：{DESTRUCTIVE[call]}")

    assert not offenders, (
        "這幾處直接刪東西，沒有走 `app/services/not_ours.py` 那道門：\n  "
        + "\n  ".join(offenders)
        + "\n\n改成 `not_ours.remove_tree()` / `not_ours.remove_file()`，"
        "或者加進這一支的 `ALLOWED` 並**寫下為什麼那一處是安全的**。\n"
        "2026-08-21 刪掉的東西裡有 `%APPDATA%\\Microsoft\\Protect\\`"
        "（DPAPI 主金鑰），而代價四天之後才以「Edge 要重新登入」的樣子出現。"
    )


def test_the_allow_list_has_not_gone_stale() -> None:
    """放行清單上的每一筆，那個呼叫要真的還在。

    這是清單不會爛掉的那一半。少了它，`ALLOWED` 只會愈長愈長——
    而一張說謊的放行清單，比沒有清單更花時間。
    """
    stale = []
    for (rel, call), reason in ALLOWED.items():
        path = ROOT / rel
        if not path.is_file():
            stale.append(f"{rel}：檔案不在了，整筆該刪")
            continue
        if call not in _calls_in(path):
            stale.append(f"{rel}：已經沒有 {DESTRUCTIVE[call]} 了，這一筆該刪")
    assert not stale, "放行清單過期了：\n  " + "\n  ".join(stale)


def test_every_excuse_says_why() -> None:
    """放行要寫得出理由。

    「這個沒問題」不是理由——三個月後讀到的是一個沒有來由的結論，
    而那時候重新判斷的成本，跟當初查清楚一樣高。
    """
    thin = [f"{rel}／{call}" for (rel, call), why in ALLOWED.items() if len(why) < 20]
    assert not thin, f"這幾筆放行沒寫理由：{thin}"


def test_the_scan_actually_found_something() -> None:
    """前提檢查：掃不到任何刪除呼叫的話，上面那條是恆真的。"""
    total = sum(len(_calls_in(p)) for p in _files())
    assert total >= 5, f"整個 repo 只掃到 {total} 個刪除呼叫，這條檢查沒有在檢查"
