from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.project_knowledge import ProjectKnowledgeStore
from app.i18n import t


@dataclass(frozen=True)
class TranscriptImportResult:
    success: bool
    imported_files: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    # Conversations that were already here, by content. Reported rather than
    # counted as imports: 「匯入了 3 個」 when nothing changed is the kind of
    # confirmation that trains people to stop reading confirmations.
    already_here: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        if not self.success:
            detail = self.errors[0] if self.errors else t("error.unknown")
            return f"舊對話匯入失敗：{detail}"
        parts = [f"舊對話匯入完成：{len(self.imported_files)} 個 context-only 檔案"]
        if self.already_here:
            parts.append(f"其中 {len(self.already_here)} 個之前就匯入過，沒有重複建立")
        return "；".join(parts)


class CloudTranscriptImporter:
    """Import exported web conversations as context-only project documents."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path.expanduser().resolve()
        self.store = ProjectKnowledgeStore(self.project_path)
        self.transcript_dir = (
            self.project_path / ".aicc" / "imported-context" / "transcripts"
        )
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self._already_here: list[str] = []

    def import_paths(self, paths: list[Path]) -> TranscriptImportResult:
        imported: list[str] = []
        errors: list[str] = []
        self._already_here: list[str] = []
        for path in paths:
            try:
                markdown_files = self._convert_path(path.expanduser().resolve())
                for markdown in markdown_files:
                    self.store.index_file(
                        markdown,
                        source_type="imported",
                        original_source_path=path,
                    )
                    imported.append(markdown.name)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{path.name}: {exc}")
        self.store.export_manifest()
        return TranscriptImportResult(
            success=not errors,
            imported_files=tuple(imported),
            errors=tuple(errors),
            already_here=tuple(self._already_here),
        )

    def _convert_path(self, path: Path) -> list[Path]:
        if not path.is_file():
            raise ValueError("File does not exist.")
        suffix = path.suffix.lower()
        if suffix == ".json":
            raw = json.loads(path.read_text(encoding="utf-8"))
            return self._convert_json(raw, path)
        if suffix in {".txt", ".md", ".markdown"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            title = path.stem
            return [
                self._write_markdown(
                    provider="manual",
                    title=title,
                    body=text,
                    source=path,
                )
            ]
        raise ValueError("Only JSON, Markdown, and text transcripts are supported.")

    def _convert_json(self, raw: Any, source: Path) -> list[Path]:
        if isinstance(raw, list):
            return [
                self._write_markdown(
                    provider=self._provider_from_dict(item),
                    title=self._safe_title(self._title_of(item, source)),
                    body=self._conversation_from_mapping(item),
                    source=source,
                    index=index,
                )
                for index, item in enumerate(raw)
                if isinstance(item, dict)
            ]
        if isinstance(raw, dict):
            if "mapping" in raw or "messages" in raw or "chat_messages" in raw:
                return [
                    self._write_markdown(
                        provider=self._provider_from_dict(raw),
                        title=self._safe_title(self._title_of(raw, source)),
                        body=self._conversation_from_mapping(raw),
                        source=source,
                    )
                ]
            if "conversations" in raw and isinstance(raw["conversations"], list):
                return self._convert_json(raw["conversations"], source)
        raise ValueError("Unsupported transcript JSON shape.")

    def _conversation_from_mapping(self, item: dict[str, Any]) -> str:
        """Turn one exported conversation into readable markdown.

        Three shapes, because the two vendors do not agree and never will:

        * ChatGPT `conversations.json` - a `mapping` of nodes keyed by id
        * a plain `messages` list
        * Claude.ai's export - `chat_messages`, with `sender` instead of
          `role` and the text under either `text` or a `content` list

        The Claude shape used to fall through to a raw `json.dumps` of the
        whole object. That is the worst available outcome: the import reports
        success, the blob goes into the search index, and it gets selected
        into prompts as unreadable JSON that costs tokens and teaches the
        model nothing. Measured before the fix - a real-shaped Claude export
        came out as `{ "uuid": "abc", "name": …`.
        """
        lines: list[str] = []
        mapping = item.get("mapping")
        if isinstance(mapping, dict):
            ordered = sorted(
                mapping.values(),
                key=lambda node: str(
                    (node or {}).get("message", {}).get("create_time", "")
                ),
            )
            for node in ordered:
                if not isinstance(node, dict):
                    continue
                message = node.get("message")
                if not isinstance(message, dict):
                    continue
                role = str(message.get("author", {}).get("role", "unknown"))
                text = self._content_text(message.get("content"))
                if text:
                    lines.extend([f"## {role}", "", text, ""])
        for key in ("messages", "chat_messages"):
            messages = item.get(key)
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                role = str(
                    message.get("role")
                    or message.get("sender")
                    or message.get("author")
                    or "unknown"
                )
                # `text` first: Claude's export carries the plain string there
                # and repeats it inside `content`, so reading `content` first
                # would work but go the long way round for the common case.
                text = self._content_text(
                    message.get("text") or message.get("content", message)
                )
                if text:
                    lines.extend([f"## {role}", "", text, ""])
        body = "\n".join(lines).strip()
        if not body:
            # Refused rather than dumped as JSON. A blob that imports
            # "successfully" is worse than a rejection: it reaches the index,
            # gets selected into prompts, and the user never looks again.
            raise ValueError(
                t("import.unrecognised_format")
            )
        return body

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, dict):
            parts = content.get("parts")
            if isinstance(parts, list):
                return "\n\n".join(str(part) for part in parts if str(part).strip())
            text = content.get("text")
            if isinstance(text, str):
                return text.strip()
        if isinstance(content, list):
            return "\n\n".join(str(part) for part in content if str(part).strip())
        return ""

    @staticmethod
    def _title_of(raw: dict[str, Any], source: Path) -> str:
        """The conversation's own name. Claude calls it `name`, ChatGPT
        `title` - reading only one of them silently titled every Claude
        conversation after the export file it came out of, so a whole export
        produced files all called the same thing."""
        for key in ("title", "name"):
            value = str(raw.get(key, "")).strip()
            if value:
                return value
        return source.stem

    @staticmethod
    def _provider_from_dict(raw: dict[str, Any]) -> str:
        provider = str(raw.get("provider", "")).casefold()
        if "claude" in provider:
            return "claude"
        # Claude's export has no provider field, so it is recognised by its
        # own shape. Guessing "chatgpt" for everything is only invisible until
        # somebody sorts their transcripts by where they came from.
        if "chat_messages" in raw:
            return "claude"
        return "chatgpt"

    def _write_markdown(
        self,
        provider: str,
        title: str,
        body: str,
        source: Path,
        index: int = 0,
    ) -> Path:
        """Write one transcript, named by its content rather than by the clock.

        The name used to start with a UTC timestamp, which meant **the same
        export imported twice produced two files**. Measured: importing one
        `conversations.json` twice left two markdown files on disk while the
        index held one, so what was on disk and what AICC believed had already
        drifted apart on the second click.

        A content hash makes re-importing a no-op. It also makes it a *cheap*
        no-op, which is the ROADMAP's 「避免每次都送完整舊對話」: nothing new is
        indexed, so nothing new can be selected into a prompt.

        The imported-at line is deliberately not in the hashed content - it is
        written only when the file is first created, because putting a
        timestamp inside the body would make every import a different file
        again, one layer down.
        """
        safe_title = self._safe_title(title or source.stem)
        content = (
            f"# {safe_title}\n\n"
            "Imported cloud transcript. This file is context-only; it is not a resumable local session.\n\n"
            f"- Provider: {provider}\n"
            f"- Source: {source}\n"
            "## Transcript\n\n"
            f"{body.strip()}\n"
        )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:10]
        suffix = f"_{index}" if index else ""
        destination = (
            self.transcript_dir / f"{provider}_{digest}{suffix}_{safe_title}.md"
        )
        if destination.is_file():
            # Same conversation, already here. Left exactly as it was: the
            # existing file is what the index points at, and rewriting it to
            # refresh a timestamp would be a change with no information in it.
            self._already_here.append(destination.name)
            return destination
        destination.write_text(
            content.replace(
                "## Transcript",
                f"- Imported at: {datetime.now(timezone.utc).isoformat()}\n\n"
                "## Transcript",
            ),
            encoding="utf-8",
        )
        return destination

    @staticmethod
    def _safe_title(raw: str) -> str:
        cleaned = re.sub(r"[^\w.()\- \u4e00-\u9fff]+", "_", raw, flags=re.UNICODE)
        cleaned = "_".join(cleaned.strip().split())
        return cleaned[:80] or "imported_transcript"
