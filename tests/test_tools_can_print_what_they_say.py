"""`tools/` 裡的工具在 Windows 的預設輸出編碼下印得出自己的輸出。

這台機器上 `sys.stdout.encoding` 是 **cp950**（輸出被導向管線或檔案時尤其是）。
一個 cp950 編不出來的字元會讓整支工具變成 traceback，而這件事的危險之處是
**它只在有話要說的時候才炸**：

* `verify_web_pages.py` 的 `✗` 只在有頁面失敗時才印。一切正常的日子裡完全
  看不出問題；出事的那天它不會告訴你哪裡出事，它會給你一個 UnicodeEncodeError。
* `inspect_run.py` 印 `✓ ✗ ⚑ ⋯ −`。自用驗收清單第 16 項直接叫使用者跑它，
  而 ROADMAP 說它是「三格以上的線第一次出問題時第一個該跑的東西」。

2026-08-11 查證：`negative_control.py`、`ui_payload_digest.py`、`zero_callers.py`
有那三行修正，另外六支沒有。`negative_control.py` 的註解甚至寫著這個坑是怎麼
踩到的——**修正存在、教訓寫下來了，就是沒有套到其他工具上**。

一支工具的輸出就是它唯一的產物，所以這條規則是機械的：
**印非 ASCII 的腳本，就要把 stdout／stderr 重設成 utf-8。**

## 而這支檢查自己漏了兩支（2026-08-24 修）

第一行寫的是「`tools/` 裡的工具」，`ROADMAP.md` 與交接文件也照著這樣轉述，
但 2026-08-11 寫下來的掃描是 `TOOLS.glob("*.py")`——**只有第一層**。
`tools/claude-hooks/` 是 2026-08-22 才長出來的，於是那兩支 hook 從那天起
不在這支檢查的視線裡，而報告上一整排 PASSED 讀起來像每一支都驗過了。

那兩支剛好都有那三行，所以這是**宣稱錯**，不是已經在漏。但漏的代價不是
理論：`session_start.py` 印的是壓縮之後要塞回視野的那段話，裡面有一個 `⋯`
——cp950 編不出來。它自己的註解就寫著 2026-08-22 實測「這支 hook 一直是
壞的，而且不會有人發現：hook 失敗不會擋住對話，只會安靜地什麼都不做」。

範圍現在是 `rglob`，並且多一條 `test_the_scan_goes_into_the_subfolders_too`
點名那支檔案——不然下一個子資料夾會用一模一樣的方式安靜地掉出去。

## 它擋不住什麼

擋不住「有那三行但根本沒生效」——這裡讀的是原始碼，不是跑起來的行為。
只有 `test_a_tool_really_survives_a_cp950_stdout` 那一條真的開了一個
process，而它只跑 `inspect_run.py` 一支。
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def _name(path: Path) -> str:
    """報告上顯示的名字：`tools/` 底下的相對路徑。

    用檔名的話，`claude-hooks/` 底下哪天出現一個跟第一層同名的檔，
    報告上兩列會長得一模一樣，而失敗的是哪一支看不出來。
    """
    return path.relative_to(TOOLS).as_posix()


def _scripts() -> list[Path]:
    """可以被當成腳本跑的工具（有 `__main__` 區塊的）。

    `rglob` 不是 `glob`：`tools/` 在 2026-08-11 是平的，`claude-hooks/`
    是 08-22 才有的，而少了那個 r，那兩支 hook 從此不在任何一條檢查裡
    ——**報告不會少一行，只會少兩行**，而那正是最難看出來的一種漏。
    """
    found = []
    for path in sorted(TOOLS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        if any(
            isinstance(node, ast.If)
            and ast.dump(node.test).find("__main__") >= 0
            for node in tree.body
        ):
            found.append(path)
    return found


def _prints_non_ascii(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(ord(char) > 127 for char in node.value):
                return True
    return False


def _unencodable(path: Path) -> set[str]:
    """這支工具的字串裡，cp950 編不出來的字元。"""
    bad: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for char in node.value:
                try:
                    char.encode("cp950")
                except UnicodeEncodeError:
                    bad.add(char)
    return bad


def test_there_are_tools_to_check() -> None:
    # 前提檢查：抓到零支的話底下每一條都會恆真。
    assert len(_scripts()) >= 8, [_name(p) for p in _scripts()]


# 這支 hook 印的是壓縮之後要塞回視野的那段話，而那段話裡有一個 `⋯`。
# 它是這條檢查 2026-08-11～08-24 之間看不到的那兩支之一，也是兩支裡真的
# 會 traceback 的那一支——所以點名它，不點名數量。
A_TOOL_IN_A_SUBFOLDER = "claude-hooks/session_start.py"


def test_the_scan_goes_into_the_subfolders_too() -> None:
    """**範圍縮回第一層的那一天，要有東西紅。**

    上面那條數的是「至少八支」，而第一層自己就有十四支——所以
    `rglob` 改回 `glob` 的時候它一個字都不會說，底下每一條參數化的檢查
    只是安靜地少兩列。報告上沒有失敗、沒有跳過，也沒有任何一行寫著
    「有兩支我沒看」。

    這條問的是別的事：**那個子資料夾裡的 entry point 有沒有真的進來。**

    **它擋不住什麼**：擋不住「未來第三個子資料夾」——它只認得這一支。
    真正的保險是 `rglob` 這件事本身，這條是它的警報器。
    """
    scanned = sorted(_name(p) for p in _scripts())
    assert A_TOOL_IN_A_SUBFOLDER in scanned, (
        f"{A_TOOL_IN_A_SUBFOLDER} 沒有進到掃描範圍裡。\n"
        f"掃到的是：{scanned}\n"
        "`_scripts()` 是不是又變回 `TOOLS.glob(\"*.py\")` 了——"
        "那是 2026-08-11 寫下的形狀，當時 tools/ 還是平的。"
    )


# 兩張參數化名單各點一支一定要在裡面的。
# `inspect_run.py` 印 `✓ ✗ ⚑ ⋯ −`（自用驗收清單第 16 項叫使用者跑它），
# `claude-hooks/session_start.py` 印的 `⋯` 是 cp950 真的編不出來的那一個。
A_TOOL_THAT_PRINTS_NON_ASCII = "inspect_run.py"
A_TOOL_THAT_WOULD_CRASH = "claude-hooks/session_start.py"


def test_both_filters_still_select_something() -> None:
    """**前提檢查之三：兩張參數化的名單空掉的那一天，要有東西紅。**

    2026-08-25 覆核補的。上面兩條前提檢查守的是 `_scripts()`——掃到幾支、
    有沒有走進子資料夾。**但底下兩條檢查真正吃的不是 `_scripts()`，是它再
    過一層篩子**（`_prints_non_ascii()` 與 `_unencodable()`），而那兩層當時
    沒有人看。

    參數化的名單空掉不會失敗，也不會跳過——它**收集到零個案例**：報告上
    少兩段，沒有 F、沒有 s，一個字都不會說。所以只要那兩支篩子哪天回錯了
    （例如又縮回隔壁那支「只認含 `print(` 的那一行」的寫法，那正是這一則
    在修的病），底下兩條就安靜地變成恆真，而 `tools/` 裡每一支印符號的工具
    從此沒有人驗。

    這裡點名不數數量，理由跟上面那條一樣：`_scripts()` 有十六支，篩子少掉
    一兩支的時候數字幾乎不動。
    """
    prints = sorted(_name(p) for p in _scripts() if _prints_non_ascii(p))
    crashes = sorted(_name(p) for p in _scripts() if _unencodable(p))

    assert A_TOOL_THAT_PRINTS_NON_ASCII in prints, (
        f"{A_TOOL_THAT_PRINTS_NON_ASCII} 不在「會印非 ASCII」那張名單上。\n"
        f"名單是：{prints}\n"
        "`_prints_non_ascii()` 是不是縮掉了——它空掉的話下面那條參數化檢查"
        "會收集到零個案例，而報告上看不出來。"
    )
    assert A_TOOL_THAT_WOULD_CRASH in crashes, (
        f"{A_TOOL_THAT_WOULD_CRASH} 不在「cp950 真的編不出來」那張名單上。\n"
        f"名單是：{crashes}\n"
        "`_unencodable()` 是不是縮掉了——它空掉的話下面那條參數化檢查"
        "會收集到零個案例，而報告上看不出來。"
    )


@pytest.mark.parametrize(
    # 只列真的會印非 ASCII 的。用 skip 的話報告上會多一個跳過，而這個專案
    # 有一條零跳過的底線——**一個跳過的測試在報告上跟通過分不出來**。
    "path", [p for p in _scripts() if _prints_non_ascii(p)], ids=_name
)
def test_a_tool_that_prints_non_ascii_fixes_its_own_encoding(path: Path) -> None:
    source = path.read_text(encoding="utf-8-sig")

    assert "reconfigure(encoding=\"utf-8\"" in source, (
        f"{_name(path)} 會印非 ASCII，但沒有把 stdout 重設成 utf-8。\n"
        "在 cp950 下這支工具的輸出會變成亂碼，而如果字元編不出來就直接 traceback"
        "——而那通常發生在它終於有話要說的那一刻。"
        "抄 negative_control.py 開頭那三行。"
    )


@pytest.mark.parametrize(
    "path", [p for p in _scripts() if _unencodable(p)], ids=_name
)
def test_the_dangerous_ones_are_the_ones_that_would_crash(path: Path) -> None:
    """哪幾支真的會炸，講出來。

    這條不是重複上面那條：上面那條的規則是「非 ASCII」，這條指名**真的**
    在 cp950 下編不出來的那些字元。`✗` 跟「決策紀錄」是兩種嚴重度：
    前者 traceback，後者只是亂碼。
    """
    assert _unencodable(path), "這條的參數化出錯了"
    source = path.read_text(encoding="utf-8-sig")

    assert "reconfigure" in source, (
        f"{_name(path)} 會印 {''.join(sorted(_unencodable(path)))!r}，"
        "cp950 編不出來 —— 沒有那三行的話這支工具會在印到那個字元時 traceback"
    )


def test_a_tool_really_survives_a_cp950_stdout() -> None:
    """真的跑一次，不是只讀原始碼。

    挑 `inspect_run.py` 的「這不是 AICC 專案」那條路：它很快、印中文、
    而且是使用者照著驗收清單跑錯路徑時最可能看到的第一句話。
    `PYTHONIOENCODING=cp950` 明確逼出那個環境，工具開頭那三行要把它蓋回來。
    """
    if not PYTHON.is_file():  # pragma: no cover - 環境問題
        pytest.skip(f"找不到 venv 的 python：{PYTHON}")
    env = dict(os.environ, PYTHONIOENCODING="cp950")

    proc = subprocess.run(
        [str(PYTHON), "tools/inspect_run.py", "C:/nope-not-a-project"],
        cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )

    assert "UnicodeEncodeError" not in proc.stderr, proc.stderr[-400:]
    assert "不是 AICC 專案" in proc.stdout, proc.stdout[:300]
