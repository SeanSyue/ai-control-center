from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.conversation_search import (  # noqa: E402
    ConversationHit,
    best_snippet,
    collect_hits,
    normalize_title,
    redact,
    score_session,
)


def print_table(hits: list[ConversationHit]) -> None:
    if not hits:
        print("No matching local AI conversations found.")
        return

    for index, hit in enumerate(hits, 1):
        updated = hit.updated_at[:19] if hit.updated_at else "-"
        print(f"{index:02d}. [{hit.provider}] {hit.display_title}")
        print(
            "    "
            f"updated: {updated} | score: {hit.score} | "
            f"{hit.source}/{hit.title_source}"
        )
        print(f"    status: {hit.resume_status} | id: {hit.session_id[:12]}")
        if hit.project_path:
            print(f"    project: {hit.project_path}")
        if hit.snippet:
            print(f"    snippet: {hit.snippet}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search local Claude/Codex conversation metadata safely.",
    )
    parser.add_argument("query", nargs="*", help="Keywords to search for.")
    parser.add_argument(
        "--project",
        default=".",
        help="Project path used for project-aware scanning.",
    )
    parser.add_argument(
        "--provider",
        choices=["all", "claude", "codex"],
        default="all",
    )
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--no-snippets",
        action="store_true",
        help="Skip message previews.",
    )
    args = parser.parse_args()

    hits = collect_hits(
        project_path=Path(args.project),
        provider=args.provider,
        query=" ".join(args.query),
        limit=max(1, args.limit),
        include_snippets=not args.no_snippets,
    )

    if args.as_json:
        import json
        from dataclasses import asdict

        print(
            json.dumps(
                [asdict(hit) for hit in hits],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print_table(hits)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
