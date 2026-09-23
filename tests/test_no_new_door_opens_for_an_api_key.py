"""**AICC 要多收一種憑證之前，先問他。**

老闆 2026-08-09（`DECISIONS.md:271` 記著這一句）：

> 如果一定要保留點什麼，那就把這個跟我討論，**可能可以設定特例**

那條定案的不變式是模組級的（`credential_entry` 沒有任何一個函式收得下一把
key），旁邊那支 `test_the_credential_door_cannot_take_a_key.py` 守的就是那一個
模組。**而推論那一句一直沒有人守**：今天在 `app/` 底下任何一支新模組長出
一個 `save_api_key(...)`，不會有任何一條檢查變紅，而 `app/core/lineup.py:86`
的 `CREDENTIAL_API_KEY = "api_key"` 已經是一個合法值在等那條路打開。

「先問他」如果只是一句話，失敗的時候不會有東西紅。這裡是那件事的機制。

## 為什麼是「形狀」不是「行為」

跟隔壁那支同一個理由：存或不存是行為，行為隔一次改動就會變；**收得下就代表
某一天會有人存它**。所以量的是門有沒有多開一扇——模組名、類別名、函式名、
參數名，**以及宣告出來的欄位**（`api_key: str = ""`）與掛到實例上的屬性
（`self.access_token = ...`）。

### 欄位那兩種是 2026-08-24 覆核時補的

第一版只掃到「參數名」為止，而**這個程式碼庫最可能長出那扇門的形狀不是參數，
是欄位**：設定物件在 `app/services/ai_settings.py`、`app/core/lineup.py` 這
一帶是 dataclass，加一格設定的自然寫法是加一個欄位，不是加一支
`save_api_key()`。實測過：

```python
@dataclass
class ProviderConfig:      # 類別名不含憑證字眼
    api_key: str = ""      # ← 第一版：整條檢查一句話都不會說
```

一條名字是全稱句（「多一扇收得下憑證的門就紅」）的檢查，真正走過的範圍比那
句話小——那正是 FG15／FG16／FG18／FG19 這一整批的同一個病，而它差一點在**修
那個病的那支檔案自己身上**再犯一次。

## 它擋不住什麼（都寫出來）

1. **擋不住換個名字。**`def remember(provider, value)` 一個字都不會被撈到。
   這條檢查賭的是「取名字的人會照著它是什麼取」，而那對善意的改動成立，
   對刻意繞過不成立——它防的是**不小心**，不是**故意**。
2. **擋不住已經在的那一扇。**LINE token 是拍過板的例外
   （`ROADMAP.md:1004-1008`、`SECURITY_MODEL.md:23`「使用者自己交出來的 API
   key 可收可存」），它在下面那張白名單上。這裡守的是**下一扇**。
3. **擋不住 `tools/` 與 `app/ui/web/*.html`。**掃的是 `app/**/*.py`。
   工具那一側是開發用的，不是出貨的產品面；頁面那一側收得到的是使用者
   打的字，而字要落地一定要經過 Python 這一側的某一個函式。
4. **擋不住沒有型別標註的模組級常數**，例如 `app/core/lineup.py:86` 那句
   `CREDENTIAL_API_KEY = "api_key"`。那一句是**欄位的名字**不是欄位本身
   ——它已經在那裡等那條路打開了（這支檔案存在的理由就是它），而真的把 key
   收進來的時候，收它的一定是上面那六種形狀裡的一種。
5. **擋不住 dict 的鍵**（`settings["api_key"] = ...`）。同一個理由：那是把值
   放進一個已經存在的袋子，而那個袋子本身是上面某一種形狀。

## 範圍縮掉的時候誰會紅

**`test_every_excuse_still_points_at_something_real` 就是這條檢查的前提檢查。**
下面白名單上有三列是**欄位**（`lineup.py::credential（欄位）` 與兩個
`estimated_tokens（欄位）`）——把掃欄位那兩段拿掉，`_doors()` 就再也產不出
那三個鍵，那條測試立刻報「白名單上這幾列已經指不到任何東西了」。

所以那三列不只是放行，它們是**釘子**：不要因為「反正是誤報」就把它們刪掉，
刪掉的那一天，掃描範圍縮回參數為止就沒有人會說一個字。

## 白名單怎麼加

**不要**因為紅了就把那一行貼進 `EXCUSED`。這條紅燈的意思是「這件事要先跟他
討論」，而白名單那一欄要寫的是**討論的結果**，不是「我看了覺得沒問題」。
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path("app")

# 一個名字裡有這些字，它十之八九就是憑證本身。
#
# `token` 收在裡面是刻意的，即使 LLM 的計費單位也叫 token：多出來的那種
# 命中要的是一行「這不是憑證，是計費單位」寫在白名單上，成本一行，
# 而漏掉一個 `access_token` 的成本是使用者的帳號。
SECRET_WORDS = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "bearer",
    "private_key",
    "client_secret",
)

# 已經開著的那幾扇門，一扇一個理由。
#
# 理由要回答的是「他什麼時候點頭的」，不是「我覺得它安全」——因為讀這張表
# 的人要判斷的是**這件事有沒有經過他**，而「看起來沒問題」是誰都寫得出來的。
EXCUSED: dict[str, str] = {
    "app/services/credential_entry.py": (
        "這支的全部工作是把人帶到廠商自己的登入畫面，它連一個參數都收不下"
        "——盯著這件事的是 tests/test_the_credential_door_cannot_take_a_key.py"
        "::test_no_function_here_can_even_accept_a_key，那支掃的就是這個檔案"
    ),
    # 底下三列是**同一個名字**命中三次：模組、類別、`MainWindow` 上的屬性。
    # 它們是「登入與安裝」那一頁的面板，名字跟著它畫的那一頁走
    # （`credentials_v1.html`），**不是跟著它碰得到的東西走——它一把都碰不到**。
    #
    # 這一列要回答的是「他什麼時候點頭的」：老闆 2026-08-26 拍板「憑證入口
    # 不進側邊欄、掛成 MainWindow 的一個屬性」（交接給PM-2026-08-25-C §十一 A）。
    # 拍的是**那一頁擺在哪**，而那一頁本身 2026-08-24 就已經定成「會員優先、
    # 一格輸入欄位都沒有」。要開 API key 那條路仍然要先問他。
    #
    # 而這三列不是靠「我看了覺得沒問題」撐著，有兩條機制：
    #   * `tests/test_the_credentials_page_is_reachable_and_never_invents_a_green_light.py`
    #     ::test_no_function_in_the_panel_can_even_accept_a_key —— 掃的就是這個檔案
    #   * `tools/verify_web_pages.py` 的 `CREDENTIALS_PROBE` ① —— 那一頁出現
    #     任何一個 `<input>` 就紅
    "app/ui/credentials_web_panel.py": (
        "「登入與安裝」那一頁的面板。名字跟著 credentials_v1.html 走，"
        "它連一個收得下 key 的參數都沒有——盯著這件事的是那一頁自己的測試"
        "::test_no_function_in_the_panel_can_even_accept_a_key，"
        "以及 verify_web_pages 的 CREDENTIALS_PROBE ①（頁面上不准有輸入欄位）"
    ),
    "app/ui/credentials_web_panel.py::CredentialsWebPanel": (
        "同上——這是那個模組裡唯一的類別，跟模組是同一件事"
    ),
    "app/ui/main_window.py::credentials_panel（欄位）": (
        "同上——這是那個面板掛在視窗上的那一格。老闆 2026-08-26 拍板它"
        "「不進側邊欄、掛成 MainWindow 的一個屬性」，這一行就是那句話"
    ),
    "app/services/line_notifier.py::save_credentials": (
        "LINE 通知的 token。使用者自己在畫面上貼進來的，ROADMAP.md:1004-1008"
        "拍過板、SECURITY_MODEL.md:23 明寫「使用者自己交出來的 API key 可收可存」"
        "（2026-08-09 定案）。存在 %APPDATA%、DPAPI 加密"
    ),
    "app/services/line_notifier.py::save_credentials(channel_access_token)": (
        "同上——這是那一扇門的門把，跟門是同一件事"
    ),
    "app/services/line_notifier.py::load_credentials": (
        "讀回自己存的那一份，不是去挖使用者沒交出來的東西"
        "（那條線由 tests/test_no_credential_scraping.py 守）"
    ),
    "app/services/line_notifier.py::clear_credentials": (
        "刪掉自己存的那一份。**這一扇是相反方向的門**，拿掉它使用者就沒有"
        "「我不要了」的出口（tests/test_line_credentials_can_be_removed.py）"
    ),
    "app/ui/main_window.py::_forget_line_credentials": (
        "上面那個「我不要了」在畫面上的按鈕"
    ),
    "app/ui/settings_web_panel.py::_line_token_set": (
        "只回一個 True／False 給設定頁畫「已設定／未設定」，不碰內容本身"
    ),
    # 底下三列是欄位。**它們同時是這條檢查的前提檢查**——掃欄位那兩段被拿掉
    # 的話，這三個鍵就不存在了，test_every_excuse_still_points_at_something_real
    # 會當場紅。不要因為「反正是誤報」把它們刪掉。
    "app/core/lineup.py::credential（欄位）": (
        "記的是這一輪**用了哪一種**憑證（座位登入／環境變數／未知），"
        "不是憑證本身——原始碼那一段的註解就寫著 "
        "「Never the key itself」，而且今天永遠是空字串。"
        "真的有人交出一把 key 的那一天，收它的是別的形狀，那時候這條照樣會紅"
    ),
    "app/services/context_ledger.py::estimated_tokens（欄位）": (
        "LLM 的**計費單位**，不是 access token。這正是 SECRET_WORDS 收 "
        "`token` 時就預期會付的那一行成本——寧可多寫一行「這不是憑證」，"
        "也不要為了少寫這一行而漏掉一個 `access_token`"
    ),
    "app/services/project_knowledge.py::estimated_tokens（欄位）": (
        "同上——LLM 的計費單位，不是 access token。這一格算的是「這次要送出去的"
        "脈絡大概幾個 token」，畫面上拿它顯示塞不塞得下"
    ),
}


def _smells(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in SECRET_WORDS)


def _python_files() -> list[Path]:
    return [p for p in sorted(APP.rglob("*.py")) if "__pycache__" not in p.parts]


def _slot(target: ast.expr) -> str:
    """一個被指派的東西的名字：`api_key` 或 `self.api_key` 的 `api_key`。

    認不得的形狀（下標、tuple 拆包）回空字串——那些不是「宣告一個位置」，
    是往一個已經存在的位置裡塞東西，見上面〈它擋不住什麼〉第 5 條。
    """
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _doors() -> dict[str, str]:
    """每一扇收得下憑證的門，以及它在哪一行。

    六種形狀，因為一把 key 要進到程式裡只有這幾條路：模組叫那個名字、
    類別叫那個名字、函式叫那個名字、參數叫那個名字、**宣告了一個那個名字的
    欄位**（`api_key: str = ""`），或者**掛一個那個名字的屬性上去**
    （`self.access_token = ...`）。

    後面那兩種是 2026-08-24 覆核時補的：這個程式碼庫的設定物件是 dataclass，
    加一格設定的自然寫法是加一個欄位而不是加一支函式，**而第一版掃到參數就
    停了**。詳情見這個檔案開頭那一節。

    回的是 `{門: 位置}`；門的字串本身就是白名單的鍵，所以它要**穩定**
    ——用相對路徑不用絕對路徑，用 posix 斜線不用 Windows 的。欄位那兩種
    多一個「（欄位）」後綴：同一個檔案裡一支叫 `credential` 的函式跟一個叫
    `credential` 的欄位是兩扇不同的門，共用一個鍵的話關掉一扇另一扇會跟著
    從清單上消失。
    """
    found: dict[str, str] = {}
    for path in _python_files():
        where = path.as_posix()
        if _smells(path.stem):
            found[where] = f"{where}:1（模組名字）"
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _smells(node.name):
                found[f"{where}::{node.name}"] = f"{where}:{node.lineno}"
            # `api_key: str = ""` —— 有型別標註的宣告，dataclass 的欄位是這種。
            if isinstance(node, ast.AnnAssign):
                slot = _slot(node.target)
                if slot and _smells(slot):
                    found[f"{where}::{slot}（欄位）"] = f"{where}:{node.lineno}"
            # `self.access_token = ...` —— 掛到實例上，沒有標註也算一個位置。
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and _smells(target.attr):
                        key = f"{where}::{target.attr}（欄位）"
                        found[key] = f"{where}:{node.lineno}"
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _smells(node.name):
                found[f"{where}::{node.name}"] = f"{where}:{node.lineno}"
            args = node.args
            everything = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            everything += [a for a in (args.vararg, args.kwarg) if a is not None]
            for arg in everything:
                if _smells(arg.arg):
                    key = f"{where}::{node.name}({arg.arg})"
                    found[key] = f"{where}:{node.lineno}"
    return found


def test_the_scan_really_walks_the_whole_app() -> None:
    """前提檢查：範圍縮掉的話，底下兩條都會安靜地變成恆真。

    這條不數「掃到幾扇門」——正常狀態下門就那幾扇，數字很難出錯得夠明顯。
    它問的是**掃描有沒有真的走進子資料夾**：`app/services/line_notifier.py`
    是已知的那一扇，它不在結果裡就代表掃描沒走到 `app/services/` 底下，
    而不是代表「沒有人收憑證了」。
    """
    files = _python_files()
    assert len(files) >= 40, f"只掃到 {len(files)} 支 .py，範圍是不是縮掉了"

    doors = _doors()
    assert "app/services/line_notifier.py::save_credentials" in doors, (
        "已知的那一扇門不在掃描結果裡，所以底下每一條都不算證據。\n"
        f"掃到的是：{sorted(doors)}"
    )


def test_no_new_door_opens_for_an_api_key() -> None:
    """**多一個地方收得下憑證，這裡就要紅。**

    改壞的樣子：某一支模組為了「方便」加一個 `save_api_key(provider, key)`，
    使用者的畫面上多一格輸入框，AICC 從「帶路的手」變成「保管的人」——
    而那件事他明講過要先討論。

    紅了的正確反應**不是**把那一行貼進 `EXCUSED`，是去問他。
    """
    doors = _doors()
    strangers = sorted(door for door in doors if door not in EXCUSED)
    assert not strangers, (
        "`app/` 底下多了收得下憑證的地方：\n  "
        + "\n  ".join(f"{door}　（{doors[door]}）" for door in strangers)
        + "\n\nDECISIONS.md:271：**要開 API key 那條路之前要先問他**"
        "（他的原話：「如果一定要保留點什麼，那就把這個跟我討論，"
        "可能可以設定特例」）。\n"
        "先問，拿到答案再把它加進這個檔案的 EXCUSED，"
        "**理由那一欄寫他什麼時候點頭的，不要寫我覺得它安全**。"
    )


def test_every_excuse_still_points_at_something_real() -> None:
    """白名單上的東西要還在。

    這條擋的是另一個方向的腐爛：那扇門被改名或刪掉之後，白名單上那一列
    留在原地——**而一列過期的放行等於替下一個人先開好一扇門**，因為它的
    名字哪天被別人拿去用，就直接是綠的。

    這個 repo 已經因為同一種形狀吃過虧（`SHOWS_A_WINDOW_ON_PURPOSE`
    那張清單，見 tests/test_no_black_windows_flash.py）。
    """
    doors = _doors()
    stale = sorted(door for door in EXCUSED if door not in doors)
    assert not stale, (
        "白名單上這幾列已經指不到任何東西了：\n  "
        + "\n  ".join(stale)
        + "\n\n那扇門搬家或改名的話，把這一列跟著改；真的關掉了就刪掉它。"
    )


def test_every_excuse_says_who_said_yes() -> None:
    """每一列都要有理由，而且是一句話不是一個字。

    空的理由跟沒有那一列的差別，只在讀的人會不會停下來——而這張表存在的
    全部意義就是讓人停下來。
    """
    thin = sorted(door for door, why in EXCUSED.items() if len(why.strip()) < 10)
    assert not thin, f"這幾列沒寫為什麼可以放行：{thin}"
