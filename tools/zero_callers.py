"""誰定義了、誰都不呼叫？以及更有意思的：誰只有測試在呼叫？

這支工具找過三個東西，每一個都值得：

* `Orchestrator.run()`（343 行）—— 舊 CLI 導向的狀態機，被 `WorkflowWorker`
  取代但沒清掉。零呼叫者。
* `app/services/cli_discovery.py`（251 行）—— 整個模組零production呼叫者。
* 2026-08-11：`_can_start` 等六個 property。它們**不是**零呼叫者，是
  **只有測試在呼叫**——而那是更糟的一類。

「只有測試在呼叫」為什麼比死碼糟：死碼是沒有人讀的程式，刪掉就好。
只有測試在讀的產品程式碼，代表**有人寫下一條規則、也寫了測試證明它成立，
然後那條規則從來沒有到達畫面**。測試是綠的、規則是對的、功能不存在。
那六個 property 表達的是「這個動作現在不能按」，而實際出貨的行為是
每個 handler 自己靜靜 return——選單項目是亮的，按下去畫面完全沒變。

用法：

    .\\.venv\\Scripts\\python.exe tools\\zero_callers.py

它算的是**名字出現的次數**（`ast.Name` / `ast.Attribute` / 改名的 import，
加上字串字面值），所以它是「有沒有被用到」的下限。看不到的東西，逐條寫在下面：

* JS 呼叫的 `@Slot`（`bridge.on_action` 在 HTML 裡，不在 .py 裡）。
* `getattr` 組出來的名字。
* Qt 用名字接的 signal／override（`closeEvent` 那一類，已列在 QT_OVERRIDES）。
* 同名不同物：兩個類別各有一個 `refresh`，其中一個沒人用也看不出來。

**曾經看不到、已經修掉的**：`from x import y as z`。之後每一次呼叫都寫 `z`，
`y` 這個名字一次都不會再出現。這個盲點差一點造成真的損害——2026-08-11 這支
工具把 `native_theme.stylesheet` 列進「只有測試在呼叫」，而 `main_window`
正在用它套整個原生外框的樣式。照著清單刪，原生視窗會失去全部樣式，
**而且沒有任何測試會紅**。`tests/test_zero_callers_does_not_lie.py` 釘住這一條。

**所以輸出是一張要人去讀的短名單，不是一張刪除清單。** 這件事很重要：
上一輪就有六個名字進了「零呼叫者」那一欄，而它們其實有測試在呼叫——
差別是掃描範圍，不是事實。

已經查過的、不用再查一次的（判斷寫在 ROADMAP，不在這裡，因為它們是決定
不是事實）：

* 四個原生面板類別（`DashboardPanel` 等）是 `main_window` 刻意保留的回退錨點，
  有 `tests/test_the_rollback_path_still_exists.py` 守著。留不留是待拍板項。
* `web_panel.on_action` 是誤報：JS 呼叫它（`bridge.on_action`），
  而那個呼叫在 HTML 裡不在 .py 裡。
"""
from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
# 呼叫端。app/ 之外也算，否則 tools/ 裡的入口（inspect_run、decision_summary）
# 會讓一整批真的在用的函式看起來像死碼。
CALLERS = {"app": APP, "tools": ROOT / "tools", "tests": ROOT / "tests"}

DUNDER_OK = {
    "__init__", "__enter__", "__exit__", "__repr__", "__str__", "__eq__",
    "__hash__", "__iter__", "__len__", "__getitem__", "__post_init__",
    "__contains__", "__bool__", "__lt__",
}
# 框架照名字呼叫的，不是我們呼叫的。
QT_OVERRIDES = {
    "closeEvent", "resizeEvent", "showEvent", "hideEvent", "paintEvent",
    "eventFilter", "keyPressEvent", "mousePressEvent", "sizeHint",
    "javaScriptConsoleMessage", "acceptNavigationRequest", "run",
}


def _parse(path: Path) -> ast.Module:
    # utf-8-sig：至少一個檔案帶著 BOM，用 utf-8 讀會在第一行就 SyntaxError。
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def _definitions() -> dict[str, list[tuple[str, int, str]]]:
    out: dict[str, list[tuple[str, int, str]]] = {}
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(_parse(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(node.name, []).append((rel, node.lineno, "def"))
            elif isinstance(node, ast.ClassDef):
                out.setdefault(node.name, []).append((rel, node.lineno, "class"))
    return out


def _uses(root: Path, names: set[str]) -> Counter:
    found: Counter = Counter()
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*.py")):
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                found[node.id] += 1
            elif isinstance(node, ast.Attribute):
                found[node.attr] += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                # `from x import y as z`：之後每一次呼叫都寫 `z`，`y` 這個名字
                # 一次都不會再出現。**這個盲點差一點造成一次真的損害**：
                # 2026-08-11 這支工具把 `native_theme.stylesheet` 列進
                # 「只有測試在呼叫」，而 `main_window` 正在用它
                # （`from app.ui.native_theme import stylesheet as
                # native_stylesheet`，第 1424 行套上整個原生外框的樣式）。
                # 照著清單刪，原生視窗會失去全部樣式而且沒有任何測試會紅。
                for alias in node.names:
                    if alias.asname:
                        found[alias.name.split(".")[-1]] += 1
        # 字串裡也可能帶著名字（getattr、動作 key、Qt slot 名）。
        text = path.read_text(encoding="utf-8-sig")
        for name in names:
            if f'"{name}"' in text or f"'{name}'" in text:
                found[name] += 1
    return found


def shortlists() -> tuple[list[str], list[str], dict, int]:
    """(只有測試在呼叫, 哪裡都沒提到, 定義位置, 掃了幾個名字)。

    第四個回傳值是 2026-08-24 補的。`main()` 原本印
    `len(names)`——而 `names` 是**這支函式的區域變數**，所以那一行是
    `NameError`，整支工具從第一行報告就掛掉。

    **而它 `raise SystemExit(main())` 的 exit code 是 0**（例外在
    `main()` 裡冒出來，`SystemExit` 那一行根本沒執行到），所以任何一個
    「跑得過就算過」的檢查看它都是綠的。一支專門用來報壞消息的工具，
    整支是死的，而沒有任何東西講。

    分類寫在這裡而不是 `main()` 裡，因為 `tests/test_zero_callers_does_not_lie.py`
    要問的就是這個分類。**測試自己重算一遍分類是不夠的**——負向驗證證實過：
    把 `main()` 改成什麼都不報，那條測試照樣全綠，因為它算的是自己那一份。
    """
    definitions = _definitions()
    names = {
        name
        for name in definitions
        if name not in DUNDER_OK
        and name not in QT_OVERRIDES
        and not (name.startswith("__") and name.endswith("__"))
    }
    seen = {where: _uses(root, names) for where, root in CALLERS.items()}

    nowhere: list[str] = []
    tests_only: list[str] = []
    for name in sorted(names):
        if seen["app"][name] or seen["tools"][name]:
            continue
        (tests_only if seen["tests"][name] else nowhere).append(name)
    return tests_only, nowhere, definitions, len(names)


def main() -> int:
    tests_only, nowhere, definitions, how_many = shortlists()

    def report(title: str, why: str, group: list[str]) -> None:
        print(f"\n{'=' * 68}\n{title}（{len(group)}）\n{why}\n")
        for name in group:
            for rel, line, kind in definitions[name]:
                print(f"  {kind:<6}{name:<34}{rel}:{line}")

    print(f"app/ 裡定義了 {how_many} 個名字（已排除 dunder 與 Qt override）。")
    report(
        "只有測試在呼叫",
        "產品程式碼有規則、測試證明規則成立、而規則沒有到達畫面。先讀這一組。",
        tests_only,
    )
    report(
        "app/、tools/、tests/ 都沒有提到",
        "可能是死碼，也可能是 JS／getattr／Qt 照名字呼叫的——要讀過才知道。",
        nowhere,
    )
    print(f"\n{'=' * 68}")
    print("這是下限，不是判決。看不到什麼寫在這支檔案的 docstring 裡。")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # stderr 也要：traceback 走那一條，只設 stdout 的話它會在印出
    # 崩潰訊息的路上再崩潰一次，而使用者拿到一個沒頭沒尾的錯誤。
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
