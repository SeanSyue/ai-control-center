r"""同一種設定只准存在一個地方，而**那個地方要在清單上**。

老闆 2026-08-21 問的原話：

> 阿那些精靈設定下來的東西寫在哪　**會不會亂跑亂發散亂存**
> 這樣架構會跟影片說的核心理念們完全背道而馳

定案是【設定分五層，一層一個家】＋【每一層一份清單，一支會紅的檢查】。
清單在 `app/services/settings_homes.py`，這裡是那支檢查。

## 這份檔案 2026-08-24 之前只做了一半

它守著「登錄檔的位置只有一個」（下面第二條），那是被一個打錯字逼出來的：
`ai_settings_panel.py` 用的是 `QSettings("Sean", "AIControlCenter")`（**沒有
空格**），而 app 裡其他每一處都是 `"AI Control Center"`。在 Windows 登錄檔
裡**那是兩個不同的位置**，而畫面上完全正常。

但當時第三條（「不准自己開新檔」）被拿掉了，理由寫在檔案最後：**它會誤報**
——它分不出機器層的檔跟專案底下的檔，於是舉報了十幾個正常的檔名。

> 那件事要守的話，正確的做法是【每一層一份清單】那條定案——**先有清單，
> 檢查才有東西可以比**。清單還沒有，所以檢查也還不該有。

**清單 2026-08-24 寫好了**，所以那一條回來了，而且這次比得到東西。

寫清單的時候順便量到：原本文件裡那張表列了 10 個家，實際掃 `app/` 掃出
**17 個**——`sync-state.json`、`handover.json`、`task.md`、`workflow-result.json`、
`runtime/`、`.gitignore`，以及一個完全在另一顆星球上的 `Documents\AI Control
Center\logs\crash.log`。那張表寫的是「精靈會寫什麼」，不是「AICC 到底存了
什麼」——**而那個差距正是「亂跑亂發散」的定義**。

## 而那一條回來的時候，範圍只回來一半（2026-08-24 補）

第 49 行宣告 `SEARCHED = ("app", "tools")`，上半（QSettings）照著跑，
下半（`.aicc/` 與 `%APPDATA%` 那兩條全稱句）卻寫死 `Path("app")`。
於是「亂跑亂發散亂存」只在 `app/` 底下被擋，**工具那一側任何人加一個
`.aicc/<誰都沒決定過的檔>` 都不會紅**，而測試名字讀起來像整個 repo 都守著。

當時沒有真的漏網（`tools/` 那三個站點都在清單上），所以這不是一個已經
在漏的洞，是一個**宣稱比守備範圍大**的洞——而這個專案已經因為同一種
落差寫過整整一份掃描報告。兩半現在共用 `_python_files()`，
並且多一條 `test_the_aicc_scan_also_reaches_the_tools_side` 守著範圍
不准再縮回去。
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.services.settings_homes import (
    HOMES,
    SCOPE_MACHINE,
    machine_files,
    project_entries,
)

# 這台機器上唯一合法的那一個。**兩個字串都寫出來**：只寫一個的話，有人把
# 這裡跟程式一起改掉，這條檢查會安靜地跟著搬家。
ORGANISATION = "Sean"
APPLICATION = "AI Control Center"

SEARCHED = ("app", "tools")


def _qsettings_calls(path: Path) -> list[tuple[int, tuple[str, ...]]]:
    """這個檔案裡每一次 `QSettings(...)`，以及它用的字串引數。

    用語法樹不用 grep：註解與 docstring 裡提到 `QSettings("Sean", ...)` 的
    地方很多（這個專案的說明寫得很長），而**提到一個位置不等於用了它**。
    這個專案已經因為同一種混淆拿掉過三條檢查。

    回的是**呼叫**不是引數：`QSettings(ORGANISATION, APPLICATION)` 一個字面值
    都沒有，而它照樣是一次開店——只看引數的話會漏掉它。
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    found: list[tuple[int, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func
        if isinstance(name, ast.Attribute):
            name = name.attr
        elif isinstance(name, ast.Name):
            name = name.id
        else:
            continue
        if name != "QSettings":
            continue
        found.append((
            node.lineno,
            tuple(
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ),
        ))
    return found


def _python_files() -> list[Path]:
    return [
        path
        for folder in SEARCHED
        for path in Path(folder).rglob("*.py")
    ]


def test_the_scan_finds_something() -> None:
    """前提檢查：撈不到的話底下每一條都會「全部通過」。"""
    total = sum(len(_qsettings_calls(p)) for p in _python_files())
    assert total >= 1, f"只撈到 {total} 個 QSettings，這條檢查等於空轉"


# 唯一准自己開 `QSettings` 的地方。
THE_ONE_OWNER = "ui_preferences.py"


def test_only_one_place_opens_the_settings_store() -> None:
    """**登錄檔那一個家，只有一個主人。**

    這條 2026-08-24 之前檢查的是「每一處都要用對的組織名／程式名」——比對
    字串。那擋得住已經寫錯的那一處，擋不住**下一處**：五個地方各自
    `QSettings("Sean", "AI Control Center")`，第六個人照著抄，抄漏一個空格。

    現在只有 `services/ui_preferences.py` 開得了它，其他人叫 `store()`。
    打錯字這件事因此不再是紀律問題——**沒有第二個地方可以打錯。**
    """
    offenders = [
        f"{path}:{line}"
        for path in _python_files()
        if path.name != THE_ONE_OWNER
        for line, _ in _qsettings_calls(path)
    ]
    assert not offenders, (
        "這幾處自己開了設定庫：\n  "
        + "\n  ".join(offenders)
        + "\n\n改叫 `ui_preferences.store()`。組織名與程式名只准寫在 "
        f"{THE_ONE_OWNER} 一個地方——在 Windows 登錄檔裡少一個空格就是"
        "另一個位置，而畫面上看不出差別。"
    )


def test_that_one_place_uses_the_right_name() -> None:
    """主人自己要對。

    **兩個字串在這份測試裡也寫一次**：只靠 `ui_preferences` 自己的常數比，
    有人把那邊改掉的時候這條會跟著搬家，然後每個既有使用者的設定在那一刻
    安靜地消失（舊位置的東西還在，只是沒有人再去讀）。
    """
    from app.services import ui_preferences

    assert ui_preferences.ORGANISATION == ORGANISATION
    assert ui_preferences.APPLICATION == APPLICATION



# --- `.aicc/` 底下不准多出一個誰都沒決定過的東西 --------------------------

def _right_under_aicc(left: str) -> bool:
    """左邊那一段**就是 `.aicc` 本身**嗎（而不是它底下更深的地方）。

    只認第一層是刻意的。`project_path / ".aicc" / "runs" / run_id / "run.json"`
    在語法樹裡是四層 BinOp，每一層的左邊都含有 `.aicc`——寬鬆比對會把
    `runs`、`run_id`、`run.json` 全部當成「`.aicc/` 底下的新東西」報出來，
    而其中兩個只是路徑的中間段。

    **那種紅燈就是會訓練人忽略紅燈的那種。**
    """
    return (
        left.endswith(".aicc'")
        or left.endswith('.aicc"')
        or left.endswith("aicc_path")
        or left in ("aicc", "self.aicc", "local_aicc", "cloud_aicc")
    )


def _under_config_dir(left: str) -> bool:
    return left.endswith("config_dir")


def _joined_names(hint) -> list[tuple[str, int, str]]:
    """`<左邊符合 hint> / "檔名"` 的每一個字面值。

    比對的是**左邊那一段的原始碼文字**，不是執行結果——執行不了，因為
    那些路徑要等到有一個真的專案才存在。所以這條檢查的精確度全部來自
    `hint` 這個判斷寫得夠窄。

    掃的範圍是 `_python_files()`，也就是這個檔案第 49 行宣告的 `SEARCHED`。
    **2026-08-24 之前這裡寫死 `Path("app")`**：上半（QSettings 那半）跟著
    `SEARCHED` 跑，下半（`.aicc/` 與 `%APPDATA%` 這兩條）只看 `app/`，而
    兩條測試的名字都是全稱句。實測差額是三個站點——`tools/inspect_run.py`
    的 `runs`、`tools/ui_payload_digest.py` 的 `task.md`、
    `tools/claude-hooks/pre_compact.py` 指向**真正的** repo 的 `knowledge`。
    那三個當時剛好都在清單上，所以沒有人漏網；**洞是「工具那一側任何人
    加一個誰都沒決定過的檔都不會紅」**，不是已經漏出去的東西。

    範圍走位這件事自己也要有人守，那是
    `test_the_aicc_scan_also_reaches_the_tools_side` 的工作——不然縮回
    `app/` 的那一天，兩條全稱句照樣全綠。
    """
    found: list[tuple[str, int, str]] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
                continue
            right = node.right
            if not isinstance(right, ast.Constant) or not isinstance(right.value, str):
                continue
            if not hint(ast.unparse(node.left).strip().lower()):
                continue
            found.append((path.as_posix(), node.lineno, right.value))
    return found


def _is_a_declared_home(name: str) -> bool:
    declared = project_entries()
    if name in declared:
        return True
    # 目錄型的家（`runs/`、`knowledge/`、`runtime/`）：清單上寫著結尾的斜線，
    # 程式裡是 `"runs"`。兩種都算同一個家。
    return any(d.endswith("/") and name == d[:-1] for d in declared)


def test_the_aicc_scan_finds_something() -> None:
    """前提檢查。撈不到就等於恆真——那正是這條檢查上一次被拿掉的原因。"""
    assert len(_joined_names(_right_under_aicc)) >= 5


def test_the_aicc_scan_also_reaches_the_tools_side() -> None:
    """前提檢查之二：**範圍縮回 `app/` 的那一天要有東西紅。**

    上面那條數的是「撈到幾個」，而 `app/` 一側就有三十幾個——所以範圍從
    `("app", "tools")` 掉回 `app/` 的時候，它的數字幾乎不動，一個字都不會說。
    那正是 2026-08-24 之前的狀態：兩條全稱句（`.aicc/` 底下、`%APPDATA%`
    旁邊）在工具那一側是瞎的，而報告上是綠的。

    所以這裡問的不是數量，是**有沒有真的走到 `tools/` 底下**（含子資料夾
    ——`tools/claude-hooks/` 是 2026-08-22 才長出來的，而它指的是這個 repo
    真正的 `.aicc/knowledge`）。

    **它擋不住什麼**：擋不住 `SEARCHED` 再多一個資料夾而 `_joined_names`
    沒跟上——它只認得「tools/ 掉了」這一種走位。真正的保險是兩半共用
    `_python_files()` 這件事本身，這條是那件事的警報器。
    """
    from_tools = sorted(
        f"{path}:{line} → {name}"
        for path, line, name in _joined_names(_right_under_aicc)
        if path.startswith("tools/")
    )
    assert from_tools, (
        "`.aicc/` 的掃描一個 `tools/` 底下的站點都沒撈到。"
        "檢查 `_joined_names` 是不是又寫死了 `Path(\"app\")`——"
        f"這個檔案宣告的範圍是 {SEARCHED}。"
    )
    assert any(path.startswith("tools/claude-hooks/") for path, _, _ in
               _joined_names(_right_under_aicc)), (
        "掃到了 `tools/` 第一層，但沒有走進子資料夾。"
        "`tools/claude-hooks/pre_compact.py` 寫的是這個 repo 真正的 "
        "`.aicc/knowledge`，它不進來的話那一支等於沒有人看。"
    )


def test_nothing_new_appears_under_aicc() -> None:
    """**`.aicc/` 底下每一個東西都要有人決定過它屬於哪一個功能。**

    這一條擋的不是「檔案放錯資料夾」——那看得出來。它擋的是**多一個家**：
    某個功能寫了一個誰都沒決定過位置的新檔，而畫面上完全正常。
    """
    strays = sorted(
        {
            f"{path}:{line} → .aicc/{name}"
            for path, line, name in _joined_names(_right_under_aicc)
            if not _is_a_declared_home(name)
        }
    )
    assert not strays, (
        "`.aicc/` 底下出現了清單上沒有的東西：\n  "
        + "\n  ".join(strays)
        + "\n\n先決定它屬於哪一個功能，再把它加進 "
        "`app/services/settings_homes.py`。**不要先寫再說**——"
        "那正是「亂跑亂發散亂存」的樣子。"
    )


def test_nothing_new_appears_beside_the_machine_settings() -> None:
    """機器層同一件事。`%APPDATA%` 底下多一個檔一樣要有人看過。"""
    allowed = set(machine_files())
    strays = sorted(
        {
            f"{path}:{line} → {name}"
            for path, line, name in _joined_names(_under_config_dir)
            if name not in allowed
        }
    )
    assert not strays, (
        "`%APPDATA%` 底下出現了清單上沒有的檔：\n  " + "\n  ".join(strays)
    )


def test_every_home_says_what_it_is_for() -> None:
    """清單本身也要能用。

    一個沒有「這是什麼」的家，下一個人只能靠檔名猜——而依功能分類那條定案
    要的正是**先回答「這是什麼」才找得到位置**。
    """
    for home in HOMES:
        assert home.what.strip(), f"{home.where} 沒有寫它是什麼"
        assert home.where.strip(), f"「{home.what}」沒有位置"
    whats = [h.what for h in HOMES]
    assert len(whats) == len(set(whats)), "有兩個家宣稱自己是同一個功能"


def test_the_machine_layer_never_lands_in_a_project_folder() -> None:
    r"""**CLI 路徑不准進專案資料夾。**

    理由不是整齊：`cli-paths.json` 裡的路徑長成 `C:\Users\<名字>\...`，
    **路徑本身就會洩漏使用者的帳號名稱**，而專案資料夾會被打包、拷給別人、
    將來會進版控。
    """
    for home in HOMES:
        if home.scope != SCOPE_MACHINE:
            continue
        assert not home.where.startswith(".aicc"), (
            f"「{home.what}」是機器層的，卻住在專案資料夾裡：{home.where}"
        )


def test_the_home_names_are_keys_not_sentences() -> None:
    """**每一個家的名字是 key，不是那句中文。**

    `project_wizard.WRITES_TO` 拿這個值當查詢的鍵。存中文的話，介面翻成
    英文的那一刻那個查詢就斷了——而斷掉的樣子是精靈說「我不知道這一題
    寫到哪」，在一個看起來完全正常的畫面上。

    改壞的樣子就是它 2026-08-24 之前的樣子：`what` 直接存「這個專案的人」。
    """
    import re

    from app.i18n import STRINGS

    cjk = re.compile(r"[\u4e00-\u9fff]")
    for home in HOMES:
        assert not cjk.search(home.what), (
            f"「{home.what}」是一句中文，不是 key。"
            "存中文的話，翻譯的那一刻 WRITES_TO 那個查詢就斷了。"
        )
        assert home.what in STRINGS, f"{home.what} 不在 app/i18n.py 裡"
        assert home.label.strip(), f"{home.what} 取不出那句話"


def test_both_wizards_point_at_real_homes() -> None:
    """兩個精靈的每一題都要對到一個真的家。

    改壞的樣子：`WRITES_TO` 裡留著一個舊的 key，而那一題寫出去的東西
    落在一個沒有人宣告過的位置。
    """
    from app.services import project_wizard, system_wizard

    known = {home.what for home in HOMES}
    for module in (project_wizard, system_wizard):
        for field, key in module.WRITES_TO.items():
            assert key in known, (
                f"{module.__name__} 的「{field}」指向 {key!r}，"
                "而那不在 settings_homes.HOMES 裡"
            )
