# Changelog

## 2026-09-25 — Fixes after the external review

Four defects from the external review were fixed. Nothing else in the product changed.

- **Workspace import could write outside the chosen folder** (review A10). A manifest `project_name` of `..` passed the name filter, so the project root became the folder above the destination and archive entries overwrote files there. The name now drops trailing dots and Windows device names, and the project folder must resolve to a direct child of the destination — which also stops a same-named junction. [`workspace_portability.py`](app/services/workspace_portability.py)
- **Drive sync could write outside the sync folder** (review A11). `project_id` came from project metadata and was joined onto the path unchecked; an absolute value made Windows discard the sync root. It must now be a canonical UUID and must resolve directly under `AIControlCenter/projects/`, for both sync and link. [`cloud_sync.py`](app/services/cloud_sync.py)
- **The installer could rename and delete a folder that was not its own** (review A12). It now moves or deletes only a directory that contains the product executable, refuses a non-empty foreign folder on the folder page and again at install time, and leaves a foreign `.previous` folder alone. [`AI Control Center.iss`](packaging/AI%20Control%20Center.iss)
- **The packaged app was missing `shell.js`** (review A13). The spec now packages `*.js`, and a test requires every file type in `app/ui/web/` to be packaged. [`AI Control Center.spec`](packaging/AI%20Control%20Center.spec)

How it was checked, in the maintainer's environment:

- New tests: `tests/test_an_import_cannot_climb_out_of_its_folder.py`, `tests/test_sync_cannot_write_outside_the_drive_folder.py`, `tests/test_the_installer_only_moves_its_own_folder.py`, and one more in `tests/test_packaging.py`. The import and installer tests failed against the old code before the fixes.
- Twelve new negative-control recipes in four files under `tools/mutations/` remove each new check one at a time; each turned its test red, and the file was restored.
- Two existing sync tests used made-up ids such as `cloud-proj-normal`; they now use fixed UUIDs, because real ids always are.
- Full suite in this repository, with its own virtual environment: 2,254 passed, 0 skipped, exit code 0 (2026-09-25). `negative_control.py --check`: all 115 recipe files still find their targets.
- The installer script compiles with ISCC 6. **It was not run**: an actual install, upgrade or cancel would write to the user's account, so the installer checks are static.

Not changed: deletion ownership, sensitive files reaching review prompts, archive resource limits, and the other open items in [SECURITY.md](SECURITY.md).

## 2026-09-25 — Portfolio documentation

Clarified solo design ownership and AI-assisted development, source setup, known defects and verification limits. Added an architecture guide and a reading map; historical notes remain at their original paths. The author reports successful configured end-to-end use. No scheduled maintenance or release is promised. This documentation pass did not change product code or dependencies.

The original snapshot record below describes the earlier export; its dates and test counts are historical.

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
