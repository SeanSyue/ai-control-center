r"""數一數：還有多少段中文寫死在程式裡。

定案（2026-08-22）：**先做結構與守衛，翻譯之後做。**

這支是那支守衛的眼睛——`tests/test_no_new_hardcoded_chinese.py` 用它數，
人也可以直接跑它看現在剩幾段：

    .venv\Scripts\python.exe tools\i18n_scan.py

## 註解與 docstring 不算

這個專案**刻意**用大量中文寫說明（每一個決定為什麼那樣定、被什麼打掉）。
那些不是介面文字，翻譯也不該碰它們。所以這支用語法樹分辨：

* 模組／類別／函式最前面那個字串常數 → **docstring，不算**
* 其他的字串常數含中文 → **算**

用 grep 的話這兩種分不出來，而這個專案已經因為同一種混淆拿掉過三條檢查。
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")

# 只掃 `app/`。`tools/` 與 `tests/` 的中文是給開發者看的，不是介面。
ROOT = Path("app")

# 印中文到被導向的 stdout 時，cp950 會炸。這個專案已經因為同一件事修過
# 六支工具——**而且它們都只在「有話要說」的時候才炸**：全綠的那幾次完全正常，
# 等到真的抓到東西要講的那一刻才死，而那正是最需要它講話的一刻。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # stderr 也要：traceback 走那一條，只設 stdout 的話它會在印出崩潰訊息的
    # 路上再崩潰一次，而使用者拿到一個沒頭沒尾的錯誤。
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASELINE = Path("tools/i18n_baseline.json")

# **不算欠債的檔案，以及為什麼。**
#
# 這裡放的是「裡面的中文不是介面文字」的檔案。放行要留下痕跡，所以是
# 一份寫得出理由的清單，不是一個模式比對。
NOT_INTERFACE_TEXT = {
    "app/core/prompt_builder.py":
        "這一份裡的中文**是給 AI 看的提示詞**，不是給人看的介面。"
        "它改動的理由跟介面完全不同（換模型、調語氣、補一條規則），"
        "而把它算成欠債的結果是：有人加一行提示詞，這支守衛就變紅——"
        "**一條會誤報的檢查比沒有更貴，它會訓練人忽略紅燈。**"
        "（翻譯也不該碰它們：提示詞要用哪種語言寫，是另一個問題。）",
    "app/ui/web/build_pages.py":
        "這是**建置工具**，不是產品——它的訊息是給跑建置的開發者看的，"
        "不會出現在使用者的畫面上。它住在 `app/ui/web/` 只是因為它建的東西"
        "在那裡；照 `tools/` 底下其他工具一樣不算介面文字。",
    "app/services/chinese_text.py":
        "這一份裡的中文**是斷詞用的資料**（停用詞），不是給人看的。"
        "介面翻成英文的那天，中文的停用詞還是要留著——使用者的舊任務描述是"
        "中文的，索引裡的文件也是。真的要支援英文查詢，做的是**再加一份**，"
        "不是把這一份換掉。**加一份，不是換一份**，這就是它跟 i18n 的差別。",
    "app/core/prompt_text.py":
        "這一份裡的中文**是送給 AI 看的**，不是給人看的。它跟介面文字照不同的"
        "理由改：介面翻成英文的那天它們不該跟著變——那會讓 AICC 突然改用英文"
        "跟兩支 CLI 講話，而那是一個**該被決定的事，不是介面翻譯的副作用**。"
        "（判準：同時給人看又給 AI 看的，跟著「人」走進 `app/i18n.py`；"
        "只給 AI 看的留這裡。）",
    "app/core/prompt_markers.py":
        "這一份裡的中文**是 AICC 自己寫進提示詞的開場白長什麼樣**，"
        "拿來把它從對話紀錄裡剝掉。它必須跟 `prompt_builder` 送出去的一模一樣，"
        "所以是**由提示詞決定的，不是由介面語言決定的**——介面翻成英文的那一天，"
        "使用者硬碟上的舊對話紀錄還是中文，翻掉這裡等於讓剝除規則對所有既有"
        "紀錄失效。（`tests/test_prompt_markers_still_match.py` 看著。）",
    "app/i18n.py":
        "**那些字應該在的地方。**把目錄本身算成欠債的話，"
        "每搬一段進來總數就多一段——搬家會讓守衛變紅，"
        "而那正好是這支守衛要鼓勵的事。",
}


# **不算欠債的「一個名字」，以及為什麼。**
#
# 比整個檔案放行細一級：放行的是**某一個賦值（或某一支函式）的內部**，
# 不是那個檔案。這件事重要，因為上面那份清單放行一個檔案之後，那個檔案裡
# **之後新加的**介面文字也會一起被放掉——而這幾個檔案裡的中文只有一小撮
# 不是介面文字。
NOT_INTERFACE_VALUES = {
    ("app/services/ai_settings.py", "_LEGACY_PROFILE_NAMES"):
        "這張表的**鍵**是舊設定檔裡存過的中文（「省額度」「深入」⋯⋯），"
        "拿來把它們對回穩定代號。翻掉的話，既有使用者的設定檔會對不上任何"
        "一個分支，**安靜地退回最省的那一張**——用中文當鍵正是這張表的工作。",
    ("app/core/orchestrator_parsing.py", "SECRET_CHECK_BROKE"):
        "機密檢查自己壞掉的時候要回的那一句。**刻意不走 `t()`**——"
        "壞掉的那個東西有可能就是 i18n，而走 `t()` 會在報告故障的路上再掉進"
        "同一個洞，等於什麼都沒報。`test_the_four_that_would_poison_the_"
        "five_node_test.py` 有一條斷言它**不在** `STRINGS` 裡，"
        "而負向驗證 F5 把它搬進 i18n 會紅。",
    ("app/services/not_ours.py", "why_it_is_not_ours"):
        "拒絕刪除的時候要講的那一句。**刻意不走 `t()`**，理由跟 "
        "`SECRET_CHECK_BROKE` 同一條：這是一句**解釋安全拒絕**的話，"
        "它不能依賴另一個子系統是好的。i18n 自己壞掉的那一天，"
        "走 `t()` 會讓「我拒絕刪這個」變成「i18n 掛了」——而使用者要看到的"
        "是前者。2026-08-21 那次刪掉的東西裡有 DPAPI 主金鑰，"
        "代價四天後才出現，所以這一句非講清楚不可。",
    ("app/services/markdown_lite.py", "_is_cjk"):
        "這幾個「字」是 **Unicode 區段的邊界**（「一」到「鿿」是統一表意"
        "文字那一段的頭尾），不是文字。翻譯它等於改判斷範圍。",
    ("app/services/settings_homes.py", "HOMES"):
        "那一列是**登錄檔路徑**，Windows 上它就長這樣。前面兩個中文字是"
        "路徑被人讀得懂的那一半，而整串是一個位置，不是一句話。",
    ("app/ui/native_theme.py", "_TEMPLATE"):
        "這是 **Qt 樣式表的樣板**，裡面的中文全是 CSS 註解。它們不會出現在"
        "任何畫面上——真正會出現的那一句（「不認識的模式 ...」）已經搬走了。",
}


def _excused_value_ids(path: Path, tree: ast.AST) -> set[int]:
    """這個檔案裡被指名放行的那幾段，底下所有字串常數的 id。"""
    names = {
        name
        for (file_name, name) in NOT_INTERFACE_VALUES
        if file_name == path.as_posix()
    }
    if not names:
        return set()
    out: set[int] = set()
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Assign):
            target = next(
                (t.id for t in node.targets if isinstance(t, ast.Name)), None
            )
        # 有型別註記的賦值（`HOMES: tuple[Home, ...] = (...)`）是另一個節點
        # 型別。**漏掉它的下場不是報錯，是那條放行安靜地不生效**——而它看
        # 起來跟生效了一模一樣：清單上寫著理由，數字卻沒有變。
        elif isinstance(node, ast.AnnAssign):
            target = node.target.id if isinstance(node.target, ast.Name) else None
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            target = node.name
        if target is not None and target in names:
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    out.add(id(child))
    return out


def _docstring_ids(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            out.add(id(first.value))
    return out


def count_in(path: Path) -> int:
    """這個檔案裡有幾段寫死的中文介面文字。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    skip = _docstring_ids(tree) | _excused_value_ids(path, tree)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and CJK.search(node.value)
    )


def scan() -> dict[str, int]:
    """每個檔案幾段。**沒有中文的檔案不列**——列了只是把清單撐大。"""
    found: dict[str, int] = {}
    for path in sorted(ROOT.rglob("*.py")):
        if path.as_posix() in NOT_INTERFACE_TEXT:
            continue
        n = count_in(path)
        if n:
            found[path.as_posix()] = n
    return found


def load_baseline() -> dict[str, int]:
    if not BASELINE.is_file():
        return {}
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def main() -> int:
    now = scan()
    base = load_baseline()
    total = sum(now.values())

    if "--write" in sys.argv:
        BASELINE.write_text(
            json.dumps(dict(sorted(now.items())), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"寫好了：{len(now)} 個檔案、{total} 段。")
        return 0

    print(f"現在：{total} 段，分佈在 {len(now)} 個檔案")
    if base:
        was = sum(base.values())
        print(f"基準：{was} 段（差 {total - was:+d}）")
        grew = sorted(
            (name, n, base.get(name, 0))
            for name, n in now.items()
            if n > base.get(name, 0)
        )
        if grew:
            print("\n變多的（這些會讓守衛變紅）：")
            for name, n, before in grew:
                print(f"  {before:5} → {n:<5} {name}")
        shrank = sum(
            base.get(name, 0) - n for name, n in now.items() if n < base.get(name, 0)
        )
        if shrank:
            print(f"\n少掉 {shrank} 段——記得跑 `--write` 把基準往下收。")
    print("\n最多的十個：")
    for name, n in sorted(now.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {n:5}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
