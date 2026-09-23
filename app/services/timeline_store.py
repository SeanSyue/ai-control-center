from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.core.lineup import Lineup


@dataclass(frozen=True)
class TimelineEvent:
    event_id: str
    timestamp: str
    run_id: str
    source: str
    event_type: str
    content: str
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunInfo:
    """What a run was, including who ran it.

    `lineup` is the record of which agents took part and in what order. It
    replaces the two named session-id fields this class used to carry: those
    could not express two positions held by the same vendor, and could not
    express a third position at all (ROADMAP P10).

    `claude_session_id` / `codex_session_id` are still readable and still mean
    what they meant, but they are now derived - there is one place the answer
    lives. They are lossy by construction once a line-up has two slots from the
    same vendor, which is the point: code that needs to know *which* agent has
    to ask by role.
    """

    run_id: str
    created_at: str
    updated_at: str
    status: str
    task_preview: str
    lineup: Lineup = field(default_factory=Lineup)

    @property
    def claude_session_id(self) -> str:
        return self.lineup.session_for_provider("claude")

    @property
    def codex_session_id(self) -> str:
        return self.lineup.session_for_provider("codex")


class TimelineStore:
    """Append-only project timeline plus per-run artifacts."""

    VERSION = 1
    VALID_SOURCES = {"claude", "codex", "aicc", "user"}

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path.expanduser().resolve()
        self.aicc_path = self.project_path / ".aicc"
        self.timeline_dir = self.aicc_path / "timeline"
        self.runs_dir = self.aicc_path / "runs"
        self.timeline_path = self.timeline_dir / "timeline.jsonl"
        self._lock = threading.Lock()
        self.timeline_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_gitignore()

    def create_run(
        self,
        task: str,
        claude_session_id: str = "",
        codex_session_id: str = "",
        run_id: str = "",
        lineup: Lineup | None = None,
    ) -> str:
        """Start a run. Pass `lineup` to record who is on it.

        The two named session-id arguments still work and still mean the
        classic Claude-writes/Codex-reviews pairing - they build that line-up.
        They are kept because every current caller uses them, and dropping them
        would have made this change touch the whole workflow at once.
        """
        if run_id:
            existing = self.runs_dir / run_id / "run.json"
            if existing.is_file():
                return run_id

        now = self._now()
        generated = run_id.strip() or (
            datetime.now().strftime("%Y%m%d_%H%M%S")
            + "_"
            + uuid.uuid4().hex[:8]
        )
        run_dir = self.runs_dir / generated
        run_dir.mkdir(parents=True, exist_ok=True)

        task_text = task.strip()
        resolved = (
            lineup
            if lineup is not None
            else Lineup.classic(claude_session_id, codex_session_id)
        )
        run_payload = {
            "version": self.VERSION,
            "run_id": generated,
            "project_path": str(self.project_path),
            "created_at": now,
            "updated_at": now,
            "status": "RUNNING",
            "task_preview": self._preview(task_text),
            "agents": resolved.to_list(),
            # Still written, so a run.json stays readable by a build that
            # predates the line-up. Derived from `agents`, never set beside it:
            # two keys that can disagree is how a file starts lying.
            "claude_session_id": resolved.session_for_provider("claude"),
            "codex_session_id": resolved.session_for_provider("codex"),
        }
        self._atomic_write_json(run_dir / "run.json", run_payload)
        (run_dir / "task.md").write_text(task_text + "\n", encoding="utf-8")
        return generated

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        claude_session_id: str | None = None,
        codex_session_id: str | None = None,
        lineup: Lineup | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        run_path = self.runs_dir / run_id / "run.json"
        payload = self._load_json(run_path, {})
        if not isinstance(payload, dict):
            payload = {}

        payload.setdefault("version", self.VERSION)
        payload.setdefault("run_id", run_id)
        payload.setdefault("project_path", str(self.project_path))
        payload.setdefault("created_at", self._now())
        payload["updated_at"] = self._now()

        if status is not None:
            payload["status"] = status

        # Whichever way the caller names them, the line-up is what gets stored
        # and the two flat keys are re-derived from it afterwards.
        current = self._lineup_from_payload(payload)
        if lineup is not None:
            current = lineup
        else:
            if claude_session_id is not None:
                current = self._replace_provider_session(
                    current, "claude", claude_session_id.strip()
                )
            if codex_session_id is not None:
                current = self._replace_provider_session(
                    current, "codex", codex_session_id.strip()
                )
        if lineup is not None or claude_session_id is not None or codex_session_id is not None:
            payload["agents"] = current.to_list()
            payload["claude_session_id"] = current.session_for_provider("claude")
            payload["codex_session_id"] = current.session_for_provider("codex")
        if extra:
            payload.update(extra)

        self._atomic_write_json(run_path, payload)

    def append(
        self,
        run_id: str,
        source: str,
        event_type: str,
        content: str,
        *,
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
        timestamp: str = "",
    ) -> TimelineEvent:
        normalized_source = source.strip().lower()
        if normalized_source not in self.VALID_SOURCES:
            normalized_source = "aicc"

        event = TimelineEvent(
            event_id=str(uuid.uuid4()),
            timestamp=timestamp.strip() or self._now(),
            run_id=run_id.strip(),
            source=normalized_source,
            event_type=event_type.strip() or "message",
            content=content.rstrip(),
            session_id=session_id.strip(),
            metadata=dict(metadata or {}),
        )
        encoded = json.dumps(asdict(event), ensure_ascii=False)

        with self._lock:
            self.timeline_path.parent.mkdir(parents=True, exist_ok=True)
            with self.timeline_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded + "\n")

            if run_id:
                run_timeline = self.runs_dir / run_id / "timeline.jsonl"
                run_timeline.parent.mkdir(parents=True, exist_ok=True)
                with run_timeline.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(encoded + "\n")

        return event

    def save_artifact(
        self,
        run_id: str,
        name: str,
        content: str | bytes,
    ) -> Path:
        safe_name = Path(name).name
        if not safe_name:
            raise ValueError("Artifact name cannot be empty")

        destination = self.runs_dir / run_id / safe_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_name(destination.name + ".tmp")

        if isinstance(content, bytes):
            temp_path.write_bytes(content)
        else:
            temp_path.write_text(content, encoding="utf-8")

        temp_path.replace(destination)
        return destination

    def read_artifact(self, run_id: str, name: str) -> str:
        """An artifact this run saved, or "" if it never did.

        Returns "" rather than raising for a missing file: callers use this to
        ask 「這一輪已經產生過這個了嗎」, and for that question "no" is an
        answer, not an error. A real read failure (permissions, bad encoding)
        still raises, because that one is not the same as "not there".
        """
        path = self.runs_dir / run_id / Path(name).name
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def finalize_run(self, run_id: str, result: dict[str, Any]) -> None:
        self.save_artifact(
            run_id,
            "result.json",
            json.dumps(result, ensure_ascii=False, indent=2),
        )
        self.update_run(
            run_id,
            status=str(result.get("workflow_status", "UNKNOWN")),
            claude_session_id=str(result.get("claude_session_id", "")),
            codex_session_id=str(result.get("codex_session_id", "")),
            extra={
                "review_status": str(result.get("review_status", "")),
                "fix_attempts": int(result.get("fix_attempts", 0)),
                "ai_settings": result.get("ai_settings", {}),
            },
        )

    @staticmethod
    def _lineup_from_payload(payload: dict[str, Any]) -> Lineup:
        """Read a run's line-up, falling back to the two flat keys.

        Runs written before P10 have no `agents`, and there are real ones on
        disk right now - reading them as "nobody was on this run" would erase
        history that is still there in the file.
        """
        stored = Lineup.from_list(payload.get("agents"))
        if stored:
            return stored
        return Lineup.classic(
            str(payload.get("claude_session_id", "")),
            str(payload.get("codex_session_id", "")),
        )

    @staticmethod
    def _replace_provider_session(
        lineup: Lineup,
        provider: str,
        session_id: str,
    ) -> Lineup:
        """Set the session id of the first slot held by `provider`.

        The compatibility path, for callers still saying "the Claude one". It
        can only ever reach the first such slot, which is exactly why callers
        that compose their own line-up should pass `lineup=` instead.
        """
        slot = lineup.first_for_provider(provider)
        if slot is None:
            return lineup
        return lineup.with_session(slot.role, session_id)

    def list_runs(self) -> list[RunInfo]:
        result: list[RunInfo] = []
        if not self.runs_dir.is_dir():
            return result

        for run_path in self.runs_dir.iterdir():
            if not run_path.is_dir():
                continue

            payload = self._load_json(run_path / "run.json", {})
            if not isinstance(payload, dict):
                continue

            run_id = str(payload.get("run_id", run_path.name)).strip()
            if not run_id:
                continue

            result.append(
                RunInfo(
                    run_id=run_id,
                    created_at=str(payload.get("created_at", "")),
                    updated_at=str(payload.get("updated_at", "")),
                    status=str(payload.get("status", "UNKNOWN")),
                    task_preview=str(payload.get("task_preview", "")),
                    lineup=self._lineup_from_payload(payload),
                )
            )

        return sorted(
            result,
            key=lambda item: item.updated_at or item.created_at,
            reverse=True,
        )

    def load_events(
        self,
        *,
        run_id: str = "",
        sources: Iterable[str] | None = None,
        limit: int = 2000,
    ) -> list[TimelineEvent]:
        path = (
            self.runs_dir / run_id / "timeline.jsonl"
            if run_id
            else self.timeline_path
        )
        if not path.is_file():
            return []

        source_filter = {
            value.strip().lower()
            for value in (sources or [])
            if value.strip()
        }
        events: list[TimelineEvent] = []

        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(raw, dict):
                        continue

                    source = str(raw.get("source", "aicc")).lower()
                    if source_filter and source not in source_filter:
                        continue

                    metadata = raw.get("metadata", {})
                    if not isinstance(metadata, dict):
                        metadata = {}

                    events.append(
                        TimelineEvent(
                            event_id=str(raw.get("event_id", "")),
                            timestamp=str(raw.get("timestamp", "")),
                            run_id=str(raw.get("run_id", "")),
                            source=source,
                            event_type=str(raw.get("event_type", "message")),
                            content=str(raw.get("content", "")),
                            session_id=str(raw.get("session_id", "")),
                            metadata=metadata,
                        )
                    )
        except OSError:
            return []

        events.sort(key=lambda item: item.timestamp)
        if limit > 0 and len(events) > limit:
            return events[-limit:]
        return events

    def _ensure_gitignore(self) -> None:
        ignore_path = self.aicc_path / ".gitignore"
        required = [
            "workflow-result.json",
            "sync-state.json",
            "conflicts/",
            "sessions.json",
            "ai-settings.json",
            "runs/",
            "timeline/",
        ]
        existing: list[str] = []
        if ignore_path.is_file():
            try:
                existing = ignore_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            except OSError:
                existing = []

        changed = False
        normalized = {line.strip() for line in existing}
        for item in required:
            if item not in normalized:
                existing.append(item)
                normalized.add(item)
                changed = True

        if changed or not ignore_path.exists():
            ignore_path.write_text(
                "\n".join(existing).rstrip() + "\n",
                encoding="utf-8",
            )

    @staticmethod
    def _preview(text: str, limit: int = 160) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _atomic_write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(path.name + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).astimezone().isoformat()
