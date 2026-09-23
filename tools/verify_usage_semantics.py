"""Settle the two open questions about the CLIs' own token figures.

ROADMAP P12「用量與花費怎麼呈現」 leaves two things unverified, and they need
opposite arithmetic, so no total is displayed until they are answered:

  1. Is `cached_input_tokens` inside `input_tokens`, or extra?
  2. Is `usage` this turn's, or a running total for the session?
     (openai/codex#17539 says `turn.completed` emits `ThreadTokenUsage.total`,
     not `.last` - but whether that total survives a `codex exec resume` in a
     NEW process is the half that decides our arithmetic.)

This reads a project's ledger and says which reading the data supports. It runs
no CLI and spends nothing; the readings are already stored by
`ContextLedger.record_vendor_usage`, so all that is needed is one real run on a
machine where a CLI is installed and signed in.

    HOW TO PRODUCE THE DATA (about a minute, one run):
      1. Settings: **序列要切成「計畫序列」**, then planning_rounds >= 2.
         2026-08-27 起出廠序列是**開發序列**, and `planning_rounds` is a field
         only the planning sequence ever reads - set it to 2 on the factory
         settings and the run ignores it, so the session never gets resumed
         mid-run and this script still cannot decide anything.
         `force_new_sessions` must be OFF - the whole question is what happens
         when a session is resumed. (出廠已經是 OFF, 見第 3 點.)
      2. Run the workflow once, all the way through.
      3. .\\.venv\\Scripts\\python.exe tools\\verify_usage_semantics.py <project path>

**2026-08-16: this machine can now do it.** The line above used to say it
could not - `discover_all()` reporting no Codex CLI and `signed_in=False` for
Claude. Re-measured: Claude Code 2.1.226 and Codex CLI 0.147.0, both on PATH,
both `signed_in: True`. The earlier reading was almost certainly a shell whose
PATH lacked the node directory.

Nothing blocks this but the decision to spend. See 自用驗收清單.md
「第一次真實執行」 for the run-book (target, settings, what to watch, when to
abort) - and note the profile presets do NOT set `planning_rounds >= 2` except
「深入」.

2026-08-27, re-measured: `force_new_sessions` is **False** in all three presets
and in the dataclass default. The line here used to say it was on in all three
and had to be turned off - it was changed 2026-08-21 (老闆: 要沿用同一個對話框)
and this run-book kept saying otherwise. A run-book that sends you to switch off
something that is already off reads exactly like one that is right.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.context_ledger import ContextLedger  # noqa: E402

# 這支工具會印非 ASCII，而 Windows 的預設輸出編碼是 cp950：編不出來的字元會讓
# 整支變成 traceback。危險的地方是**它只在有話要說的時候才炸**——`✗` 只在失敗
# 時才印，所以一切正常的時候完全看不出來。
# 這一行不是為了好看：一支工具的輸出就是它唯一的產物。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

BAR = "=" * 68


def _sessions(record) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for reading in record.usage_readings:
        grouped[str(reading.get("session_id") or "(未帶 session)")].append(reading)
    return grouped


def _verdict(series: list[int]) -> tuple[str, str]:
    """Which reading a single session's ordered input_tokens supports."""
    if len(series) < 2:
        return "?", "只有一筆讀數，看不出形狀——需要同一個 session 被 resume 至少一次"

    rising = all(b > a for a, b in zip(series, series[1:]))
    if not rising:
        return (
            "單輪值",
            "後面的讀數比前面小過至少一次。累計值不會變小，所以這是每次呼叫各自計數"
            "——**要相加**",
        )

    # Rising is necessary for cumulative but not sufficient: a per-process
    # counter also grows, because each round replays a longer conversation.
    # What separates them is the size of the jump against the first reading.
    first, last = series[0], series[-1]
    if last >= sum(series[:-1]):
        return (
            "累計值",
            f"最後一筆（{last:,}）不小於前面所有讀數的總和（{sum(series[:-1]):,}）。"
            "單輪值不會有這種形狀，因為每一輪各自只帶自己的 context——**要取最大值**",
        )
    return (
        "?",
        f"單調成長但幅度不夠決定性（{first:,} → {last:,}）。兩種讀法都會成長"
        "（後面的回合本來就帶著更長的對話）。再跑一次輪數更多的，或讓最後一輪的"
        "提示明顯更短",
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    project = Path(argv[1]).expanduser().resolve()
    if not project.is_dir():
        print(f"X 找不到專案資料夾：{project}")
        return 2

    ledger = ContextLedger(project)
    records = [r for r in ledger.records() if r.usage_readings]
    print(BAR)
    print(f"專案：{project}")
    print(f"ledger：{ledger.path}")

    if not records:
        print("\n沒有任何用量讀數。可能原因：")
        print("  · 這個專案在 2026-08-09 記錄功能上線之後還沒跑過")
        print("  · 跑了，但 CLI 沒有回報 usage（例如中途失敗、或 Claude 沒登入）")
        total_calls = sum(r.usage_total_calls for r in ledger.records())
        if total_calls:
            print(f"\n  （有 {total_calls} 次 CLI 呼叫被記錄，但沒有一次回報 usage）")
        return 1

    decisive = False
    for record in records:
        print(f"\n{BAR}\nrun {record.run_id}   {record.at}")
        print(
            f"  回報 {record.usage_reported_calls} / 呼叫 {record.usage_total_calls}"
            f"{'   ← 有呼叫沒回報，這一輪的資料不完整' if record.usage_reported_calls < record.usage_total_calls else ''}"
        )
        for session_id, readings in _sessions(record).items():
            series = [
                int((r.get("numbers") or {}).get("input_tokens", 0) or 0)
                for r in readings
            ]
            label, why = _verdict(series)
            print(f"\n  session {session_id[:18]}…  ({len(series)} 筆讀數)")
            for reading, value in zip(readings, series):
                cached = (reading.get("numbers") or {}).get("cached_input_tokens")
                extra = f"   快取 {int(cached):,}" if cached is not None else ""
                print(f"      {reading.get('event_type', '?'):<16} input {value:>9,}{extra}")
            print(f"      → 判定：{label}   {why}")
            if label != "?":
                decisive = True

    print(f"\n{BAR}")
    if decisive:
        print("問題 2（累計還是單輪）有結論了。把算法寫進 ContextLedger，")
        print("並把 _shell_state() 的 usage state 從 'developing' 換掉。")
    else:
        print("還不夠決定。需要同一個 session 被 resume 至少一次的資料：")
        print("  planning_rounds >= 2，而且 force_new_sessions 要關掉。")

    print(
        "\n問題 1（cached_input_tokens 是不是子集）**這個腳本判定不了**："
        "\n  它需要廠商文件，或一次「同樣輸入、關掉快取」的對照執行。"
        "\n  在有答案之前，不要把 cached 加進任何總和。"
    )
    return 0 if decisive else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
