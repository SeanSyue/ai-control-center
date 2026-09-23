"""壓縮之前，把「現在做到哪」寫到硬碟。

壓縮會把對話摘要掉。摘要是有損的——2026-08-21 那次，老闆講的「章要對應
**邏輯審核**」在摘要裡變成「機器章與審查章要分開」，四個字就這樣沒了，
然後照著摘要做出來的東西少了那一半。

這支在壓縮**之前**跑，把只能從環境確定拿到的東西存成一份 checkpoint：
現在在哪個分支、有沒有沒存的改動、最近做了什麼。壓縮之後由
`session_start.py` 讀回來。

它**不存對話內容**。對話內容該去的地方是 `docs/WHY.md`（老闆的原話與決策
脈絡）與 git commit 訊息——那些在講的當下就該寫了，不是等壓縮前才補。
這支只是最後一道保險。

PreCompact 的 stdout **不會**被注入 context，所以這裡印什麼都只是給人看的。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
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

# 這個工作區裡的 git repo。記憶層那個是獨立的 repo，不會跟著母 repo 走，
# 所以要分開問——只問一個的話，另一個有沒有沒存的東西就看不到了。
REPOS = [
    ("AIControlCenter", ROOT / "AIControlCenter"),
    ("AICC-視覺概念", ROOT / "AICC-視覺概念"),
    ("共同記憶", ROOT / "AIControlCenter" / ".aicc" / "knowledge"),
]


def git(repo: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "").strip()


def describe(name: str, repo: Path) -> str:
    if not (repo / ".git").exists():
        return f"- **{name}**：不是 git repo"
    branch = git(repo, "branch", "--show-current") or "(不明)"
    dirty = [line for line in git(repo, "status", "--short").splitlines() if line.strip()]
    last = git(repo, "log", "-1", "--format=%h %s")
    line = f"- **{name}**（{branch}）：{len(dirty)} 個未提交"
    if dirty:
        line += "　⚠️ 沒存的東西壓縮後會忘記它為什麼在那裡"
    if last:
        line += f"\n  - 最後一筆：{last}"
    return line


def _transcript_hint() -> str:
    """這次對話的逐字紀錄在哪。

    不寫死 session id——每次對話都是新的一個。挑那個資料夾裡**最近改過**的
    `.jsonl`，那就是正在跑的這一場。

    找不到也不要讓 hook 死掉：checkpoint 少一行的代價，遠小於 hook 崩潰之後
    **整份都沒寫出來**——而那正是 2026-08-22 實際發生的事。
    """
    try:
        # Claude Code names a project's folder after its path, with ':' and the
        # separators turned into '-'. Deriving it beats hardcoding one machine's.
        workspace = Path(__file__).resolve().parents[3]
        encoded = (
            str(workspace).replace(":", "-").replace(chr(92), "-").replace("/", "-")
        )
        folder = Path.home() / ".claude" / "projects" / encoded
        newest = max(folder.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
        return str(newest)
    except Exception:
        return "（找不到；去 ~/.claude/projects/ 底下挑最近改過的 .jsonl）"


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 壓縮前的 checkpoint",
        "",
        f"寫於 {stamp}　觸發：{payload.get('trigger', '(不明)')}",
        "",
        "## 各個 repo 現在的狀態",
        "",
    ]
    lines += [describe(name, repo) for name, repo in REPOS]
    lines += [
        "",
        "## 原始逐字紀錄還在，而且找得到",
        "",
        "**壓縮是有損的，但被壓掉的東西沒有消失**——這一次對話的逐字紀錄還在",
        "硬碟上。壓縮後想不起來某件事**為什麼**那樣定，去那裡搜，不要重問老闆：",
        "",
        "```",
        f"{_transcript_hint()}",
        "```",
        "",
        "找法：`grep` 那個檔案，或用 `tools/search_ai_conversations.py`。",
        "**先搜再問。**他已經因為被重問而說過三次同一件事。",
        "",
        "## 壓縮之後**第一件事**",
        "",
        "讀 `交接給下一個對話.md`（工作區根目錄）。**那一份不是摘要，是刻意",
        "整理過的結論**——你是誰、他是誰、現在在做什麼、剩幾件、以及三件你",
        "一定會做錯的事。壓縮是有損的，那一份不是。",
        "",
        "## 然後",
        "",
        "1. 讀 `AIControlCenter/docs/WHY.md`——**老闆的原話與決策脈絡在那裡，"
        "不在對話裡**。要動哪個主題就先搜那個主題的標籤。",
        "2. **找東西問索引，不要用猜的**："
        "`cd AIControlCenter && .venv\\Scripts\\python.exe tools\\find.py \"要找什麼\"`",
        "3. 上面任何一個 repo 有未提交的東西，先問它是什麼——"
        "**壓縮後我不會記得那些改動的來由**。",
        "",
        f"（這份由 `.claude/hooks/pre_compact.py` 自動產生，每次壓縮前覆寫。）",
    ]

    try:
        CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
        CHECKPOINT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        # 存不下來不准擋住壓縮：壓縮擋住的話使用者就卡在滿的視窗裡，
        # 那比少一份 checkpoint 糟得多。
        print(f"checkpoint 寫不進去：{exc}", file=sys.stderr)
        return 0

    print(f"已寫入 {CHECKPOINT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
