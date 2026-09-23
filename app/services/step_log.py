"""What each seat was actually asked, and what it actually said.

ROADMAP P10 Stage C. Written because of an arithmetic fact rather than a
feature request: once a line-up can hold three seats, the chance of a whole
line finishing is the product of each seat's, and today a failure at the last
seat means paying for every seat again. Re-running **one** seat is the answer,
and nothing can re-run a step that was not recorded.

Three readers, and the third is the one that decides the shape:

1. **Re-running one seat** needs the prompt that seat was given, verbatim.
2. **Looking at what happened** needs it in the order it happened, including
   the rounds that a `save_artifact` name would have overwritten - Codex's
   planning rounds all share `event_type="planning"`, so the artifact folder
   keeps the last round of N and this keeps all of them.
3. **Debugging a custom prompt** (Stage D) needs to be able to say 「這一格
   用的不是出廠 prompt」. Without it, a failure caused by an edited prompt is
   indistinguishable from a broken build, and that misdiagnosis is expensive.

**Why not read this out of the timeline instead.** The timeline already
appends every prompt. It is the human-readable narrative of a run - free text,
mixed with CLI chatter, keyed by nothing a program can index on. Deriving a
machine-readable record from it would couple re-running to the phrasing of log
lines. Both are written from the same dispatch funnel, so they cannot disagree
about *which* steps happened; they disagree only in shape, on purpose.

Prompts are stored in full. That duplicates bytes the timeline also holds, and
that trade is deliberate: a local JSONL is cheap, and a re-run that
reconstructs its prompt from a second source is a re-run that can quietly send
something different from what failed.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

STEP_LOG_FILE = "steps.jsonl"

# A run makes at most 11 model calls today (`AIExecutionSettings`'s own
# estimate), and planning rounds add a few more. The cap exists so a runaway
# loop cannot fill a disk, not because any real run approaches it.
MAX_STEPS = 200


@dataclass(frozen=True)
class StepRecord:
    """One dispatch to one seat."""

    index: int
    role: str
    provider: str
    event_type: str
    read_only: bool
    prompt: str
    output: str
    success: bool
    started_at: str
    finished_at: str
    # True when this step's 「你是⋯⋯」 line was replaced by a user-supplied one
    # (ROADMAP P10 Stage D). Recorded at dispatch because it is only knowable
    # then: the prompt text alone cannot say whether its opening line is the
    # shipped one or an edit that happens to look like it.
    custom_identity: bool = False
    # Free-form, for whatever the step already carried (`attempt_number`,
    # `round_number`). Kept rather than flattened: these differ per step type
    # and inventing columns for each would make this file a schema to maintain.
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def seconds(self) -> float:
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.finished_at)
        except ValueError:
            return 0.0
        return max(0.0, (end - start).total_seconds())


class StepLog:
    """Append-only, one file per run.

    Per run rather than per project: a re-run reads exactly one run's steps,
    and a per-project file would need every reader to filter - which is the
    kind of filter that gets forgotten once and shows another run's prompt.
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = Path(project_path).resolve()
        self.runs_dir = self.project_path / ".aicc" / "runs"

    def path_for(self, run_id: str) -> Path:
        return self.runs_dir / run_id / STEP_LOG_FILE

    def append(self, run_id: str, record: StepRecord) -> None:
        """Write one step. Never raises into the caller.

        A run must not die because its own bookkeeping could not be written -
        the bookkeeping exists to make failures cheaper, and turning it into a
        new way to fail would be worse than not having it. A lost line costs
        the ability to re-run that one seat, which is exactly what the state
        was before this file existed.
        """
        if not run_id:
            return
        try:
            path = self.path_for(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(asdict(record), ensure_ascii=False) + "\n"
                )
        except Exception:
            return

    def next_index(self, run_id: str) -> int:
        """The index the next step gets.

        Read off the file rather than counted in memory, so a run resumed in a
        **new process** continues the numbering instead of restarting at 0 and
        producing two step 0s for one run.
        """
        return len(self.read(run_id))

    def read(self, run_id: str) -> list[StepRecord]:
        """Every recorded step, in order. Unreadable lines are skipped.

        One corrupt line costs that step, not the whole log - the same reason
        `AgentSlot.from_dict` returns None instead of raising. A half-written
        last line is the realistic case: the process was killed mid-append.
        """
        path = self.path_for(run_id)
        if not path.is_file():
            return []
        records: list[StepRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            try:
                records.append(
                    StepRecord(
                        index=int(raw.get("index", len(records))),
                        role=str(raw.get("role", "")),
                        provider=str(raw.get("provider", "")),
                        event_type=str(raw.get("event_type", "")),
                        read_only=bool(raw.get("read_only", False)),
                        prompt=str(raw.get("prompt", "")),
                        output=str(raw.get("output", "")),
                        success=bool(raw.get("success", False)),
                        started_at=str(raw.get("started_at", "")),
                        finished_at=str(raw.get("finished_at", "")),
                        custom_identity=bool(raw.get("custom_identity", False)),
                        metadata=(
                            raw.get("metadata")
                            if isinstance(raw.get("metadata"), dict)
                            else {}
                        ),
                    )
                )
            except (TypeError, ValueError):
                continue
        return records

    def last_failure(self, run_id: str) -> StepRecord | None:
        """The step a re-run would start from, or None if nothing failed.

        The **last** failure rather than the first: a run can carry on past a
        failed step (a failed test triggers a fix round), so the earliest
        failure is usually one that was already dealt with.
        """
        for record in reversed(self.read(run_id)):
            if not record.success:
                return record
        return None
