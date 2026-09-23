"""What the CLIs say they used, recorded without being interpreted.

The point of these tests is not arithmetic - it is the refusal to do arithmetic.
Two things about Codex's `turn.completed` usage are unverified, and the two
readings need opposite handling:

* is `cached_input_tokens` inside `input_tokens`, or extra? (our one real
  sample reads 16931 / 5504 - 25% apart depending on the answer)
* is `usage` this turn's, or the session's running total? openai/codex#17539
  reports cumulative session totals. Cumulative means summing our calls
  double-counts; per-turn means keeping the last under-counts.

So the contract under test is: transcribe every scalar under the name the CLI
gave it, tag it with the call it came from, and produce no total. A wrong total
here would look completely plausible, which is exactly what ROADMAP P12 rules
out.

The sample in `CODEX_TURN_COMPLETED` is real - copied from
`sandbox/demo_project/.aicc/runs/20260808_232045_0ea2fb1e/codex-planning_final-events.jsonl`,
written by a real Codex run on 2026-08-08.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.adapters.cli_runner import CliResult
from app.core.orchestrator import Orchestrator
from app.core.orchestrator_parsing import (
    extract_usage_readings,
    normalize_codex_result,
)
from app.services.context_ledger import MAX_USAGE_READINGS, ContextLedger

CODEX_TURN_COMPLETED = (
    '{"type":"turn.completed","usage":{"input_tokens":16931,'
    '"cached_input_tokens":5504,"cache_write_input_tokens":0,'
    '"output_tokens":698,"reasoning_output_tokens":16}}'
)
CODEX_THREAD_STARTED = (
    '{"type":"thread.started","thread_id":"019fe1f6-56fd-7721-8a19-59dfd6bf65bb"}'
)


def codex_result(*lines: str) -> CliResult:
    result = CliResult(
        command="codex",
        success=True,
        return_code=0,
        stdout="\n".join(lines),
        stderr="",
    )
    return normalize_codex_result(result)


def readings_for(*lines: str) -> list[dict]:
    return extract_usage_readings(
        codex_result(*lines),
        provider="codex",
        event_type="planning",
        session_id="thread-1",
    )


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 任務\n記錄廠商回報的用量。\n", encoding="utf-8"
    )
    return project


# ---- transcription ------------------------------------------------------


def test_the_real_codex_sample_is_transcribed_field_for_field() -> None:
    readings = readings_for(CODEX_THREAD_STARTED, CODEX_TURN_COMPLETED)

    assert len(readings) == 1
    assert readings[0]["numbers"] == {
        "input_tokens": 16931,
        "cached_input_tokens": 5504,
        "cache_write_input_tokens": 0,
        "output_tokens": 698,
        "reasoning_output_tokens": 16,
    }
    assert readings[0]["event"] == "turn.completed"
    assert readings[0]["provider"] == "codex"
    assert readings[0]["event_type"] == "planning"


def test_a_field_the_cli_adds_later_is_kept_rather_than_dropped() -> None:
    # An allowlist would silently discard whatever the next CLI version starts
    # reporting. Transcription cannot be wrong, so there is nothing to filter.
    readings = readings_for(
        '{"type":"turn.completed","usage":{"input_tokens":10,'
        '"some_future_token_field":7}}'
    )

    assert readings[0]["numbers"] == {
        "input_tokens": 10,
        "some_future_token_field": 7,
    }


def test_money_the_cli_reports_itself_is_transcribed() -> None:
    # Repeating a vendor's own figure is allowed; multiplying tokens by a price
    # list we maintain is not. See ROADMAP P12.
    readings = readings_for(
        '{"type":"result","total_cost_usd":0.0432,"usage":{"input_tokens":10}}'
    )

    assert readings[0]["numbers"]["total_cost_usd"] == 0.0432


def test_a_non_numeric_usage_value_is_not_stored_as_a_number() -> None:
    readings = readings_for(
        '{"type":"turn.completed","usage":{"input_tokens":10,'
        '"model":"gpt-5.5","details":{"a":1}}}'
    )

    assert readings[0]["numbers"] == {"input_tokens": 10}
    assert readings[0]["unread_keys"] == ["details", "model"]


def test_a_flag_is_not_counted_as_a_token_count() -> None:
    # bool is an int subclass, so an unguarded isinstance check would store
    # `"cache_hit": True` as the number 1 and let it be summed later.
    readings = readings_for(
        '{"type":"turn.completed","usage":{"input_tokens":10,"cache_hit":true}}'
    )

    assert readings[0]["numbers"] == {"input_tokens": 10}
    assert readings[0]["unread_keys"] == ["cache_hit"]


# ---- streams that report nothing ---------------------------------------


def test_a_plain_text_stream_yields_no_readings() -> None:
    # Three of the five `*-events.jsonl` files on this machine are not JSON at
    # all - they hold an "ASK_USER:" line. A `.jsonl` name is not a guarantee.
    readings = readings_for(
        "ASK_USER: 要覆寫 demo.py 嗎？現有檔案有手動修改過的痕跡。"
    )

    assert readings == []


def test_a_truncated_stream_yields_no_readings() -> None:
    # The run was killed before the turn finished. Two of the five real files
    # on this machine look exactly like this.
    readings = readings_for(CODEX_THREAD_STARTED, '{"type":"turn.started"}')

    assert readings == []


# ---- the ledger ---------------------------------------------------------


def test_a_call_that_reported_nothing_is_still_counted(tmp_path: Path) -> None:
    # "the CLI said zero" and "the CLI said nothing" must not look the same. A
    # total assembled from an unknown number of silent calls is the plausible
    # wrong number this whole record exists to avoid.
    ledger = ContextLedger(tmp_path)

    ledger.record_vendor_usage("run-1", readings_for(CODEX_TURN_COMPLETED))
    ledger.record_vendor_usage("run-1", [])
    ledger.record_vendor_usage("run-1", [])

    record = ledger.records()[0]
    assert record.usage_total_calls == 3
    assert record.usage_reported_calls == 1
    assert len(record.usage_readings) == 1


def test_two_calls_sharing_an_event_type_both_land(tmp_path: Path) -> None:
    # This is the case the artifact files lose. `save_artifact` overwrites
    # `codex-planning-events.jsonl`, and `run_codex_plan` uses
    # event_type="planning" for every non-final round, so parsing the artifacts
    # back off disk would keep one round out of N and look fine doing it.
    ledger = ContextLedger(tmp_path)
    first = readings_for('{"type":"turn.completed","usage":{"input_tokens":100}}')
    second = readings_for('{"type":"turn.completed","usage":{"input_tokens":200}}')

    ledger.record_vendor_usage("run-1", first)
    ledger.record_vendor_usage("run-1", second)

    stored = [r["numbers"]["input_tokens"] for r in ledger.records()[0].usage_readings]
    assert stored == [100, 200]


def test_no_total_is_stored_anywhere(tmp_path: Path) -> None:
    # The guard on the decision itself. Until the two semantics are verified,
    # 100 + 100 must not appear in the ledger under any name - neither as 200
    # (per-turn reading) nor collapsed to one 100 (cumulative reading).
    ledger = ContextLedger(tmp_path)
    reading = readings_for('{"type":"turn.completed","usage":{"input_tokens":100}}')

    ledger.record_vendor_usage("run-1", reading)
    ledger.record_vendor_usage("run-1", reading)

    raw = json.loads(ledger.path.read_text(encoding="utf-8"))
    entry = raw["runs"][0]
    scalars = [v for v in entry.values() if isinstance(v, (int, float))]
    assert 200 not in scalars
    # Both readings are still there, side by side and unmerged.
    assert len(entry["usage_readings"]) == 2


def test_readings_are_capped(tmp_path: Path) -> None:
    ledger = ContextLedger(tmp_path)
    reading = readings_for('{"type":"turn.completed","usage":{"input_tokens":1}}')

    for _ in range(MAX_USAGE_READINGS + 5):
        ledger.record_vendor_usage("run-1", reading)

    record = ledger.records()[0]
    assert len(record.usage_readings) == MAX_USAGE_READINGS
    # The calls themselves are still all counted, so the cap is visible rather
    # than silently making a run look smaller than it was.
    assert record.usage_total_calls == MAX_USAGE_READINGS + 5


def test_a_ledger_written_before_usage_existed_still_reads(tmp_path: Path) -> None:
    path = tmp_path / ".aicc" / "context-ledger.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {"version": 1, "runs": [{"run_id": "old", "at": "2026-08-01T10:00:00"}]}
        ),
        encoding="utf-8",
    )

    record = ContextLedger(tmp_path).records()[0]
    assert record.usage_readings == []
    assert record.usage_total_calls == 0


# ---- wired into the orchestrator ---------------------------------------


def test_a_real_codex_call_records_what_codex_reported(tmp_path: Path) -> None:
    orchestrator = Orchestrator(make_project(tmp_path))

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        return CliResult(
            command="codex",
            success=True,
            return_code=0,
            stdout="\n".join([CODEX_THREAD_STARTED, CODEX_TURN_COMPLETED]),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    orchestrator.run_codex_plan(task="加一個空字串的判斷")

    record = ContextLedger(orchestrator.project_path).records()[0]
    assert record.usage_total_calls == 1
    assert record.usage_reported_calls == 1
    reading = record.usage_readings[0]
    assert reading["numbers"]["input_tokens"] == 16931
    assert reading["numbers"]["cached_input_tokens"] == 5504
    assert reading["event_type"] == "planning"
    # Tagged with the session, because that is what decides the aggregation
    # once the cumulative-or-not question is settled.
    assert reading["session_id"] == "019fe1f6-56fd-7721-8a19-59dfd6bf65bb"


def test_a_claude_call_that_reports_nothing_is_still_counted(tmp_path: Path) -> None:
    # Claude's usage shape is unverified - this machine's CLI is signed out, so
    # no successful Claude output has ever been read. The wiring must therefore
    # assume nothing: a call with no usage is a counted call with no reading,
    # not a zero and not a crash.
    orchestrator = Orchestrator(make_project(tmp_path))

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps({"result": "done", "session_id": "claude-1"}),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    orchestrator.run_claude()

    record = ContextLedger(orchestrator.project_path).records()[0]
    assert record.usage_total_calls == 1
    assert record.usage_reported_calls == 0
    assert record.usage_readings == []


def test_claude_usage_is_transcribed_if_it_ever_appears(tmp_path: Path) -> None:
    # Not a claim about what Claude reports - a claim that if it reports
    # anything, it lands without a code change. This is the seam ROADMAP P12
    # step 2 needs.
    orchestrator = Orchestrator(make_project(tmp_path))

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        return CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps(
                {
                    "result": "done",
                    "session_id": "claude-1",
                    "total_cost_usd": 0.0117,
                    "usage": {"input_tokens": 4210, "output_tokens": 655},
                }
            ),
            stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    orchestrator.run_claude()

    reading = ContextLedger(orchestrator.project_path).records()[0].usage_readings[0]
    assert reading["provider"] == "claude"
    assert reading["numbers"] == {
        "input_tokens": 4210,
        "output_tokens": 655,
        "total_cost_usd": 0.0117,
    }


# ---- 「比平常兇」 ---------------------------------------------------------
#
# The one honest thing that can be said about tokens before the two semantics
# are settled. A ratio cancels the unknown that an absolute total cannot.


def _reading(tokens: int) -> list[dict]:
    return readings_for(
        '{"type":"turn.completed","usage":{"input_tokens":%d,"output_tokens":0}}'
        % tokens
    )


def _history(ledger: ContextLedger, sizes: list[int]) -> None:
    for index, size in enumerate(sizes):
        ledger.record_vendor_usage(f"run-{index}", _reading(size))


def test_the_peak_is_the_largest_reading_not_the_sum(tmp_path: Path) -> None:
    # A sum is exactly what the unverified semantics forbid. Max of one reading
    # is comparable across runs under either reading.
    ledger = ContextLedger(tmp_path)
    for size in (100, 900, 300):
        ledger.record_vendor_usage("run-1", _reading(size))

    assert ledger.usage_peak("run-1") == 900


def test_a_run_far_above_recent_normal_is_flagged(tmp_path: Path) -> None:
    ledger = ContextLedger(tmp_path)
    _history(ledger, [1000, 1000, 1000, 1000, 1000])
    ledger.record_vendor_usage("run-now", _reading(3000))

    spike = ledger.usage_spike("run-now")
    assert spike == {"factor": 3.0, "window": 5}


def test_an_ordinary_run_says_nothing(tmp_path: Path) -> None:
    # Not a factor of 1.0 - nothing at all. A reassuring number every run is
    # noise, and it would make the warning invisible when it matters.
    ledger = ContextLedger(tmp_path)
    _history(ledger, [1000, 1000, 1000, 1000, 1000])
    ledger.record_vendor_usage("run-now", _reading(1100))

    assert ledger.usage_spike("run-now") is None


def test_a_project_without_enough_history_says_nothing(tmp_path: Path) -> None:
    # 「沒有平常可以比」 is a real state. Comparing against two runs would let
    # the second run of a project's life declare a 5x spike off no evidence.
    ledger = ContextLedger(tmp_path)
    _history(ledger, [1000, 1000])
    ledger.record_vendor_usage("run-now", _reading(9000))

    assert ledger.usage_spike("run-now") is None


def test_silent_runs_do_not_crowd_out_real_history(tmp_path: Path) -> None:
    # A run whose CLI reported nothing has a peak of 0. If those count towards
    # the window they push the real runs out of it and the median collapses -
    # so a project with plenty of history would quietly stop warning at all.
    #
    # This assertion was rewritten after a negative control: the first version
    # asserted `is None` with only four real runs, and it passed with or
    # without the filter, because the empty-median guard caught it either way.
    # It proved nothing. This one fails without the filter.
    ledger = ContextLedger(tmp_path)
    _history(ledger, [1000, 1000, 1000, 1000, 1000])
    for run_id in ("silent-1", "silent-2", "silent-3"):
        ledger.record_vendor_usage(run_id, [])
    ledger.record_vendor_usage("run-now", _reading(3000))

    assert ledger.usage_spike("run-now") == {"factor": 3.0, "window": 5}


def test_the_factor_is_measured_against_the_median_not_the_mean(
    tmp_path: Path,
) -> None:
    # One freak run in the history would drag a mean up and hide the next
    # spike behind it. The median ignores it.
    ledger = ContextLedger(tmp_path)
    _history(ledger, [1000, 1000, 1000, 1000, 50000])
    ledger.record_vendor_usage("run-now", _reading(3000))

    spike = ledger.usage_spike("run-now")
    assert spike is not None and spike["factor"] == 3.0


# ---- the semantics checker ----------------------------------------------
#
# tools/verify_usage_semantics.py is what turns 「卡在沒有機器」 into one
# command. Its verdict logic is tested here because a checker that gives a
# confident wrong answer is worse than no checker.


def _verdict(series: list[int]) -> str:
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "tools" / "verify_usage_semantics.py"
    spec = importlib.util.spec_from_file_location("verify_usage_semantics", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._verdict(series)[0]


def test_a_counter_that_never_shrinks_and_leaps_reads_as_cumulative() -> None:
    # 8k → 22k → 45k: the last reading is more than everything before it put
    # together, which a per-turn counter cannot produce.
    assert _verdict([8000, 22000, 45000]) == "累計值"


def test_a_reading_that_drops_proves_per_turn() -> None:
    # A running total never goes down. One decrease settles it.
    assert _verdict([8000, 6000, 6500]) == "單輪值"


def test_one_reading_decides_nothing() -> None:
    assert _verdict([16931]) == "?"


def test_gentle_growth_is_reported_as_undecided_not_guessed() -> None:
    # Both readings grow - a per-process counter also rises because each round
    # replays a longer conversation. Saying "cumulative" here would be the
    # confident-looking wrong answer this tool exists to avoid.
    assert _verdict([10000, 11000, 12000]) == "?"
