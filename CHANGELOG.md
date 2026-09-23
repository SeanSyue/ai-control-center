# Changelog

## Unreleased — first public snapshot

This repository starts from a snapshot of the private working repository, not from its history. The earlier history contained client names and local paths from the machine it was built on, so it was not carried over.

What the snapshot contains:

- The engine (`app/`), 2,219 tests, and the tools: negative control with 111 recipes, page verification, the project index, the memory budget check.
- The design pages the app renders (`design-pages/`), and the build step that copies them into `app/ui/web/`.
- The decision history in `docs/WHY.md`: every product rule, the wording that decided it, and what it replaced.

What it does not contain:

- The maintainer's own project memory (`.aicc/knowledge/`) — those files are notes between one person and his AI agents, not product.
- Business material: pricing, pitch, roadmap strategy.
- Internal handover notes and work-in-progress lists.
- Seven test files and six recipe files that guard those private notes rather than the product. The private working copy keeps them.
- Screenshots of the real app; the ones here use demo data.

Not done yet:

- No continuous integration. The suite needs Windows and a Qt web engine; a workflow that fails for the wrong reason is worse than none.
- No release build. The last packaged installer (2026-08-08) is older than this code.
