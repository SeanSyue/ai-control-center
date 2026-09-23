"""壓縮（或續接）之後，把該記得的東西塞回視野。

SessionStart 的 stdout **會被當成 context 注入**——這是整個機制成立的關鍵。
壓縮把對話摘要掉之後，Claude Code 會再叫一次 SessionStart，而這支印出來的
東西，壓縮後的我看得到。

## 刻意印得很短

這裡印的是**指路**與**規矩**，不是資料。

理由很直接：印得長，下一次壓縮就把它一起壓掉了——那等於把同一個問題往後
挪一輪。真正的內容留在硬碟上（`docs/WHY.md`），這裡只負責讓我知道
「去那裡查」以及「沒查之前不准動工」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Windows 主控台是 cp950，印不出 `⋯` 這種字元——而這支的**全部意義就是印東西**。
#
# 2026-08-22 實測：這支 hook 一直是壞的，`print` 直接丟 UnicodeEncodeError，
# 於是壓縮之後該被塞回視野的東西一個字都沒進來。**而且它不會有人發現**：
# hook 失敗不會擋住對話，只會安靜地什麼都不做。
#
# 同一個 bug 讓設計端那支頁面驗證十頁只驗了兩頁。這是第三次了，所以
# `tools/find.py` 從第一天就帶著這一段。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# AICC/AIControlCenter/tools/claude-hooks/x.py -> AICC
ROOT = Path(__file__).resolve().parents[3]
CHECKPOINT = ROOT / ".claude" / "compact-checkpoint.md"
WHY = ROOT / "AIControlCenter" / "docs" / "WHY.md"


def recent_headings(limit: int = 6) -> list[str]:
    """WHY.md 最近幾則的標題。

    只印標題不印內容：標題已經足夠讓人（和我）知道「這個主題有紀錄」，
    而全文印出來會在下一次壓縮時再被壓一次。
    """
    if not WHY.is_file():
        return []
    try:
        text = WHY.read_text(encoding="utf-8")
    except OSError:
        return []
    heads = [
        line[3:].strip()
        for line in text.splitlines()
        if line.startswith("## ") and "·" in line
    ]
    return heads[-limit:]


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    source = str(payload.get("source", "")) or "(不明)"

    out: list[str] = []
    out.append("=== 這個工作區的長期記憶不在對話裡 ===")
    out.append("")
    out.append(
        "**動工之前先讀 `AIControlCenter/docs/WHY.md`**——老闆的原話與"
        "每條規則被什麼打掉的紀錄在那裡。對話會被壓縮，那份不會。"
    )
    out.append(
        "要做哪個主題，就先搜那個主題的標籤（`#簽核鏈`、`#角色`、`#記憶` ⋯⋯）。"
        "**用他的原話對，不要用我的轉述對。**"
    )
    out.append("")
    out.append(
        "老闆講出一個需求或否決一個做法時，**當下**就加一則進 `WHY.md`，"
        "不要等之後補。等之後補等於不會補。"
    )
    out.append("")
    out.append(
        "**找東西問索引，不要用猜的**："
        "`cd AIControlCenter && .venv\\Scripts\\python.exe tools\\find.py \"要找什麼\"`"
        "——它涵蓋 `AIControlCenter/` 與 `AICC-視覺概念/` 兩邊，"
        "而且會講出索引有沒有過期。**先用它再用 grep。**"
    )

    heads = recent_headings()
    if heads:
        out.append("")
        out.append("最近記下的幾則：")
        out += [f"  - {h}" for h in heads]

    if source == "compact" and CHECKPOINT.is_file():
        try:
            out.append("")
            out.append("=== 壓縮前的 checkpoint ===")
            out.append(CHECKPOINT.read_text(encoding="utf-8").strip())
        except OSError:
            pass

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
