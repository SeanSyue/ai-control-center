r"""會印中文的工具，要印得出來才算數。

Windows 的主控台預設編碼是 cp950。它印得出常用中文，**但印不出 `✗`、`⋯`、
`←` 這一類符號**——而那些正是工具在「有話要說」的時候才會用到的字。

於是同一個 bug 已經發生三次：

| 哪裡 | 症狀 |
|---|---|
| `AICC-視覺概念/verify_pages.py` | 死在印失敗符號那一行，**十頁只驗了兩頁**，另外七頁從來沒被檢查過 |
| `tools/claude-hooks/session_start.py` | 印 `⋯` 直接炸，**壓縮之後該塞回視野的東西一個字都沒進來** |
| `tools/claude-hooks/pre_compact.py` | 同上 |

**每一次的形狀都一樣，而且都一樣難發現**：

1. 它**只在有東西要報的時候才炸**。平常那條路上沒有那些符號，所以全綠的時候
   它跑得好好的。
2. 它壞掉的方式不是回報錯誤，是**安靜地什麼都不做**（hook 失敗不擋對話；
   崩潰在迴圈裡，迴圈後面的守衛永遠執行不到）。

所以這裡不再靠「記得加那一段」。這一支掃過 `tools/` 底下每一支會被人直接執行
的程式，任何一支印得出非 ASCII 而沒有 `reconfigure` 就變紅。

**為什麼是「印得出非 ASCII」而不是「所有程式」**：只被 import 的模組不負責
印東西，強迫它們帶那段等於把一個保護稀釋成一個儀式。
"""
from __future__ import annotations

import re
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"

# 這一段的形狀。比對字串而不是解析 AST：它是一段固定的樣板，抄的人會照抄，
# 而抄錯的那種寫法（只設 stdout）正是這裡要抓的。
#
# 前綴比對，因為既有的工具帶著 `errors="replace"`——那比不帶更穩（印不出來
# 的字變成 `?`，而不是整支死掉），所以不能因為多了那個參數就判它沒做。
RECONFIGURE = 'reconfigure(encoding="utf-8"'

# 印得出來但 cp950 印不出來的那些字。挑的是實際踩過雷的三個。
NON_ASCII_SYMBOLS = ("✗", "✓", "⋯", "←", "→", "「", "」")


def _entry_points() -> list[Path]:
    """`tools/` 底下會被人直接跑的程式。

    判準是有沒有 `__main__` 那一段或 `def main(`——被 import 的輔助模組不負責
    印東西，把它們也算進來會讓這條檢查變成儀式。
    """
    found = []
    for path in sorted(TOOLS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if '__name__ == "__main__"' in text or "\ndef main(" in text:
            found.append(path)
    return found


def _prints_symbols(text: str) -> list[str]:
    """程式碼裡出現、而且會被印出去的那些符號。

    只看 `print(` 那幾行——註解與說明字串裡出現 `✗` 不會害任何人。
    """
    hits: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "print(" not in line and "sys.stdout.write" not in line:
            continue
        for symbol in NON_ASCII_SYMBOLS:
            if symbol in line:
                hits.add(symbol)
    return sorted(hits)


def test_every_tool_that_prints_symbols_can_actually_print_them() -> None:
    offences: list[str] = []

    for path in _entry_points():
        text = path.read_text(encoding="utf-8")
        symbols = _prints_symbols(text)
        if not symbols:
            continue
        if RECONFIGURE in text:
            continue
        offences.append(
            f"{path.relative_to(TOOLS.parent)} 會印 {' '.join(symbols)}"
        )

    assert not offences, (
        "這些工具會印出 cp950 印不出來的符號，而且沒有把輸出改成 utf-8。\n"
        "它們會在**有話要說的那一刻**崩潰，平常完全正常——"
        "已經有三支因為這件事安靜地失效過。\n"
        "修法：檔案開頭加\n"
        "    for _stream in (sys.stdout, sys.stderr):\n"
        "        try:\n"
        '            _stream.reconfigure(encoding="utf-8")\n'
        "        except Exception:\n"
        "            pass\n  "
        + "\n  ".join(offences)
    )


def test_the_fix_covers_stderr_too() -> None:
    """只設 `stdout` 不算。

    traceback 走的是 `stderr`。只設 stdout 的話，程式在崩潰的時候會**在印出
    崩潰訊息的路上再崩潰一次**，而使用者拿到的是一個沒頭沒尾的錯誤。
    """
    half_done: list[str] = []

    for path in _entry_points():
        text = path.read_text(encoding="utf-8")
        if RECONFIGURE not in text:
            continue
        # 找那個迴圈那一行，看 stderr 在不在裡面。
        loop = re.search(r"for\s+\w+\s+in\s*\(([^)]*)\)\s*:", text)
        streams = loop.group(1) if loop else ""
        if "stderr" not in streams and "sys.stderr" not in text:
            half_done.append(str(path.relative_to(TOOLS.parent)))

    assert not half_done, (
        "這些工具只把 stdout 改成 utf-8，stderr 沒有——"
        "它們會在印出 traceback 的路上再炸一次：" + "、".join(half_done)
    )
