"""把所有專案的決策紀錄數起來放在一起看（ROADMAP P8「跨專案彙整」）。

回答的問題只有一個：**這段時間我一共批准了幾件，其中幾件其實沒有人看過。**

用法（從 repo 根目錄）：

    .\\.venv\\Scripts\\python.exe tools\\decision_summary.py
    .\\.venv\\Scripts\\python.exe tools\\decision_summary.py --since 2026-08-01
    .\\.venv\\Scripts\\python.exe tools\\decision_summary.py --since 2026-08-01 --until 2026-08-31

日期是 ISO 字串比對（`2026-08-01`），不是自然語言。

**只讀，不寫，不呼叫任何 CLI，不花錢。**

輸出含專案名稱，而專案名稱常常就是客戶名稱，所以它是本機閱讀用的。
要給別人看之前先走 `redact-export` skill。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.decision_summary import (  # noqa: E402
    render_open_questions,
    render_summary,
    summarise_all,
)

# 這支工具會印非 ASCII，而 Windows 的預設輸出編碼是 cp950：編不出來的字元會讓
# 整支變成 traceback。危險的地方是**它只在有話要說的時候才炸**——`✗` 只在失敗
# 時才印，所以一切正常的時候完全看不出來。
# 這一行不是為了好看：一支工具的輸出就是它唯一的產物。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把所有專案的決策紀錄數起來放在一起看。"
    )
    parser.add_argument("--since", default="", help="起始日，例如 2026-08-01")
    parser.add_argument("--until", default="", help="結束日，例如 2026-08-31")
    parser.add_argument(
        "--questions",
        action="store_true",
        help="列出還在等你回答的問題原文（可能含客戶名稱）",
    )
    args = parser.parse_args(argv)

    summary = summarise_all(since=args.since, until=args.until)
    print(render_summary(summary, since=args.since, until=args.until))
    if args.questions:
        print()
        print(render_open_questions(summary))

    # Non-zero when something could not be verified, so this can sit in a
    # checklist and actually stop it. A summary whose exit code is always 0
    # gets glanced at rather than read.
    return 1 if summary.excluded else 0


if __name__ == "__main__":
    raise SystemExit(main())
