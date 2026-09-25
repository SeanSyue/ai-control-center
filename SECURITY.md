# Security and known issues

This is a portfolio snapshot with no scheduled maintenance or promised response time. The author may revisit it, but fixes and releases are not committed. This document includes the 2026-09-26 follow-up on `2d4d838` and subsequent source changes described in [CHANGELOG.md](CHANGELOG.md). Review and implementation were AI-assisted; this is not an independent security certification. Earlier defects outside that follow-up remain explicitly listed below.

## Data and credentials

- Vendor sign-in belongs to the official Claude Code/Codex CLIs. AICC does not provide a vendor API-key entry flow or proxy that login. It can inspect local conversation files to associate sessions with projects.
- Prompts, selected context and code supplied to vendor CLIs may leave the machine through those tools. Do not describe this as “nothing is uploaded.”
- Optional LINE notifications use a user-supplied token stored with Windows user-scoped DPAPI. DPAPI does not protect all project records or isolate malware running as that user. Notification content varies with sensitivity; even a project folder name may identify a customer.
- Optional Drive integration copies files through a configured sync folder; the external Drive client can upload them. Local-first is not a no-cloud guarantee.
- The reviewed release archives and reachable public Git content had no confirmed real credentials. Test fixtures intentionally contain fake keys. This result is not a promise about later commits or runtime files.

## Confirmed issues to address before sensitive use

| Area | Known boundary or defect |
|---|---|
| Workspace import | The project root must resolve directly under the chosen destination. **2026-09-26:** an existing target directory or file now stops the import; it is not merged or overwritten. Archive member paths reject ADS colons, Windows device names, ambiguous trailing dots/spaces and invalid characters. Rejected members are reported; valid members may still be imported. **Still open:** archive resource limits (total size/file count), transactional staging and protection against another process changing the filesystem during import. A failed import can leave its new partial directory; inspect it before manually removing or renaming it. See [`workspace_portability.py`](app/services/workspace_portability.py). |
| Drive synchronization (A11, intentionally deferred) | Canonical UUID and project-root checks block the original absolute-ID case. **Unfixed:** a junction/reparse point below a valid project root, such as `.aicc`, can redirect file operations outside the intended directory. Do not treat the sync feature as a boundary against an untrusted folder tree; leave it disabled for such evaluation. Future work could validate both local and cloud paths and every ancestor at each read/write, reject unapproved reparse points, and use handle-based operations to address path-change races. Those measures are proposals, not implemented protections. Remote metadata is not trusted local policy. See [`cloud_sync.py`](app/services/cloud_sync.py). |
| Deletion ownership | [`not_ours.py`](app/services/not_ours.py) uses protected-root rules rather than proof of ownership. Unknown paths may be allowed while legitimate LINE credential removal may be blocked. |
| Installer | **2026-09-26:** the [Inno script](packaging/AI%20Control%20Center.iss) keeps the previous directory under an unused `.previous` / `.previous.N` name; existing backups are retained. Backup failure stops the upgrade. Recovery first preserves a partial directory as `.incomplete` / `.incomplete.N`, then attempts to restore the previous directory. The custom script no longer recursively deletes those trees. The product executable is only a recognition marker, not ownership proof. Review and manually clean backups only after checking the new installation and retrieving needed files. ISCC compilation and structural tests passed; a normal Windows install/upgrade/cancel cycle is **still unverified**. Directory recovery does not promise transactional recovery of registry entries or shortcuts. Version comparison still uses string order. |
| Sensitive prompts | Newly created sensitive files such as `.env.staging` or a private-key file can enter review material. Existing scanning is not a complete outgoing-secret barrier. |
| Sensitivity metadata | Missing, unreadable or non-file metadata does not uniformly produce the strictest classification. Do not rely on this as a complete fail-closed boundary. |
| Web bridge | Navigation is not restricted to bundled pages. An isolated probe confirmed the channel remained callable after navigation to a different local page; a full HTTP attack was not reproduced. |
| Runtime records | Published ignore rules do not cover every project runtime artifact; local Git excludes are not portable. Review the exact staged files before committing runs, conversations or project memory. |
| Decision log | Retained hashes do not prove completeness, truth or resistance to a rewritten chain. Tail truncation and some malformed/unchained records are not reliably reported as verification failures. |
| Dependencies | The allowed pypdf range includes older vulnerable versions, and Qt 6.11.1 has native advisories requiring attention. A clean Python-package metadata lookup is not assurance about all bundled native code; no complete reachable-exploit claim is made for every Qt advisory. |

The [PyInstaller spec](packaging/AI%20Control%20Center.spec) omitted `shell.js` in the original snapshot. It now includes that file. On 2026-09-26 the updated source was built again and the frozen asset checker passed all 10 page scenarios. Compilation and page checks are not an installation acceptance test. Use source for non-sensitive evaluation until applicable defects are addressed. Documentation and an MIT license do not remediate these issues.

## Execution and tests

The workflow passes permission settings to vendor tools, but AICC is not an OS sandbox and does not intercept every command. Stopping a process tree is best effort. A successful run does not establish that all file, credential or network boundaries held.

Run the test suite and mutation tooling only in a disposable checkout under an isolated Windows test user or VM. Tests perform filesystem operations; redirecting environment variables alone does not guarantee isolation. The mutation runner temporarily changes source and restores it, and currently does not independently establish every baseline before accepting a failure.

## Reports

No response or fix timetable is promised. Do not post credentials or private project data in public issues. If the repository enables GitHub private vulnerability reporting, use that channel; this document does not assert that the repository setting is enabled.
