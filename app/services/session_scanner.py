from __future__ import annotations

import json
from collections import deque
import os
import queue
import re
import shutil
import sqlite3
import subprocess

from app.adapters.no_console import NO_WINDOW
from app.i18n import t
from app.core import prompt_markers
from app.services import not_ours
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionInfo:
    provider: str
    session_id: str
    title: str
    project_path: str = ""
    created_at: str = ""
    updated_at: str = ""
    source: str = "local"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FormatHealth:
    """Whether this build still understands the provider's on-disk format."""

    checked: int
    understood: int
    unrecognised: tuple[str, ...] = ()
    # 哪一家的格式。兩家各自改版，訊息也要各自講——「本機對話格式看不懂了」
    # 而不說是誰，使用者第一件事會是去翻錯的那一邊。
    provider: str = "claude"

    @property
    def looks_changed(self) -> bool:
        """True when the shape on disk stopped being one this build reads.

        Needs a few files before it says anything: one unreadable transcript
        is ordinary (a session with no user turn), and a warning that fires on
        one file is a warning people learn to dismiss. Two thirds of a sample
        is a pattern, not an oddity.
        """
        if self.checked < 3:
            return False
        return self.understood * 3 <= self.checked


@dataclass(frozen=True)
class SessionMessage:
    provider: str
    session_id: str
    role: str
    content: str
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionScanner:
    """Discover local Claude Code and Codex sessions.

    Codex discovery uses the documented app-server thread APIs first. Claude
    Code currently has no machine-readable CLI session-list command, so the
    scanner reads the local JSONL history defensively. When either provider
    changes its local format, AICC-created sessions still remain selectable
    through .aicc/sessions.json.
    """

    MAX_FILES = 400
    MAX_BYTES_PER_FILE = 8 * 1024 * 1024
    MAX_MESSAGES = 500
    TITLE_KEYS = {
        "name",
        "title",
        "displayName",
        "display_name",
        "threadName",
        "thread_name",
        "sessionName",
        "session_name",
        "conversationTitle",
        "conversation_title",
        "projectName",
        "project_name",
    }
    UUID_PATTERN = re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )

    def scan_claude(
        self,
        project_path: Path,
        include_all: bool = False,
    ) -> list[SessionInfo]:
        project = project_path.expanduser().resolve()
        roots = self._claude_roots()
        candidates = self._newest_jsonl_files(roots)
        task_subjects = self._claude_task_subjects()
        title_index = self._claude_title_index()
        sessions: list[SessionInfo] = []

        for path in candidates:
            summary = self._read_claude_summary(path)
            if summary is None:
                continue

            session_id, title, cwd, created_at, updated_at, title_source = summary
            if not session_id:
                continue

            indexed = title_index.get(session_id)
            title, title_source, cwd, created_at, updated_at = (
                self._apply_session_index(
                    indexed, title, title_source, cwd, created_at, updated_at
                )
            )

            task_subject = task_subjects.get(session_id, "")
            if task_subject and title_source in {"", "fallback", "message_preview"}:
                title = task_subject
                title_source = "task_subject"

            matches = self._paths_match(cwd, project)
            if cwd and not matches and not include_all:
                continue

            sessions.append(
                SessionInfo(
                    provider="claude",
                    session_id=session_id,
                    title=title or f"Claude {session_id[:8]}",
                    project_path=cwd,
                    created_at=created_at,
                    updated_at=updated_at,
                    source="claude-local",
                    metadata={
                        "history_file": str(path),
                        "project_match": matches,
                        "scan_scope": "all" if include_all else "project",
                        "title_source": title_source,
                        # From the sidebar index when there is one. `archived`
                        # is recorded but not filtered on: hiding a session the
                        # user archived may well be right, but that is a
                        # product decision, not a scanning one.
                        "index_title_source": (indexed or {}).get("title_source", ""),
                        "model": (indexed or {}).get("model", ""),
                        "archived": bool((indexed or {}).get("archived", False)),
                    },
                )
            )

        sessions.extend(
            self._scan_claude_session_files(
                project,
                include_all=include_all,
                task_subjects=task_subjects,
                title_index=title_index,
            )
        )

        return self._deduplicate_sessions(sessions)

    def scan_codex(
        self,
        project_path: Path,
        include_all: bool = False,
    ) -> list[SessionInfo]:
        project = project_path.expanduser().resolve()
        thread_index = self._codex_thread_index()

        try:
            threads = CodexAppServerClient(project).list_threads(
                None if include_all else project
            )
            sessions: list[SessionInfo] = []
            for thread in threads:
                if not isinstance(thread, dict):
                    continue
                session_id = str(thread.get("id", "")).strip()
                if not session_id:
                    continue

                title = self._find_title(thread)
                title_source = "official" if title else ""
                preview = str(thread.get("preview", "")).strip()
                if not title and preview:
                    title = preview
                    title_source = "message_preview"

                indexed = thread_index.get(session_id)
                if indexed:
                    title = indexed["title"]
                    title_source = "session_index"

                sessions.append(
                    SessionInfo(
                        provider="codex",
                        session_id=session_id,
                        title=title or preview or f"Codex {session_id[:8]}",
                        project_path=self._extract_thread_cwd(thread),
                        created_at=self._unix_to_iso(thread.get("createdAt")),
                        updated_at=self._unix_to_iso(thread.get("updatedAt")),
                        source="codex-app-server",
                        metadata={
                            "preview": preview,
                            "source_kind": str(thread.get("sourceKind", "")),
                            "is_pinned": bool(thread.get("isPinned", False)),
                            "scan_scope": "all" if include_all else "project",
                            "title_source": title_source or "fallback",
                            "archived": bool((indexed or {}).get("archived", False)),
                        },
                    )
                )
            return self._deduplicate_sessions(sessions)
        except (OSError, RuntimeError, TimeoutError, ValueError):
            return self._scan_codex_jsonl(
                project,
                include_all=include_all,
                thread_index=thread_index,
            )

    def load_claude_messages(
        self,
        session_id: str,
        project_path: Path,
    ) -> list[SessionMessage]:
        session_id = session_id.strip()
        if not session_id:
            return []

        files = self._newest_jsonl_files(self._claude_roots())
        matching = [
            path
            for path in files
            if path.stem == session_id or session_id in path.name
        ]

        for path in matching:
            messages = self._parse_claude_messages(path, session_id)
            if not messages:
                continue

            cwd = self._first_nonempty(
                [str(item.metadata.get("cwd", "")) for item in messages]
            )
            return messages[-self.MAX_MESSAGES :]

        return self._load_claude_task_messages(session_id)[
            -self.MAX_MESSAGES :
        ]

    def load_codex_messages(
        self,
        session_id: str,
        project_path: Path,
    ) -> list[SessionMessage]:
        session_id = session_id.strip()
        if not session_id:
            return []

        project = project_path.expanduser().resolve()
        try:
            thread = CodexAppServerClient(project).read_thread(session_id)
            return self._parse_codex_thread(thread, session_id)[
                -self.MAX_MESSAGES :
            ]
        except (OSError, RuntimeError, TimeoutError, ValueError):
            return self._load_codex_jsonl_messages(session_id)[
                -self.MAX_MESSAGES :
            ]

    def _read_claude_summary(
        self,
        path: Path,
    ) -> tuple[str, str, str, str, str, str] | None:
        entries = self._read_jsonl(path, max_lines=160)
        if not entries:
            return None

        session_id = ""
        title = ""
        title_source = ""
        cwd = ""
        created_at = ""
        updated_at = self._mtime_iso(path)

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            session_id = session_id or self._find_string(
                entry,
                {"sessionId", "session_id"},
            )
            cwd = cwd or self._find_string(
                entry,
                {"cwd", "projectPath", "project_path"},
            )
            timestamp = self._find_string(
                entry,
                {"timestamp", "created_at", "createdAt"},
            )
            created_at = created_at or self._normalize_timestamp(timestamp)

            official_title = self._find_title(entry)
            if official_title and not title:
                title = official_title
                title_source = "official"

            if not title:
                role, content = self._extract_message(entry)
                if role == "user" and content:
                    title = self._preview(content)
                    title_source = "message_preview"
                elif isinstance(entry.get("summary"), str):
                    title = self._preview(str(entry["summary"]))
                    title_source = "summary"

        if not session_id:
            match = self.UUID_PATTERN.search(path.stem)
            if match:
                session_id = match.group(0)
            elif path.stem:
                session_id = path.stem

        if not title_source:
            title_source = "fallback"

        return session_id, title, cwd, created_at, updated_at, title_source

    def _claude_title_index(self) -> dict[str, dict[str, Any]]:
        """The desktop app's own sidebar index, keyed by transcript uuid.

        Every entry in the Claude Code sidebar has one small JSON here:

            %APPDATA%\\Claude\\claude-code-sessions\\<org>\\<workspace>\\local_<uuid>.json

        and its `cliSessionId` is exactly the `<uuid>.jsonl` filename under
        ~/.claude/projects/. That makes this the authoritative answer to "what
        is this conversation called" - it is the string the user sees and the
        one they typed - for a few hundred bytes per session.

        Reading it replaces guessing the title out of the transcript, which was
        wrong for every real session on this machine (measured 2026-08-07, see
        ARCHITECTURE.md). Two of the three causes were unfixable by adding more
        keys to TITLE_KEYS: titles are re-emitted throughout the file with the
        *last* one authoritative, and the scanner reads only the first 160
        lines - so a 9,000-line transcript carrying 655 title records would
        still have handed back a stale early one.

        Reverse-engineered from app version 2.1.221, not a documented API. It
        is treated as preferred, never as required: a missing or unreadable
        index leaves the transcript parsing below exactly as it was.
        """
        index: dict[str, dict[str, Any]] = {}
        for root in self._claude_index_roots():
            if not root.is_dir():
                continue
            try:
                paths = sorted(root.rglob("local_*.json"))[: self.MAX_FILES]
            except OSError:
                continue
            for path in paths:
                try:
                    if path.stat().st_size > self.MAX_BYTES_PER_FILE:
                        continue
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                cli_id = str(data.get("cliSessionId", "")).strip()
                title = " ".join(str(data.get("title") or "").split()).strip()
                if not cli_id or not title:
                    continue
                index[cli_id] = {
                    "title": title,
                    # "user" = the user typed it, "auto" = the app generated
                    # it. Both are real titles shown in the sidebar; the
                    # distinction is passed through rather than acted on here.
                    "title_source": str(data.get("titleSource") or "").strip(),
                    "cwd": str(data.get("cwd") or data.get("originCwd") or "").strip(),
                    "created_at": self._unix_to_iso(data.get("createdAt")),
                    "updated_at": self._unix_to_iso(data.get("lastActivityAt")),
                    "model": str(data.get("model") or "").strip(),
                    "archived": bool(data.get("isArchived", False)),
                }
        return index

    # Columns the thread index wants, if the table happens to have them. Named
    # rather than `SELECT *` so a schema that grows a column does not change
    # what is read, and a schema that drops one degrades instead of raising.
    _CODEX_THREAD_COLUMNS = (
        "id",
        "title",
        "cwd",
        "rollout_path",
        "created_at_ms",
        "updated_at_ms",
        "archived",
        "is_pinned",
    )

    @staticmethod
    def _codex_state_dbs() -> list[Path]:
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        try:
            return sorted(codex_home.glob("state_*.sqlite"))
        except OSError:
            return []

    def _codex_thread_index(self) -> dict[str, dict[str, Any]]:
        """Codex Desktop's own thread list, keyed by thread id.

        `~/.codex/state_<n>.sqlite` has a `threads` table holding the name each
        conversation shows under in the app, alongside `rollout_path` - the
        exact `.jsonl` under ~/.codex/sessions that the scanner reads.

        This is the Codex half of the same problem the Claude session index
        solved: the transcript carries no title at all (checked - the only
        record types are `session_meta`, `event_msg` and `response_item`, and
        none of them has a title field), so deriving one from the first user
        message was not a shortcut, it was the only thing available. Which is
        why 「幫我看看 roadmap…」 and a bare Windows path were showing up as
        conversation names.

        The database belongs to a running application, so it is copied before
        being read - along with its -wal and -shm, or the copy would be missing
        whatever has not been checkpointed yet. Nothing here writes to Codex's
        own files, and a failure at any step leaves the caller with an empty
        index and the previous behaviour.

        Reverse-engineered from Codex 0.147.0-alpha.6.5; not a documented API.
        """
        index: dict[str, dict[str, Any]] = {}
        for db in self._codex_state_dbs():
            for row in self._read_codex_threads(db):
                thread_id = str(row.get("id") or "").strip()
                title = " ".join(str(row.get("title") or "").split()).strip()
                if not thread_id or not title:
                    continue
                entry = {
                    "title": title,
                    "cwd": self._strip_extended_path(str(row.get("cwd") or "")),
                    "created_at": self._unix_to_iso(row.get("created_at_ms")),
                    "updated_at": self._unix_to_iso(row.get("updated_at_ms")),
                    "archived": bool(row.get("archived") or 0),
                    "is_pinned": bool(row.get("is_pinned") or 0),
                }
                index[thread_id] = entry
                # Also reachable by transcript filename: the scanner falls back
                # to the file stem when a jsonl carries no id of its own.
                rollout = str(row.get("rollout_path") or "").strip()
                if rollout:
                    index.setdefault(Path(rollout).stem, entry)
        return index

    def _read_codex_threads(self, db: Path) -> list[dict[str, Any]]:
        temp_dir: str | None = None
        try:
            temp_dir = tempfile.mkdtemp(prefix="aicc-codex-")
            copy = Path(temp_dir) / db.name
            shutil.copy2(db, copy)
            for suffix in ("-wal", "-shm"):
                side = db.with_name(db.name + suffix)
                if side.is_file():
                    shutil.copy2(side, copy.with_name(copy.name + suffix))

            connection = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            try:
                connection.row_factory = sqlite3.Row
                available = {
                    str(item[1])
                    for item in connection.execute("PRAGMA table_info(threads)")
                }
                if not available:
                    return []
                columns = [c for c in self._CODEX_THREAD_COLUMNS if c in available]
                if "id" not in columns or "title" not in columns:
                    return []
                sql = f"SELECT {', '.join(columns)} FROM threads"
                return [dict(row) for row in connection.execute(sql)]
            finally:
                connection.close()
        except (OSError, sqlite3.Error, ValueError):
            return []
        finally:
            if temp_dir:
                # **走那道門，不直接 rmtree。**這一行本來是
                # `shutil.rmtree(temp_dir, ...)`，而 `temp_dir` 是一個變數
                # ——2026-08-21 那次事故就是一行破壞把這種變數指到了使用者的
                # `%APPDATA%`，然後刪掉了 DPAPI 的主金鑰。
                not_ours.remove_tree(temp_dir)

    @staticmethod
    def _strip_extended_path(value: str) -> str:
        """`\\\\?\\C:\\x` -> `C:\\x`. Codex stores cwd in extended-length form.

        Left in place, every path comparison against a project would fail and
        no Codex conversation would ever be recognised as belonging to one.
        """
        text = value.strip()
        for prefix in ("\\\\?\\UNC\\", "\\\\?\\"):
            if text.startswith(prefix):
                return ("\\\\" if prefix.endswith("UNC\\") else "") + text[len(prefix):]
        return text

    def _apply_session_index(
        self,
        entry: dict[str, Any] | None,
        title: str,
        title_source: str,
        cwd: str,
        created_at: str,
        updated_at: str,
    ) -> tuple[str, str, str, str, str]:
        """Let the sidebar index win on title; fill the rest only if missing."""
        if not entry:
            return title, title_source, cwd, created_at, updated_at
        return (
            entry["title"],
            "session_index",
            cwd or entry["cwd"],
            created_at or entry["created_at"],
            entry["updated_at"] or updated_at,
        )

    def _parse_claude_messages(
        self,
        path: Path,
        session_id: str,
    ) -> list[SessionMessage]:
        result: list[SessionMessage] = []
        for entry in self._read_jsonl(path):
            if not isinstance(entry, dict):
                continue
            role, content = self._extract_message(entry)
            if role not in {"user", "assistant"} or not content:
                continue

            timestamp = self._normalize_timestamp(
                self._find_string(
                    entry,
                    {"timestamp", "created_at", "createdAt"},
                )
            )
            cwd = self._find_string(
                entry,
                {"cwd", "projectPath", "project_path"},
            )
            result.append(
                SessionMessage(
                    provider="claude",
                    session_id=session_id,
                    role=role,
                    content=content,
                    timestamp=timestamp,
                    metadata={"cwd": cwd, "history_file": str(path)},
                )
            )
        return result

    def _scan_claude_session_files(
        self,
        project: Path,
        include_all: bool,
        task_subjects: dict[str, str],
        title_index: dict[str, dict[str, Any]] | None = None,
    ) -> list[SessionInfo]:
        sessions: list[SessionInfo] = []
        title_index = title_index if title_index is not None else {}
        for root in self._claude_session_roots():
            if not root.is_dir():
                continue

            try:
                files = sorted(
                    root.glob("*.json"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )[: self.MAX_FILES]
            except OSError:
                continue

            for path in files:
                data = self._read_json_file(path)
                if not isinstance(data, dict):
                    continue

                session_id = self._find_string(
                    data,
                    {"sessionId", "session_id", "id"},
                )
                if not session_id:
                    continue

                cwd = self._find_string(
                    data,
                    {"cwd", "projectPath", "project_path"},
                )
                matches = self._paths_match(cwd, project)
                if cwd and not matches and not include_all:
                    continue

                raw_title = self._find_string(data, {"name", "title"})
                name_source = self._find_string(
                    data,
                    {"nameSource", "name_source"},
                )
                task_subject = task_subjects.get(session_id, "")
                title = ""
                title_source = ""

                indexed = title_index.get(session_id)
                if indexed:
                    # The sidebar knows this session's name; a process handle
                    # from the live-process registry must not override it.
                    title = indexed["title"]
                    title_source = "session_index"
                    cwd = cwd or indexed["cwd"]
                elif self._is_user_named_claude_title(raw_title, name_source):
                    title = raw_title
                    title_source = "official"
                elif task_subject:
                    title = task_subject
                    title_source = "task_subject"
                elif cwd:
                    title = Path(cwd).name
                    title_source = "project_folder"

                sessions.append(
                    SessionInfo(
                        provider="claude",
                        session_id=session_id,
                        title=title or raw_title or f"Claude {session_id[:8]}",
                        project_path=cwd,
                        created_at=self._normalize_timestamp(
                            self._find_string(
                                data,
                                {"startedAt", "created_at", "createdAt"},
                            )
                        ),
                        updated_at=self._mtime_iso(path),
                        source="claude-session",
                        metadata={
                            "session_file": str(path),
                            "entrypoint": self._find_string(data, {"entrypoint"}),
                            "kind": self._find_string(data, {"kind"}),
                            "name_source": name_source,
                            "project_match": matches,
                            "scan_scope": "all" if include_all else "project",
                            "task_subject": task_subject,
                            "title_source": title_source or "fallback",
                        },
                    )
                )

        return sessions

    def _claude_task_subjects(self) -> dict[str, str]:
        subjects: dict[str, str] = {}
        for root in self._claude_task_roots():
            if not root.is_dir():
                continue

            try:
                task_dirs = [item for item in root.iterdir() if item.is_dir()]
            except OSError:
                continue

            for task_dir in task_dirs:
                try:
                    files = sorted(
                        task_dir.glob("*.json"),
                        key=lambda item: item.stat().st_mtime,
                        reverse=True,
                    )
                except OSError:
                    continue

                for path in files:
                    data = self._read_json_file(path)
                    if not isinstance(data, dict):
                        continue
                    subject = self._find_string(data, {"subject", "title", "name"})
                    if subject:
                        subjects[task_dir.name] = self._preview(subject, 88)
                        break

        return subjects

    def _load_claude_task_messages(self, session_id: str) -> list[SessionMessage]:
        messages: list[SessionMessage] = []
        for root in self._claude_task_roots():
            task_dir = root / session_id
            if not task_dir.is_dir():
                continue

            try:
                files = sorted(task_dir.glob("*.json"), key=self._path_mtime)
            except OSError:
                continue

            for path in files:
                data = self._read_json_file(path)
                if not isinstance(data, dict):
                    continue
                subject = self._find_string(data, {"subject", "title", "name"})
                description = self._find_string(data, {"description"})
                status = self._find_string(data, {"status"})
                if not subject and not description:
                    continue

                content_parts = []
                if subject:
                    content_parts.append(f"任務：{subject}")
                if status:
                    content_parts.append(f"狀態：{status}")
                if description:
                    content_parts.append(description)

                messages.append(
                    SessionMessage(
                        provider="claude",
                        session_id=session_id,
                        role="user",
                        content="\n\n".join(content_parts),
                        timestamp=self._mtime_iso(path),
                        metadata={"task_file": str(path)},
                    )
                )

        return messages

    def _scan_codex_jsonl(
        self,
        project: Path,
        include_all: bool = False,
        thread_index: dict[str, dict[str, Any]] | None = None,
    ) -> list[SessionInfo]:
        sessions: list[SessionInfo] = []
        thread_index = (
            thread_index if thread_index is not None else self._codex_thread_index()
        )
        for path in self._newest_jsonl_files(self._codex_roots()):
            entries = self._read_jsonl(path, max_lines=120)
            if not entries:
                continue

            session_id = ""
            cwd = ""
            title = ""
            title_source = ""
            created_at = ""

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                session_id = session_id or self._find_string(
                    entry,
                    {"thread_id", "threadId", "session_id", "sessionId", "id"},
                )
                cwd = cwd or self._find_string(
                    entry,
                    {"cwd", "project_path", "projectPath"},
                )
                timestamp = self._find_string(
                    entry,
                    {"timestamp", "created_at", "createdAt"},
                )
                created_at = created_at or self._normalize_timestamp(timestamp)

                official_title = self._find_title(entry)
                if official_title and not title:
                    title = official_title
                    title_source = "official"

                role, content = self._extract_message(entry)
                if role == "user" and content and not title:
                    title = self._preview(content)
                    title_source = "message_preview"

            if not session_id:
                match = self.UUID_PATTERN.search(path.stem)
                session_id = match.group(0) if match else path.stem
            if not session_id:
                continue

            # Before the project filter, not after: the index also carries the
            # cwd, and a transcript that never recorded one would otherwise be
            # dropped from a project it does belong to.
            indexed = thread_index.get(session_id) or thread_index.get(path.stem)
            if indexed:
                title = indexed["title"]
                title_source = "session_index"
                cwd = cwd or indexed["cwd"]
                created_at = created_at or indexed["created_at"]

            if cwd and not self._paths_match(cwd, project) and not include_all:
                continue

            sessions.append(
                SessionInfo(
                    provider="codex",
                    session_id=session_id,
                    title=title or f"Codex {session_id[:8]}",
                    project_path=cwd,
                    created_at=created_at,
                    updated_at=(indexed or {}).get("updated_at") or self._mtime_iso(path),
                    source="codex-local-fallback",
                    metadata={
                        "history_file": str(path),
                        "scan_scope": "all" if include_all else "project",
                        "title_source": title_source or "fallback",
                        "archived": bool((indexed or {}).get("archived", False)),
                    },
                )
            )

        return self._deduplicate_sessions(sessions)

    def _load_codex_jsonl_messages(
        self,
        session_id: str,
    ) -> list[SessionMessage]:
        for path in self._newest_jsonl_files(self._codex_roots()):
            if session_id not in path.name and path.stem != session_id:
                continue
            result: list[SessionMessage] = []
            for entry in self._read_jsonl(path):
                if not isinstance(entry, dict):
                    continue
                role, content = self._extract_message(entry)
                if role not in {"user", "assistant"} or not content:
                    continue
                result.append(
                    SessionMessage(
                        provider="codex",
                        session_id=session_id,
                        role=role,
                        content=content,
                        timestamp=self._normalize_timestamp(
                            self._find_string(
                                entry,
                                {"timestamp", "created_at", "createdAt"},
                            )
                        ),
                        metadata={"history_file": str(path)},
                    )
                )
            if result:
                return result
        return []

    def _parse_codex_thread(
        self,
        thread: dict[str, Any],
        session_id: str,
    ) -> list[SessionMessage]:
        result: list[SessionMessage] = []
        turns = thread.get("turns", []) if isinstance(thread, dict) else []
        if not isinstance(turns, list):
            return result

        for turn in turns:
            if not isinstance(turn, dict):
                continue
            turn_timestamp = self._normalize_timestamp(
                self._find_string(
                    turn,
                    {"timestamp", "created_at", "createdAt"},
                )
            )
            items = turn.get("items", [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                role, content = self._extract_message(item)
                item_type = str(item.get("type", "")).lower()
                if not role:
                    if "agent" in item_type or "assistant" in item_type:
                        role = "assistant"
                    elif "user" in item_type:
                        role = "user"
                if role not in {"user", "assistant"} or not content:
                    continue
                timestamp = self._normalize_timestamp(
                    self._find_string(
                        item,
                        {"timestamp", "created_at", "createdAt"},
                    )
                ) or turn_timestamp
                result.append(
                    SessionMessage(
                        provider="codex",
                        session_id=session_id,
                        role=role,
                        content=content,
                        timestamp=timestamp,
                        metadata={"item_type": item_type},
                    )
                )
        return result

    def _extract_message(self, value: Any) -> tuple[str, str]:
        if not isinstance(value, dict):
            return "", ""

        role = str(value.get("role", "")).lower()
        message = value.get("message")
        if isinstance(message, dict):
            role = str(message.get("role", role)).lower()
            content = self._content_to_text(message.get("content"))
            if content:
                return self._normalize_role(role), content

        item_type = str(value.get("type", "")).lower()
        if item_type in {"user", "assistant"}:
            role = item_type
        elif "agent_message" in item_type:
            role = "assistant"
        elif "user_message" in item_type:
            role = "user"

        for key in ("content", "text", "prompt", "input"):
            content = self._content_to_text(value.get(key))
            if content:
                return self._normalize_role(role), content

        payload = value.get("payload")
        if isinstance(payload, dict):
            nested_role, nested_content = self._extract_message(payload)
            if nested_content:
                return nested_role or self._normalize_role(role), nested_content

        return "", ""

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                block_type = str(item.get("type", "")).lower()
                if block_type in {"text", "input_text", "output_text"}:
                    text = item.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
            return "\n".join(chunk.strip() for chunk in chunks if chunk.strip())
        if isinstance(content, dict):
            for key in ("text", "content", "value"):
                text = self._content_to_text(content.get(key))
                if text:
                    return text
        return ""

    def _read_jsonl(
        self,
        path: Path,
        max_lines: int | None = None,
    ) -> list[Any]:
        if max_lines is None:
            collected: deque[Any] = deque(maxlen=4000)
        else:
            collected = deque()

        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for index, line in enumerate(stream):
                    if max_lines is not None and index >= max_lines:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        collected.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return list(collected)

    @staticmethod
    def _read_json_file(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def format_health(
        self, sample: int = 12, provider: str = "claude"
    ) -> "FormatHealth":
        """Can AICC still read what the providers are writing?

        ROADMAP P0「Provider 本機格式改版時要能優雅降級」. The降級 half was
        already true - every malformed shape tested (a `content` that became an
        object, an extra envelope layer, a top-level array instead of one
        object per line) returns zero messages rather than raising.

        **Zero messages is the problem.** It is exactly what an empty
        conversation looks like, so a format change reads as 「你沒有對話」
        rather than 「我看不懂這些檔案了」 - and the user's whole library
        quietly empties out the day Claude Code changes its transcript shape.

        So this asks a different question from the parser: of the newest files
        on disk, how many contain readable JSON but no message this build
        recognises? One such file is nothing (a tool-only session has no
        user turn). All of them is a format change.

        A sample, not a sweep: this runs at startup and the answer does not
        get better by reading three thousand files.

        **兩家都要看。** 這裡本來只抽查 Claude，而 Codex 的紀錄是另一個資料夾、
        另一種形狀——所以 Codex 改版的那天，會發生的正是這個檢查存在要防止的
        那件事，只是發生在另一半上：清單空掉，而畫面說不出為什麼。
        """
        roots = (
            self._claude_roots() if provider == "claude" else self._codex_roots()
        )
        checked = 0
        understood = 0
        unrecognised: list[str] = []
        for path in self._newest_jsonl_files(roots)[:sample]:
            rows = self._read_jsonl(path, max_lines=200)
            if not rows:
                # No readable JSON at all - that is a different problem
                # (an empty or non-JSONL file) and not evidence either way.
                continue
            checked += 1
            if self._understands(path, provider):
                understood += 1
            else:
                unrecognised.append(str(path))
        return FormatHealth(
            checked=checked,
            understood=understood,
            unrecognised=tuple(unrecognised),
            provider=provider,
        )

    def _understands(self, path: Path, provider: str) -> bool:
        """能不能從這個檔案讀出至少一則對話。

        走的是各家**真正在用的**那條解析路徑，不是另寫一份簡化版：一份只給
        健康檢查用的解析器，會在正牌解析器壞掉的那天照樣說「讀得懂」。
        """
        if provider == "claude":
            return bool(self._parse_claude_messages(path, "health"))
        for entry in self._read_jsonl(path, max_lines=200):
            if not isinstance(entry, dict):
                continue
            role, content = self._extract_message(entry)
            if role in {"user", "assistant"} and content:
                return True
        return False

    def _newest_jsonl_files(self, roots: list[Path]) -> list[Path]:
        candidates: list[Path] = []
        for root in roots:
            if not root.is_dir():
                continue
            try:
                candidates.extend(root.rglob("*.jsonl"))
            except OSError:
                continue

        def modified(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        return sorted(candidates, key=modified, reverse=True)[: self.MAX_FILES]

    @staticmethod
    def _claude_roots() -> list[Path]:
        # `%APPDATA%\Claude\projects` used to be listed here and has never
        # existed - the desktop app keeps transcripts under ~/.claude/projects
        # and its sidebar index under claude-code-sessions (see
        # _claude_index_roots). A root that is never there costs nothing but
        # says something untrue about where the data lives.
        return [Path.home() / ".claude" / "projects"]

    @staticmethod
    def _claude_index_roots() -> list[Path]:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return []
        return [Path(appdata) / "Claude" / "claude-code-sessions"]

    @staticmethod
    def _claude_session_roots() -> list[Path]:
        home = Path.home()
        return [home / ".claude" / "sessions"]

    @staticmethod
    def _claude_task_roots() -> list[Path]:
        home = Path.home()
        return [home / ".claude" / "tasks"]

    @staticmethod
    def _codex_roots() -> list[Path]:
        home = Path.home()
        codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
        return [
            codex_home / "sessions",
            codex_home / "archived_sessions",
        ]

    @staticmethod
    def _find_string(value: Any, keys: set[str]) -> str:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in keys and isinstance(item, (str, int, float)):
                    return str(item)
            for item in value.values():
                found = SessionScanner._find_string(item, keys)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = SessionScanner._find_string(item, keys)
                if found:
                    return found
        return ""

    @classmethod
    def _find_title(cls, value: Any) -> str:
        if isinstance(value, dict):
            for key, item in value.items():
                if key not in cls.TITLE_KEYS:
                    continue
                candidate = cls._title_candidate(item)
                if candidate:
                    return candidate
            for item in value.values():
                found = cls._find_title(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = cls._find_title(item)
                if found:
                    return found
        return ""

    @classmethod
    def _title_candidate(cls, value: Any) -> str:
        if not isinstance(value, (str, int, float)):
            return ""

        text = " ".join(str(value).split()).strip()
        lowered = text.lower()
        if (
            not text
            or lowered in {"none", "null", "undefined"}
            or text.startswith(("{", "["))
            or "\\" in text
            or "/" in text
            or any(m in text for m in prompt_markers.PROMPT_ANYWHERE)
        ):
            return ""

        if len(text) > 88:
            return cls._preview(text, 88)
        return text

    @staticmethod
    def _is_user_named_claude_title(title: str, name_source: str) -> bool:
        """Is this `name` a title a person chose, or a generated handle?

        `~/.claude/sessions/<pid>.json` is a live-process registry, not a title
        store. Its `name` is a process handle and it says so in `nameSource`.

        `derived` used to be rejected only when the text also matched
        `claude-<digits>`, which was a guess at what handles look like - and
        the guess was wrong: a real one measured on this machine was `aicc-4a`,
        so it passed as a user title and, being ranked `official`, outranked
        the session's actual name. The file already states the provenance;
        there is no reason to infer it from the shape of the string.
        """
        text = " ".join(str(title or "").split()).strip()
        source = str(name_source or "").strip().lower()
        if not text:
            return False
        if source in {"user", "manual", "custom", "renamed"}:
            return True
        if source == "derived":
            return False
        return not re.fullmatch(r"claude-\d+", text.lower())

    @staticmethod
    def _paths_match(raw_path: str, project: Path) -> bool:
        if not raw_path:
            return False
        try:
            return Path(raw_path).expanduser().resolve() == project
        except (OSError, RuntimeError):
            return os.path.normcase(os.path.abspath(raw_path)) == os.path.normcase(
                str(project)
            )

    @staticmethod
    def _extract_thread_cwd(thread: dict[str, Any]) -> str:
        for key in ("cwd", "workingDirectory", "projectPath"):
            value = thread.get(key)
            if isinstance(value, str):
                return value
        return ""

    @classmethod
    def _deduplicate_sessions(
        cls,
        sessions: list[SessionInfo],
    ) -> list[SessionInfo]:
        deduplicated: dict[str, SessionInfo] = {}
        for item in sessions:
            existing = deduplicated.get(item.session_id)
            if existing is None:
                deduplicated[item.session_id] = item
                continue

            item_rank = cls._title_quality_rank(item)
            existing_rank = cls._title_quality_rank(existing)
            if (
                item_rank > existing_rank
                or (
                    item_rank == existing_rank
                    and item.updated_at > existing.updated_at
                )
            ):
                deduplicated[item.session_id] = item
        return sorted(
            deduplicated.values(),
            key=lambda item: item.updated_at or item.created_at,
            reverse=True,
        )

    @staticmethod
    def _title_quality_rank(item: SessionInfo) -> int:
        source = str(item.metadata.get("title_source", "")).strip()
        ranks = {
            # The desktop app's own sidebar entry - the string the user reads
            # and, when titleSource is "user", the one they typed. Nothing
            # derived from a transcript can beat that.
            "session_index": 60,
            "official": 50,
            "task_subject": 40,
            "summary": 35,
            "project_folder": 30,
            "message_preview": 20,
            "fallback": 10,
        }
        return ranks.get(source, 20)

    @staticmethod
    def _normalize_role(role: str) -> str:
        normalized = role.strip().lower()
        if normalized in {"assistant", "agent", "model"}:
            return "assistant"
        if normalized in {"user", "human"}:
            return "user"
        return normalized

    @staticmethod
    def _preview(text: str, limit: int = 88) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1] + "…"

    @staticmethod
    def _mtime_iso(path: Path) -> str:
        try:
            return datetime.fromtimestamp(
                path.stat().st_mtime,
                tz=timezone.utc,
            ).astimezone().isoformat()
        except OSError:
            return ""

    @staticmethod
    def _path_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _unix_to_iso(value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(
                number,
                tz=timezone.utc,
            ).astimezone().isoformat()
        except (OSError, OverflowError, ValueError):
            return ""

    @staticmethod
    def _normalize_timestamp(value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        try:
            number = float(text)
        except ValueError:
            return text
        return SessionScanner._unix_to_iso(number)

    @staticmethod
    def _first_nonempty(values: list[str]) -> str:
        return next((value for value in values if value), "")


class CodexAppServerClient:
    """Minimal synchronous JSON-RPC client for `codex app-server`."""

    def __init__(self, working_directory: Path, timeout_seconds: float = 10.0) -> None:
        self.working_directory = working_directory.expanduser().resolve()
        self.timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._next_id = 1

    def list_threads(self, cwd: Path | None) -> list[dict[str, Any]]:
        with self:
            params: dict[str, Any] = {
                "cursor": None,
                "limit": 100,
                "sortKey": "updated_at",
                "sortDirection": "desc",
                "sourceKinds": ["cli", "vscode", "exec", "appServer"],
            }
            if cwd is not None:
                params["cwd"] = str(cwd.expanduser().resolve())

            response = self._request(
                "thread/list",
                params,
            )
            result = response.get("result", {})
            data = result.get("data", []) if isinstance(result, dict) else []
            return [item for item in data if isinstance(item, dict)]

    def read_thread(self, thread_id: str) -> dict[str, Any]:
        with self:
            response = self._request(
                "thread/read",
                {"threadId": thread_id, "includeTurns": True},
            )
            result = response.get("result", {})
            thread = result.get("thread", {}) if isinstance(result, dict) else {}
            if not isinstance(thread, dict):
                raise RuntimeError("Codex app-server returned an invalid thread.")
            return thread

    def __enter__(self) -> CodexAppServerClient:
        if self._process is not None:
            return self

        tool = shutil.which("codex")
        if tool is None:
            raise OSError("codex was not found in PATH")

        command = self._build_command(tool)
        self._process = subprocess.Popen(
            command,
            cwd=self.working_directory,
            creationflags=NO_WINDOW,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
        )
        if self._process.stdin is None or self._process.stdout is None:
            raise OSError("Unable to open Codex app-server pipes")

        self._reader = threading.Thread(
            target=self._read_loop,
            name="aicc-codex-app-server-reader",
            daemon=True,
        )
        self._reader.start()

        init_id = self._send(
            "initialize",
            {
                "clientInfo": {
                    "name": "aicc_session_scanner",
                    "title": "AI Control Center",
                    "version": "0.6.0",
                }
            },
        )
        self._wait_for_id(init_id)
        self._notify("initialized", {})
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._send(method, params)
        return self._wait_for_id(request_id)

    def _send(self, method: str, params: dict[str, Any]) -> int:
        request_id = self._next_id
        self._next_id += 1
        self._write({"method": method, "id": request_id, "params": params})
        return request_id

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("Codex app-server is not running")
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                self._queue.put(payload)

    def _wait_for_id(self, request_id: int) -> dict[str, Any]:
        deadline = datetime.now().timestamp() + self.timeout_seconds
        deferred: list[dict[str, Any]] = []
        try:
            while True:
                remaining = deadline - datetime.now().timestamp()
                if remaining <= 0:
                    raise TimeoutError("Codex app-server request timed out")
                try:
                    payload = self._queue.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError("Codex app-server request timed out") from exc
                if payload.get("id") == request_id:
                    if "error" in payload:
                        raise RuntimeError(str(payload["error"]))
                    return payload
                deferred.append(payload)
        finally:
            for payload in deferred:
                self._queue.put(payload)

    @staticmethod
    def _build_command(tool: str) -> list[str]:
        suffix = Path(tool).suffix.lower()
        if os.name == "nt" and suffix in {".cmd", ".bat"}:
            command_text = subprocess.list2cmdline([tool, "app-server"])
            return [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/s",
                "/c",
                command_text,
            ]
        return [tool, "app-server"]
