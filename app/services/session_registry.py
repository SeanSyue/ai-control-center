from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.lineup import Lineup


@dataclass(frozen=True)
class SessionEntry:
    provider: str
    session_id: str
    title: str
    project_path: str
    created_at: str = ""
    last_used_at: str = ""
    source: str = "aicc"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def display_title(self) -> str:
        title = self.title.strip() or self.session_id
        if len(title) > 72:
            title = title[:69] + "…"
        return title


class SessionFileTooNew(RuntimeError):
    """`sessions.json` 是比這個版本新的 AICC 寫的。

    刻意是例外而不是「回預設值繼續」：回預設值的話，使用者排好的陣容會在
    下一次存檔的時候被靜靜換成出廠的兩格，而他不會看到任何一個字。
    """


class SessionRegistry:
    """Persist one project's session bindings and its line-up.

    Only session identifiers and display metadata are stored. CLI login data,
    tokens, and provider configuration are deliberately excluded.

    Two different things live here and only one of them is about vendors:

    * `sessions` is the catalogue of conversations found for each provider.
      Keyed by provider on purpose - that is what a scan produces.
    * `lineup` is who is actually on this project's workflow, in order. It
      replaced `selected: {claude, codex}` (ROADMAP P10), which could hold one
      Claude and one Codex and nothing else. `selected` is still written and
      still read as a fallback, but it is derived from the line-up.
    """

    # 磁碟格式的版本。**寫進檔案，而且讀的時候真的比對。**
    #
    # 2026-08-23 之前這個常數存在、也寫進 `sessions.json`，但**沒有任何一行
    # 讀它**——所以一份由新版 AICC 寫出來的檔案，被舊版打開之後會被
    # `load()` 正規化成它認得的形狀，然後在下一次 `save()` 原地蓋掉。
    # 使用者失去的是他排好的陣容，而畫面上看起來一切正常。
    #
    # 現在的規矩只有一條，而且刻意只有一條：**看不懂的檔案不准蓋。**
    # 不試著轉檔、不猜、不「盡量讀讀看」——那三種都會在使用者不知情的
    # 情況下改動他的東西。
    VERSION = 1
    VALID_PROVIDERS = {"claude", "codex"}

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path.expanduser().resolve()
        self.aicc_path = self.project_path / ".aicc"
        self.path = self.aicc_path / "sessions.json"
        self.aicc_path.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        default = self._default_payload()
        if not self.path.exists():
            return default

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return default

        if not isinstance(raw, dict):
            return default

        selected = raw.get("selected", {})
        sessions = raw.get("sessions", {})

        normalized = self._default_payload()
        lineup = self._lineup_from_raw(raw.get("lineup"), selected)
        self._write_lineup_into(normalized, lineup)

        if isinstance(sessions, dict):
            for provider in self.VALID_PROVIDERS:
                items = sessions.get(provider, [])
                if not isinstance(items, list):
                    continue

                deduplicated: dict[str, dict[str, Any]] = {}
                for item in items:
                    entry = self._normalize_item(provider, item)
                    if entry is None:
                        continue
                    deduplicated[entry["session_id"]] = entry

                normalized["sessions"][provider] = list(
                    deduplicated.values()
                )

        return normalized

    def stored_version(self) -> int:
        """磁碟上那份檔案自稱是第幾版。讀不到就當 0（比第一版還舊）。"""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return 0
        if not isinstance(raw, dict):
            return 0
        try:
            return int(raw.get("version", 0))
        except (TypeError, ValueError):
            return 0

    def written_by_a_newer_build(self) -> bool:
        return self.stored_version() > self.VERSION

    def save(self, payload: dict[str, Any]) -> None:
        if self.written_by_a_newer_build():
            # **看不懂就不要蓋。**這一份是新版 AICC 寫的，裡面可能有這一版
            # 沒有欄位可以裝的東西——照這一版的形狀寫回去，那些東西會消失，
            # 而使用者不會收到任何一個字。
            raise SessionFileTooNew(
                f"{self.path} 是第 {self.stored_version()} 版寫的，"
                f"這個版本只認得到第 {self.VERSION} 版。"
                "沒有動它——請更新 AICC，或者把那個檔案移開再開一次。"
            )
        normalized = self._default_payload()
        selected = payload.get("selected", {})
        sessions = payload.get("sessions", {})

        self._write_lineup_into(
            normalized,
            self._lineup_from_raw(payload.get("lineup"), selected),
        )

        if isinstance(sessions, dict):
            for provider in self.VALID_PROVIDERS:
                items = sessions.get(provider, [])
                if not isinstance(items, list):
                    continue

                deduplicated: dict[str, dict[str, Any]] = {}
                for item in items:
                    entry = self._normalize_item(provider, item)
                    if entry is None:
                        continue
                    deduplicated[entry["session_id"]] = entry

                normalized["sessions"][provider] = sorted(
                    deduplicated.values(),
                    key=lambda item: (
                        str(item.get("last_used_at", "")),
                        str(item.get("created_at", "")),
                    ),
                    reverse=True,
                )

        normalized["updated_at"] = self._now()
        self._atomic_write(normalized)

    def list_sessions(self, provider: str) -> list[SessionEntry]:
        provider = self._validate_provider(provider)
        payload = self.load()
        result: list[SessionEntry] = []

        for item in payload["sessions"][provider]:
            result.append(
                SessionEntry(
                    provider=provider,
                    session_id=str(item["session_id"]),
                    title=str(item.get("title", "")),
                    project_path=str(item.get("project_path", "")),
                    created_at=str(item.get("created_at", "")),
                    last_used_at=str(item.get("last_used_at", "")),
                    source=str(item.get("source", "aicc")),
                    metadata=(
                        dict(item.get("metadata", {}))
                        if isinstance(item.get("metadata", {}), dict)
                        else {}
                    ),
                )
            )

        return result

    # ---- line-up --------------------------------------------------------
    def load_lineup(self) -> Lineup:
        """Who is on this project's workflow, in order."""
        return Lineup.from_list(self.load().get("lineup", []))

    def save_lineup(self, lineup: Lineup) -> None:
        payload = self.load()
        self._write_lineup_into(payload, lineup)
        self.save(payload)

    def set_role_session(self, role: str, session_id: str) -> None:
        """Bind a session to a position by role."""
        self.save_lineup(self.load_lineup().with_session(role, session_id.strip()))

    # ---- provider-shaped compatibility ----------------------------------
    # Everything below still says "the Claude one". It keeps working, and it
    # keeps reaching only the *first* slot that provider holds - so a project
    # whose line-up puts two Claudes on it cannot be driven through here. That
    # is the boundary, not an oversight: callers that compose a line-up have to
    # use the role-shaped API above.
    def selected_session_id(self, provider: str) -> str:
        provider = self._validate_provider(provider)
        return self.load_lineup().session_for_provider(provider)

    def set_selected(self, provider: str, session_id: str) -> None:
        provider = self._validate_provider(provider)
        lineup = self.load_lineup()
        slot = lineup.first_for_provider(provider)
        if slot is None:
            return
        self.save_lineup(lineup.with_session(slot.role, session_id.strip()))

    def clear_selected(self, provider: str) -> None:
        self.set_selected(provider, "")

    def upsert(self, entry: SessionEntry, select: bool = False) -> None:
        provider = self._validate_provider(entry.provider)
        session_id = entry.session_id.strip()
        if not session_id:
            raise ValueError("session_id cannot be empty")

        payload = self.load()
        items = payload["sessions"][provider]
        now = self._now()
        existing = next(
            (
                item
                for item in items
                if str(item.get("session_id", "")) == session_id
            ),
            None,
        )

        normalized = {
            "session_id": session_id,
            "title": entry.title.strip() or session_id,
            "project_path": (
                entry.project_path.strip() or str(self.project_path)
            ),
            "created_at": entry.created_at.strip() or now,
            "last_used_at": entry.last_used_at.strip() or now,
            "source": entry.source.strip() or "aicc",
            "metadata": dict(entry.metadata),
        }

        if existing is None:
            items.append(normalized)
        else:
            created_at = str(existing.get("created_at", "")).strip()
            existing.update(normalized)
            if created_at and not entry.created_at.strip():
                existing["created_at"] = created_at

        if select:
            # Through the line-up, not by writing `selected` directly: that key
            # is derived on save, so setting it here would be dropped without
            # a word. Caught by the binding tests when `selected` stopped being
            # storage and became a view.
            lineup = self._lineup_from_raw(
                payload.get("lineup"), payload.get("selected")
            )
            slot = lineup.first_for_provider(provider)
            if slot is not None:
                self._write_lineup_into(
                    payload, lineup.with_session(slot.role, session_id)
                )

        self.save(payload)

    def touch(
        self,
        provider: str,
        session_id: str,
        title: str = "",
        source: str = "aicc",
        metadata: dict[str, Any] | None = None,
        select: bool = True,
    ) -> None:
        provider = self._validate_provider(provider)
        session_id = session_id.strip()
        if not session_id:
            return

        existing = next(
            (
                item
                for item in self.list_sessions(provider)
                if item.session_id == session_id
            ),
            None,
        )

        self.upsert(
            SessionEntry(
                provider=provider,
                session_id=session_id,
                title=(
                    title.strip()
                    or (existing.title if existing is not None else session_id)
                ),
                project_path=str(self.project_path),
                created_at=(existing.created_at if existing is not None else ""),
                last_used_at=self._now(),
                source=(source or (existing.source if existing is not None else "aicc")),
                metadata=(
                    dict(metadata)
                    if metadata is not None
                    else (
                        dict(existing.metadata)
                        if existing is not None
                        else {}
                    )
                ),
            ),
            select=select,
        )

    def rename(self, provider: str, session_id: str, title: str) -> None:
        provider = self._validate_provider(provider)
        payload = self.load()
        for item in payload["sessions"][provider]:
            if str(item.get("session_id", "")) == session_id:
                item["title"] = title.strip() or session_id
                item["last_used_at"] = self._now()
                metadata = item.get("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata["custom_title"] = True
                item["metadata"] = metadata
                self.save(payload)
                return

    def _default_payload(self) -> dict[str, Any]:
        return {
            "version": self.VERSION,
            "project_path": str(self.project_path),
            "lineup": Lineup.classic().to_list(),
            "selected": {"claude": "", "codex": ""},
            "sessions": {"claude": [], "codex": []},
            "updated_at": "",
        }

    @staticmethod
    def _lineup_from_raw(stored: Any, selected: Any) -> Lineup:
        """The line-up, or the classic pair rebuilt from `selected`.

        Every sessions.json written before P10 has `selected` and no `lineup`,
        and those files hold real bindings a user made. Reading them as "no
        line-up" would silently unbind every existing project.
        """
        lineup = Lineup.from_list(stored)
        if lineup:
            return lineup
        if isinstance(selected, dict):
            return Lineup.classic(
                str(selected.get("claude", "")).strip(),
                str(selected.get("codex", "")).strip(),
            )
        return Lineup.classic()

    @staticmethod
    def _write_lineup_into(payload: dict[str, Any], lineup: Lineup) -> None:
        """Store the line-up, and re-derive `selected` from it.

        `selected` is still written so a build that predates P10 can read the
        file. Derived, never set alongside - two keys that can disagree is how
        a file starts lying about what the user chose.
        """
        payload["lineup"] = lineup.to_list()
        payload["selected"] = {
            "claude": lineup.session_for_provider("claude"),
            "codex": lineup.session_for_provider("codex"),
        }

    def _normalize_item(
        self,
        provider: str,
        item: Any,
    ) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None

        session_id = str(item.get("session_id", "")).strip()
        if not session_id:
            return None

        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        return {
            "session_id": session_id,
            "title": str(item.get("title", "")).strip() or session_id,
            "project_path": str(item.get("project_path", "")).strip()
            or str(self.project_path),
            "created_at": str(item.get("created_at", "")).strip(),
            "last_used_at": str(item.get("last_used_at", "")).strip(),
            "source": str(item.get("source", "aicc")).strip() or "aicc",
            "metadata": metadata,
        }

    def _validate_provider(self, provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized not in self.VALID_PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}")
        return normalized

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).astimezone().isoformat()
