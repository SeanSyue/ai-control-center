"""看一輪執行裡每一格被問了什麼、回了什麼，必要時重跑其中一格。

ROADMAP P10 Stage C 的入口。三格以上的工作線第一次出問題時，
第一個要回答的問題是「哪一格出事、它當初拿到什麼」——那份資料一直都在
`.aicc/runs/<run_id>/steps.jsonl`，這支工具只是把它讀出來。

用法（從 repo 根目錄）：

    .\\.venv\\Scripts\\python.exe tools\\inspect_run.py <專案路徑>
        列出這個專案最近的執行

    .\\.venv\\Scripts\\python.exe tools\\inspect_run.py <專案路徑> <run_id>
        列出那一輪的每一格：角色、供應商、成敗、耗時、prompt 開頭

    .\\.venv\\Scripts\\python.exe tools\\inspect_run.py <專案路徑> <run_id> --step 2
        印出第 2 格完整的 prompt 與輸出

    .\\.venv\\Scripts\\python.exe tools\\inspect_run.py <專案路徑> <run_id> --retry 2 --yes
        用當初那一段 prompt 重跑第 2 格

**`--retry` 會呼叫 CLI，也就是會花錢**，所以它需要 `--yes`。沒有 `--yes`
時只會印出「將要做什麼」然後停下來。這支工具不會替任何人決定要不要花錢。

`--retry` **不會把工作流接著跑下去**。它重跑一格、記錄結果，就這樣。
開發序列是手寫的流程不是可回復的狀態機，所以「從第 4 步接著跑」現在還
給不出誠實的版本。它買到的是「這格是偶發失敗還是 prompt 有問題」，
而且不用把前面幾格再付一次錢。規劃者那一格額外多買到一件事：重跑成功會
寫出 work-plan.md，下一次整輪執行就會讀它而不是重新規劃。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.orchestrator import Orchestrator  # noqa: E402
from app.services.ai_settings import AISettingsStore  # noqa: E402
from app.services.step_log import StepLog, StepRecord  # noqa: E402
from app.services.timeline_store import TimelineStore  # noqa: E402

# 這支工具會印非 ASCII，而 Windows 的預設輸出編碼是 cp950：編不出來的字元會讓
# 整支變成 traceback。危險的地方是**它只在有話要說的時候才炸**——`✗` 只在失敗
# 時才印，所以一切正常的時候完全看不出來。
# 這一行不是為了好看：一支工具的輸出就是它唯一的產物。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

RULE = "─" * 72


def first_line(text: str, limit: int = 56) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return "（空）"


def list_runs(project: Path) -> int:
    runs_dir = project / ".aicc" / "runs"
    if not runs_dir.is_dir():
        print(f"{project} 底下沒有 .aicc/runs，這個專案還沒有跑過。")
        return 1

    log = StepLog(project)
    rows = []
    for entry in sorted(runs_dir.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        steps = log.read(entry.name)
        if not steps:
            # A run from before the step log existed. Listed anyway, with the
            # reason - "not shown" and "nothing happened" must not look alike.
            rows.append((entry.name, "—", "沒有逐格紀錄（Stage C 之前的執行）"))
            continue
        failed = [s for s in steps if not s.success]
        # **「每一格的 CLI 都跑起來了」不等於「這一輪成功」。**
        #
        # 2026-08-24 之前這裡只看逐格的離開碼，於是一次被複查駁到修正上限
        # 的執行印成「全部成功」——每一格的 CLI 確實都正常結束了，
        # 而這一輪的結論寫在 `run.json` 的 `status` 裡，這支工具沒讀。
        #
        # 兩欄分開印，因為它們回答不同的問題：一欄是「幾格跑起來了」
        # （這支工具原本在答的），一欄是「這一輪怎麼收的」。
        rows.append(
            (
                entry.name,
                str(len(steps)),
                f"{len(failed)} 格沒跑起來" if failed else f"{len(steps)} 格都跑起來了",
                _ending_of(project, entry.name),
            )
        )

    if not rows:
        print("沒有找到任何執行。")
        return 1

    print(f"{'run_id':<28}{'格數':<6}{'CLI':<18}這一輪怎麼收的")
    print(RULE)
    for row in rows[:30]:
        run_id, count, cli = row[0], row[1], row[2]
        # 沒有逐格紀錄那一種只有三欄——它連 run.json 都可能沒有。
        ending = row[3] if len(row) > 3 else ""
        print(f"{run_id:<28}{count:<6}{cli:<18}{ending}")
    return 0


# 「這一步本來要跑，而它一次工都沒派出去」——一種職責排不到人的收場。
#
# **逐格紀錄記的是派出去的工**，所以這種步驟在上面那張表裡不是「失敗的一格」，
# 是**完全不存在的一格**：畫面上它跟「跑過而且通過」長得一模一樣。事件本身
# 2026-08-27 起真的寫進了 `timeline.jsonl`，但這支工具當時只讀 StepLog，
# 於是「使用者事後問得到」那句話是假的——實測跑一輪有逐格紀錄的執行，
# `decision_gate_no_seat` 在檔案裡，而這支工具一個字都沒印。
#
# 兩個都收在這裡，不是只收今天那一個：`memory_update_no_seat` 是同一個形狀
# 的另一半，而這個專案反覆「修好了一個，另外三個還在」。
SKIPPED_STEPS = {
    "decision_gate_no_seat": (
        "決策關卡——那一題沒有人先替你看過，直接問了你",
        "陣容裡沒有一格的職責是「複查」",
    ),
    "memory_update_no_seat": (
        "維護共同記憶——這一輪的定案沒有寫進那八份",
        "陣容裡缺了「複查」或「執行」其中一種職責",
    ),
}


def show_skipped_steps(project: Path, run_id: str) -> None:
    """這一輪**沒有發生**的那幾步。

    印在逐格紀錄後面，因為它回答的是同一個問題的另一半：那張表講「派出去的
    工跑成怎樣」，這一段講「哪一步一次工都沒派出去」。
    """
    try:
        events = TimelineStore(project).load_events(run_id=run_id)
    except Exception:
        # 讀不到時間軸不該讓整支工具死掉——上面那張逐格紀錄還是有用的。
        return
    skipped = [event for event in events if event.event_type in SKIPPED_STEPS]
    if not skipped:
        return
    print(RULE)
    print("這一輪跳過的步驟（逐格紀錄上不會有，因為一次工都沒派出去）")
    for event in skipped:
        what, why = SKIPPED_STEPS[event.event_type]
        print(f"  ⊘ {what}")
        print(f"      為什麼：{why}")
        print(f"      紀錄代號：{event.event_type}")


def show_context(project: Path, run_id: str) -> None:
    """What this one run carried, and what it did not re-send.

    ROADMAP P0.5. `ContextLedger` has recorded this per run for a while and
    only the daily totals were ever shown, so 「這一輪帶了什麼」 was answerable
    from a file and nowhere on screen.

    Printed here rather than on a page because it belongs next to the steps:
    the breakdown is only interesting while you are looking at what that run
    actually did.

    Deliberately no 「省下多少 token」 figure. That would need a version of the
    same work done without AICC to compare against, and no such number exists.
    「沒有重送」 is measured - it is exactly the payload a naive implementation
    would have sent again.
    """
    from app.services.context_ledger import ContextLedger

    record = next(
        (r for r in ContextLedger(project).records() if r.run_id == run_id),
        None,
    )
    if record is None:
        return
    if not record.total_characters and not record.resend_skipped_characters:
        # A run against a project with no documents and no shared memory
        # really did carry nothing, so the figures would be true - and a
        # section of zeros is still noise. Nothing to explain, nothing shown.
        return

    print(RULE)
    print(f"這一輪帶進去的 context（profile：{record.profile or '未記錄'}）")
    rows = [
        ("契約與規則", record.contract_characters),
        ("共同記憶", record.memory_characters),
        ("專案文件", record.document_characters),
        ("原始佐證", record.raw_evidence_characters),
    ]
    for label, value in rows:
        if value:
            print(f"  {label:<10}{value:>9,} 字")
    print(f"  {'合計':<10}{record.total_characters:>9,} 字"
          f"（約 {record.estimated_tokens:,} tokens，估算）")
    if record.resend_skipped_characters:
        print(
            f"  沒有重送　{record.resend_skipped_characters:>9,} 字"
            f"（{record.resend_skipped_rounds} 輪沿用同一個 session）"
        )


MEMORY_DIFF_ARTIFACT = "memory-update-diff.patch"


def memory_diff(project: Path, run_id: str) -> str:
    from app.services.timeline_store import TimelineStore

    try:
        return TimelineStore(project).read_artifact(run_id, MEMORY_DIFF_ARTIFACT)
    except OSError:
        return ""


def show_memory_summary(project: Path, run_id: str) -> None:
    """How much this run changed the shared memory, in one line.

    ROADMAP P2「在 UI 顯示記憶 diff」. The patch has been written to
    `memory-update-diff.patch` on every run that maintained memory, and
    nothing ever showed it - so 「這一輪改了共同記憶什麼」 was answerable only
    by finding the file.

    A count here and the patch behind `--memory`, for the same reason the
    open-question text sits behind `--questions`: shared memory carries
    project detail, and a full patch printed on every run listing is a wall
    people scroll past.
    """
    patch = memory_diff(project, run_id)
    if not patch.strip():
        return
    files = sorted(
        {
            line.split(" ", 1)[1].split(" (")[0].removeprefix("a/")
            for line in patch.splitlines()
            if line.startswith("--- ") and " (before)" in line
        }
    )
    added = sum(
        1
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1
        for line in patch.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    print(
        f"共同記憶：動了 {len(files)} 個檔案，+{added} −{removed} 行"
        f"（{'、'.join(files) if files else '未記錄檔名'}）"
    )
    print("　看完整差異：--memory")


def show_memory_diff(project: Path, run_id: str) -> int:
    patch = memory_diff(project, run_id)
    if not patch.strip():
        print(f"{run_id} 沒有動到共同記憶，或者這一輪沒有跑到維護那一步。")
        return 1
    print(f"執行 {run_id} 對共同記憶做的變更")
    print(RULE)
    print(patch)
    print(RULE)
    print(
        "這是**已經套用**的變更，不是待審。要不要改成先問過再套用，"
        "見 ROADMAP P2「記憶更新需要使用者批准」——那會改變每一次執行的行為，"
        "所以還沒做。"
    )
    return 0


def _ending_of(project: Path, run_id: str) -> str:
    """這一輪的收場，從 `run.json` 讀，用 `outcomes.py` 的說法印。

    **不自己再寫一張表。**同一份收場在時間軸、決策橫幅、匯出報告與這裡
    各拼一次就會有四種說法，而看的人不知道該信哪一個。
    """
    from app.core import outcomes

    try:
        info = next(
            (r for r in TimelineStore(project).list_runs() if r.run_id == run_id),
            None,
        )
    except Exception:
        return "（讀不到 run.json）"
    if info is None:
        return "（沒有 run.json）"
    status = getattr(info, "status", "") or ""
    if not status:
        return "（run.json 沒寫收場）"
    try:
        return f"{status}／{outcomes.describe({'workflow_status': status}).label}"
    except Exception:
        # 認不得的收場**照原樣印**，不套一個現成的說法——
        # 印代號很醜，但醜的東西會被回報。
        return status


def show_run(project: Path, run_id: str) -> int:
    steps = StepLog(project).read(run_id)
    if not steps:
        print(f"{run_id} 沒有逐格紀錄。可能是 Stage C 之前跑的，或者 run_id 打錯了。")
        # **跳過的步驟要在這裡也講。**一輪只跳過、一次工都沒派出去的執行
        # 逐格紀錄是空的，而那正是最需要講出「那一題沒有人先替你看過」的
        # 那一種——早退在這裡等於對它永遠不出聲。
        show_skipped_steps(project, run_id)
        return 1

    print(f"執行 {run_id}，共 {len(steps)} 格")
    # **收場印在最前面。**印在最後的話，一個被駁到上限的執行，
    # 讀的人已經看完十幾行綠色的 ✓ 才看到它。
    print(f"這一輪怎麼收的：{_ending_of(project, run_id)}")
    print(RULE)
    for step in steps:
        mark = "✓" if step.success else "✗"
        retry = step.metadata.get("retry_of")
        tag = f"（重跑第 {retry} 格）" if retry is not None else ""
        custom = " ⚑自訂prompt" if step.custom_identity else ""
        print(
            f"[{step.index}] {mark} {step.role:<9}{step.provider:<8}"
            f"{step.event_type:<16}{step.seconds:>6.0f}s{custom} {tag}"
        )
        print(f"      問：{first_line(step.prompt)}")
        print(f"      答：{first_line(step.output)}")
    show_skipped_steps(project, run_id)
    show_context(project, run_id)
    show_memory_summary(project, run_id)
    print(RULE)
    ending = _ending_of(project, run_id)
    if not ending.startswith("COMPLETED"):
        # **不是只有「某一格失敗」才要講話。**被駁到修正上限的執行每一格
        # 都成功，而它不是一次做完的工作。
        print(f"⚠ 這一輪的收場是：{ending}")
    failure = StepLog(project).last_failure(run_id)
    if failure is not None:
        print(f"最後一個失敗的是第 {failure.index} 格（{failure.event_type}）。")
        print(f"看完整內容：--step {failure.index}")
        print(f"重跑那一格：--retry {failure.index} --yes　（會花錢）")
    return 0


def show_step(project: Path, run_id: str, index: int) -> int:
    step = next(
        (s for s in StepLog(project).read(run_id) if s.index == index), None
    )
    if step is None:
        print(f"{run_id} 沒有第 {index} 格。")
        return 1

    print(f"第 {index} 格　{step.role} / {step.provider} / {step.event_type}")
    print(f"開始 {step.started_at}　結束 {step.finished_at}"
          f"　{'成功' if step.success else '失敗'}")
    if step.custom_identity:
        print("⚑ 這一格用的是自訂的「你是⋯⋯」，不是出廠設定。"
              "排查失敗原因時先把它換回預設試一次。")
    print(RULE)
    print("送出去的 prompt：")
    print(RULE)
    print(step.prompt)
    print(RULE)
    print("拿回來的輸出：")
    print(RULE)
    print(step.output or "（空）")
    return 0


def retry_step(project: Path, run_id: str, index: int, confirmed: bool) -> int:
    step = next(
        (s for s in StepLog(project).read(run_id) if s.index == index), None
    )
    if step is None:
        print(f"{run_id} 沒有第 {index} 格。")
        return 1

    print(f"要重跑的是第 {index} 格：{step.role} 由 {step.provider} 執行"
          f"（{step.event_type}）")
    print(f"prompt 長度 {len(step.prompt):,} 字，會原封不動送出去。")
    print("這會呼叫一次 CLI，也就是會花錢。")
    if not confirmed:
        print()
        print("沒有加 --yes，到此為止。確定要跑的話再加上 --yes。")
        return 0

    settings = AISettingsStore(project).load_project()
    # The line-up **that run recorded**, not whatever the composer holds now.
    # Retrying with today's line-up would re-dispatch the recorded prompt to
    # whoever happens to sit there today; `Orchestrator.retry_step` refuses
    # that, and it can only refuse if it is given the run's own line-up to
    # compare against.
    recorded = next(
        (run for run in TimelineStore(project).list_runs() if run.run_id == run_id),
        None,
    )
    orchestrator = Orchestrator(
        project,
        run_id=run_id,
        settings=settings,
        lineup=recorded.lineup if recorded is not None else None,
    )
    try:
        result = orchestrator.retry_step(index)
    except ValueError as exc:
        print()
        print(f"沒有重跑：{exc}")
        return 1

    print()
    print(RULE)
    print("成功" if result.success else "失敗")
    print(RULE)
    print(result.stdout or result.stderr or "（沒有輸出）")
    print(RULE)
    print(f"已記錄成新的一格，用 {run_id} 再看一次就會看到它。")
    return 0 if result.success else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="看一輪執行裡每一格被問了什麼、回了什麼。"
    )
    parser.add_argument("project", type=Path, help="專案資料夾")
    parser.add_argument("run_id", nargs="?", help="要看哪一輪；省略就列出全部")
    parser.add_argument("--step", type=int, help="印出這一格完整的 prompt 與輸出")
    parser.add_argument("--retry", type=int, help="重跑這一格（會花錢）")
    parser.add_argument(
        "--memory",
        action="store_true",
        help="印出這一輪對共同記憶做的完整差異",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="確認要花錢。沒有這個旗標時 --retry 只會印出將要做什麼",
    )
    args = parser.parse_args(argv)

    project = args.project.resolve()
    if not (project / ".aicc").is_dir():
        print(f"{project} 看起來不是 AICC 專案（沒有 .aicc 資料夾）。")
        return 1

    if args.run_id is None:
        return list_runs(project)
    if args.retry is not None:
        return retry_step(project, args.run_id, args.retry, args.yes)
    if args.memory:
        return show_memory_diff(project, args.run_id)
    if args.step is not None:
        return show_step(project, args.run_id, args.step)
    return show_run(project, args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
