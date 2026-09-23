"""A record of how much context each run actually carried.

The dashboard used to show a "節省 context 38%" figure that nothing computed.
It was removed rather than left in, because a fabricated number in a panel
about trust is worse than a missing one. This is what has to exist before that
box can come back: a record, written as runs happen, of two things that are
genuinely measurable.

**What was sent.** `ProjectKnowledgeStore.context_stats()` already breaks a run
down into contract / shared memory / documents / raw evidence. That breakdown
was computed on every run and then thrown away. Here it is kept.

**What was not re-sent.** This is the only honest reading of "saved". The
orchestrator sends the project context once per CLI session and deliberately
skips it on later rounds, telling the model to reuse what it already has. The
amount skipped is not an estimate of some counterfactual - it is the exact size
of the payload that a naive implementation would have sent again, and the code
knows it because it sent it a moment ago.

What is deliberately *not* recorded is "how many tokens you saved by using
AICC". That would need a version of the same work done without AICC to compare
against, and no such number exists. Anything reported here can be traced to a
byte count taken at the moment it happened.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

LEDGER_FILE = "context-ledger.json"

# Runs are small records, but a long-lived project would grow this file without
# bound. The dashboard shows recent activity, so old entries have no reader.
MAX_ENTRIES = 200

# Chars per token. A rough divisor, and named so nobody mistakes it for a real
# tokeniser: mixed CJK and code do not land on one constant. Used only where a
# figure is already labelled 估算.
CHARS_PER_TOKEN = 4

# A run makes at most 11 CLI calls (`AiSettings.expected_model_calls`), and a
# call can report more than one reading, so this is generous. It exists only so
# a pathological run cannot grow one entry without bound.
MAX_USAGE_READINGS = 64


@dataclass
class RunContextRecord:
    """One run's context accounting."""

    run_id: str
    at: str
    profile: str = ""
    contract_characters: int = 0
    memory_characters: int = 0
    document_characters: int = 0
    raw_evidence_characters: int = 0
    total_characters: int = 0
    estimated_tokens: int = 0
    # Characters the orchestrator chose not to send again on a later round.
    resend_skipped_characters: int = 0
    resend_skipped_rounds: int = 0
    # What the CLIs reported about themselves, one entry per reading, stored
    # verbatim. See `record_vendor_usage` for why there is no total beside it.
    usage_readings: list[dict[str, Any]] = field(default_factory=list)
    # How many CLI calls reported usage, out of how many were made. Kept as two
    # numbers so "the CLI said zero" and "the CLI said nothing" cannot be
    # mistaken for each other - a silently incomplete total is the failure this
    # whole record exists to avoid.
    usage_reported_calls: int = 0
    usage_total_calls: int = 0

    @property
    def sent_characters(self) -> int:
        return self.total_characters

    @property
    def would_have_sent(self) -> int:
        """What a version with no session reuse would have transmitted."""
        return self.total_characters + self.resend_skipped_characters


class ContextLedger:
    """Append-only-ish store at `.aicc/context-ledger.json`.

    A run is updated in place while it is running - the sent figures land at
    the start, the skipped ones accumulate as rounds go by - so writes are
    read-modify-write rather than pure appends.
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = Path(project_path)
        self.path = self.project_path / ".aicc" / LEDGER_FILE

    # ---- reading ---------------------------------------------------------
    def _load(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A damaged ledger must not stop a run. It is a record of runs, not
            # an input to them, so the worst case of ignoring it is a gap.
            return []
        runs = payload.get("runs")
        return runs if isinstance(runs, list) else []

    def records(self, limit: int | None = None) -> list[RunContextRecord]:
        """Most recent first."""
        known = {f for f in RunContextRecord.__dataclass_fields__}
        out: list[RunContextRecord] = []
        for raw in reversed(self._load()):
            if not isinstance(raw, dict) or "run_id" not in raw:
                continue
            # Unknown keys are dropped rather than raising: a ledger written by
            # a newer version has to stay readable by an older one.
            out.append(RunContextRecord(**{k: v for k, v in raw.items() if k in known}))
            if limit is not None and len(out) >= limit:
                break
        return out

    def summary(self, days: int = 30) -> dict[str, int]:
        """Totals over the last `days`, for the dashboard's rail box."""
        cutoff = datetime.now() - timedelta(days=days)
        runs = 0
        sent = skipped = tokens = 0
        for record in self.records():
            try:
                when = datetime.fromisoformat(record.at)
            except ValueError:
                continue
            if when < cutoff:
                continue
            runs += 1
            sent += record.total_characters
            skipped += record.resend_skipped_characters
            tokens += record.estimated_tokens
        return {
            "runs": runs,
            "sent_characters": sent,
            "skipped_characters": skipped,
            "estimated_tokens": tokens,
            "estimated_tokens_skipped": skipped // CHARS_PER_TOKEN,
        }

    # ---- comparing -------------------------------------------------------
    def usage_peak(self, run_id: str) -> int:
        """The largest single reading in a run, as input+output tokens.

        **A comparable statistic, not the run's total.** Deliberately the max of
        one reading rather than a sum, because a sum is exactly what the two
        unverified semantics forbid (see `record_vendor_usage`).

        Why this one is safe to compare across runs: whichever reading is
        correct, the same transformation applies to every run, so the *ratio*
        between two runs survives it. Under the cumulative reading the peak is
        the session's running total; under the per-turn reading it is the
        biggest single turn. Both grow with how much the run did. The absolute
        number means little on its own, which is why nothing displays it.

        input+output are added because they come from one reading - the vendor's
        own two numbers for the same moment, the same pair the ecosystem's own
        `jq` idiom adds. Cached is left out: whether it sits inside `input` is
        the other open question, and including it would change the statistic
        depending on the answer.
        """
        best = 0
        for record in self.records():
            if record.run_id != run_id:
                continue
            for reading in record.usage_readings:
                numbers = reading.get("numbers") or {}
                total = int(numbers.get("input_tokens", 0) or 0) + int(
                    numbers.get("output_tokens", 0) or 0
                )
                best = max(best, total)
        return best

    def usage_spike(
        self,
        run_id: str,
        window: int = 5,
        threshold: float = 2.0,
    ) -> dict[str, float] | None:
        """`{"factor": f, "window": n}` when this run is unusually large.

        The one honest thing that can be said about token usage before the two
        semantics are settled: not "this cost X" but "this is N times your
        recent normal". A ratio cancels the unknown; an absolute does not.

        Returns None - not a factor of 1 - when there is nothing to say: fewer
        than `window` earlier runs to compare against, a zero median, or a run
        that is simply normal. 「沒有平常可以比」 is a real state and must not
        be rendered as a reassuring number.
        """
        peak = self.usage_peak(run_id)
        if peak <= 0:
            return None

        earlier: list[int] = []
        for record in self.records():  # most recent first
            if record.run_id == run_id or not record.usage_readings:
                continue
            earlier.append(self.usage_peak(record.run_id))
            if len(earlier) >= window:
                break
        if len(earlier) < window:
            return None

        ordered = sorted(earlier)
        middle = len(ordered) // 2
        median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2
        )
        if median <= 0:
            return None

        factor = peak / median
        if factor < threshold:
            return None
        return {"factor": round(factor, 1), "window": len(ordered)}

    # ---- writing ---------------------------------------------------------
    def _write(self, runs: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "runs": runs[-MAX_ENTRIES:]}
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    def _mutate(self, run_id: str, change) -> None:
        if not run_id:
            return
        runs = self._load()
        for entry in runs:
            if entry.get("run_id") == run_id:
                change(entry)
                break
        else:
            entry = asdict(RunContextRecord(run_id=run_id, at=datetime.now().isoformat()))
            change(entry)
            runs.append(entry)
        try:
            self._write(runs)
        except OSError:
            # Same reasoning as _load: bookkeeping never fails a run.
            pass

    def record_sent(self, run_id: str, stats: dict[str, int], profile: str = "") -> None:
        """Store one run's context breakdown, as measured by context_stats()."""

        def apply(entry: dict[str, Any]) -> None:
            entry["profile"] = profile
            for key in (
                "contract_characters",
                "memory_characters",
                "document_characters",
                "raw_evidence_characters",
                "total_characters",
                "estimated_tokens",
            ):
                entry[key] = int(stats.get(key, 0) or 0)

        self._mutate(run_id, apply)

    def record_vendor_usage(
        self, run_id: str, readings: list[dict[str, Any]]
    ) -> None:
        """Store one CLI call's own usage report, and the fact that it was made.

        Called once per CLI call, readings or not. An empty list still advances
        `usage_total_calls`, so a run whose CLI told us nothing looks different
        from one that genuinely used nothing.

        **No total is computed, here or later.** The readings are stored as the
        CLI worded them, tagged with the call and session they came from,
        because two things about them are still unverified and need opposite
        arithmetic - whether `cached_input_tokens` sits inside `input_tokens`,
        and whether `usage` is this turn's or the session's running total
        (openai/codex#17539 says the latter). Summing under the wrong reading
        produces a wrong number that looks entirely plausible, which is the one
        outcome ROADMAP P12 rules out. Keeping the raw readings means the
        aggregate can be computed retroactively once a real run settles it.

        This is also why the readings come from the parsed result rather than
        from the `*-events.jsonl` artifacts: `save_artifact` writes one file per
        `event_type` and overwrites, while `run_codex_plan` reuses
        `event_type="planning"` for every non-final round. Reading them back off
        disk would quietly keep one round out of N.
        """

        def apply(entry: dict[str, Any]) -> None:
            entry["usage_total_calls"] = (
                int(entry.get("usage_total_calls", 0) or 0) + 1
            )
            if not readings:
                return
            entry["usage_reported_calls"] = (
                int(entry.get("usage_reported_calls", 0) or 0) + 1
            )
            stored = entry.get("usage_readings")
            if not isinstance(stored, list):
                stored = []
            stored.extend(readings)
            entry["usage_readings"] = stored[-MAX_USAGE_READINGS:]

        self._mutate(run_id, apply)

    def record_resend_skipped(self, run_id: str, characters: int) -> None:
        """Note a round that reused the CLI session instead of re-sending."""
        if characters <= 0:
            return

        def apply(entry: dict[str, Any]) -> None:
            entry["resend_skipped_characters"] = (
                int(entry.get("resend_skipped_characters", 0) or 0) + characters
            )
            entry["resend_skipped_rounds"] = (
                int(entry.get("resend_skipped_rounds", 0) or 0) + 1
            )

        self._mutate(run_id, apply)
