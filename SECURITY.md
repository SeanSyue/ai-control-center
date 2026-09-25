# Security and known issues

This is a portfolio snapshot with no scheduled maintenance or promised response time. The author may revisit it, but fixes and releases are not committed. This document describes the reviewed code snapshot `15c1324` and the four fixes made after that review on 2026-09-25 (marked **Fixed after review** below; see [CHANGELOG.md](CHANGELOG.md)). The reviewer has not re-examined those fixes. It is not an independent security certification.

## Data and credentials

- Vendor sign-in belongs to the official Claude Code/Codex CLIs. AICC does not provide a vendor API-key entry flow or proxy that login. It can inspect local conversation files to associate sessions with projects.
- Prompts, selected context and code supplied to vendor CLIs may leave the machine through those tools. Do not describe this as “nothing is uploaded.”
- Optional LINE notifications use a user-supplied token stored with Windows user-scoped DPAPI. DPAPI does not protect all project records or isolate malware running as that user. Notification content varies with sensitivity; even a project folder name may identify a customer.
- Optional Drive integration copies files through a configured sync folder; the external Drive client can upload them. Local-first is not a no-cloud guarantee.
- The reviewed release archives and reachable public Git content had no confirmed real credentials. Test fixtures intentionally contain fake keys. This result is not a promise about later commits or runtime files.

## Confirmed issues to address before sensitive use

| Area | Known boundary or defect |
|---|---|
| Workspace import | **Fixed after review:** the imported project folder must resolve to a direct child of the chosen destination, so a manifest name of `..` or a same-named junction can no longer move it outside; `..`, trailing dots and Windows device names fall back to a default name. **Still open:** archive resource limits (total size, file count) and a complete Windows filename policy for archive entries. See [`workspace_portability.py`](app/services/workspace_portability.py). |
| Drive synchronization | **Fixed after review:** `project_id` must be a canonical UUID and must resolve directly under the sync folder's `projects` directory, for both sync and link. Remote metadata in general is still not a trusted local policy. See [`cloud_sync.py`](app/services/cloud_sync.py). |
| Deletion ownership | [`not_ours.py`](app/services/not_ours.py) uses protected-root rules rather than proof of ownership. Unknown paths may be allowed while legitimate LINE credential removal may be blocked. |
| Installer | **Fixed after review:** the [Inno script](packaging/AI%20Control%20Center.iss) only renames or deletes a directory that contains the product's own executable. A non-empty folder that is not an AICC install is refused on the folder page and again at install time (silent installs skip that page), and a foreign `.previous` folder is left alone. Verified by compiling with ISCC and by static tests of the script; an actual install, upgrade or cancel was **not** run. Version comparison still uses string order. |
| Sensitive prompts | Newly created sensitive files such as `.env.staging` or a private-key file can enter review material. Existing scanning is not a complete outgoing-secret barrier. |
| Sensitivity metadata | Missing, unreadable or non-file metadata does not uniformly produce the strictest classification. Do not rely on this as a complete fail-closed boundary. |
| Web bridge | Navigation is not restricted to bundled pages. An isolated probe confirmed the channel remained callable after navigation to a different local page; a full HTTP attack was not reproduced. |
| Runtime records | Published ignore rules do not cover every project runtime artifact; local Git excludes are not portable. Review the exact staged files before committing runs, conversations or project memory. |
| Decision log | Retained hashes do not prove completeness, truth or resistance to a rewritten chain. Tail truncation and some malformed/unchained records are not reliably reported as verification failures. |
| Dependencies | The allowed pypdf range includes older vulnerable versions, and Qt 6.11.1 has native advisories requiring attention. A clean Python-package metadata lookup is not assurance about all bundled native code; no complete reachable-exploit claim is made for every Qt advisory. |

The [PyInstaller spec](packaging/AI%20Control%20Center.spec) omitted `shell.js` in the reviewed snapshot. **Fixed after review:** it now packages `*.js` alongside the HTML and CSS, and a test requires every file type in `app/ui/web/` to be packaged. The frozen build has not been rebuilt and re-checked since. Compilation is not an installation acceptance test. Use source only for non-sensitive evaluation until applicable defects are addressed. Documentation and an MIT license do not remediate these issues.

## Execution and tests

The workflow passes permission settings to vendor tools, but AICC is not an OS sandbox and does not intercept every command. Stopping a process tree is best effort. A successful run does not establish that all file, credential or network boundaries held.

Run the test suite and mutation tooling only in a disposable checkout under an isolated Windows test user or VM. Tests perform filesystem operations; redirecting environment variables alone does not guarantee isolation. The mutation runner temporarily changes source and restores it, and currently does not independently establish every baseline before accepting a failure.

## Reports

No response or fix timetable is promised. Do not post credentials or private project data in public issues. If the repository enables GitHub private vulnerability reporting, use that channel; this document does not assert that the repository setting is enabled.
