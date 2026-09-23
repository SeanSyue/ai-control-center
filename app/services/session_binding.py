"""Binding a chosen Claude/Codex conversation pair to a project.

This was one method inside ConversationLibraryPanel. It moved here when the
HTML conversation library became the real panel rather than a preview: two
panels that both bind sessions must bind them the same way, and the version
that lives in a widget is the one that quietly grows a difference.

The functions take a registry rather than building one so a caller that has
already switched projects cannot bind into the previous project's registry.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from app.services.conversation_search import ConversationHit
from app.services.session_registry import SessionEntry, SessionRegistry

DEFAULT_PROJECT_NAME = "AICC Paired Project"
MAX_NAME_LENGTH = 60


def bind_hits(
    registry: SessionRegistry,
    project_path: Path,
    hits: Iterable[ConversationHit | None],
) -> int:
    """Record each hit as a bound session. Returns how many were written.

    `None` entries are skipped rather than rejected: binding only a Claude
    conversation, with no Codex counterpart chosen, is a normal thing to do.

    The whole hit is kept under metadata["raw"]. The session list needs more of
    it than SessionEntry has fields for - resume status, where the title came
    from - and re-deriving those later would mean re-scanning the disk.
    """
    written = 0
    for hit in hits:
        if hit is None:
            continue
        registry.upsert(
            SessionEntry(
                provider=hit.provider,
                session_id=hit.session_id,
                title=hit.display_title,
                project_path=hit.project_path or str(project_path),
                last_used_at=hit.updated_at,
                source=hit.source,
                metadata={
                    "title_source": hit.title_source,
                    "resume_status": hit.resume_status,
                    "source_label": hit.source_label,
                    "conversation_library": True,
                    "raw": asdict(hit),
                },
            ),
            select=True,
        )
        written += 1
    return written


def suggest_project_name(hits: Iterable[ConversationHit | None]) -> str:
    """First real title among the chosen conversations, else a placeholder.

    Only a starting point - the caller shows it in an editable field, so a
    thin guess costs nothing and a wrong one costs a keystroke.
    """
    for hit in hits:
        if hit is not None and hit.display_title:
            return hit.display_title[:MAX_NAME_LENGTH]
    return DEFAULT_PROJECT_NAME
