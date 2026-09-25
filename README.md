# AI Control Center (AICC)

**A Windows desktop workspace for coordinating Claude Code and Codex: assign work, carry context forward, check results, and return unresolved decisions to a person.**

[繁體中文](README.zh-TW.md) · [Architecture and design ownership](docs/ARCHITECTURE.md) · [Documentation map](docs/README.md) · [Security and known issues](SECURITY.md)

> **Portfolio snapshot; no scheduled maintenance.** The author reports successful end-to-end runs in a configured personal environment. Private project outputs and conversations are not included. This is a working prototype with known defects, not a production or security certification. Start from source; the packaged build has unresolved issues.

![The line-up page](docs/images/lineup.png)

*Screenshots use demo data.*

## The problem I set out to solve

Using two coding agents often makes the person their message courier: copy the plan, explain the same context again, forward a review, then remember which version was actually checked. AICC puts that coordination into an explicit workflow and records the relationship between work, verification, review and human decisions.

It brings existing Claude/Codex conversations, project documents, shared memory, a role line-up and execution records into one desktop application. It invokes the official vendor CLIs under the user's own sign-in. Prompts can be sent to the vendors through those CLIs; “local-first” does not mean all processing stays on the machine.

## My role and use of AI

I independently defined the product, overall architecture, workflow rules and module boundaries, and built the project with AI assistance. I used discussions with AI to examine alternatives, generate and revise code, investigate failures, and develop verification approaches. Final scope and design decisions remained mine.

Prompt, Context, Harness, Loop and Graph Engineering describe the concepts I applied to this implementation; I am not claiming to have invented those concepts. This repository presents the resulting code, design choices and limitations, rather than claiming every line was written manually or that AI review replaces external assurance.

## Design choices to inspect

| Concern | Implementation and reason |
|---|---|
| Prompt and context | Duty-specific instructions, layered rules and project-document retrieval keep the task and relevant constraints together. Required security rules are intended to be carried without silent truncation. |
| Execution harness | Vendor CLI process management is separated from workflow logic. Permission arguments are passed to those tools; AICC itself is not an operating-system sandbox. |
| Fix loop | Declared project verification drives a bounded automatic repair loop (up to two attempts). Exhaustion returns control to a person instead of manufacturing a pass. |
| Roles and graph | Seats carry provider, duty and identity. The `parent` relation describes the displayed structure; execution order and duty rules drive the workflow. It is not a general-purpose visual DAG scheduler. |
| Version-bound evidence | Machine evidence, review reasons and approval are different records. Stamps refer to content versions so changed work does not inherit an unrelated approval. |

See [architecture](docs/ARCHITECTURE.md) for the module map, entry points and tradeoffs. The orchestrator and main window remain substantial modules; this is an incremental separation of responsibilities, not a claim of perfect decoupling.

## What is available

- Pair local Claude/Codex conversations with a project and configure a role line-up.
- Coordinate planning, work, declared verification, review, approval and questions for the user.
- Index project documents and maintain plain-text project memory.
- Inspect run outcomes, decision records and a multi-project dashboard.
- Optional LINE notifications and folder-based synchronization through a configured Google Drive client. These have their own data boundaries and known issues; see [SECURITY.md](SECURITY.md).

## Run from source

Prerequisites: Windows 10/11, Python 3.14, Git if the workflow uses Git, and both vendor CLIs installed and signed in through their official tools. The accounts must be eligible for the selected models and have available usage. AICC does not supply accounts or bypass provider limits. The interface is Traditional Chinese.

Download or clone this repository, open PowerShell in its root, then run:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.main
```

After setup, `start.bat` starts the application using that environment. The committed `app/ui/web/` pages are included; building an installer is not a prerequisite for running from source.

For an initial trial, use a disposable project containing non-sensitive data, pair the intended vendor sessions, configure the roles, and declare the project's verification command. With no usable verification, a run must not be described as machine-verified. The author's private project memory is not required to install AICC; each project supplies its own context. LINE and Drive setup are optional and can remain disabled.

The author reports successful configured runs. That experience does not establish that every fresh Windows machine, CLI version or task will behave identically.

## Verification status

The source contains automated tests, mutation recipes and a real QWebEngine page checker. Verification results below refer to the reviewed code snapshot `15c1324`, not to a promise that every run is green.

| Check | Recorded result |
|---|---|
| Author's functional use | Successful end-to-end runs reported; private outputs are deliberately not published. |
| Review of the original tests | 2,219 cases included in per-file execution; latest aggregate 2,200 passed, 19 failed, 0 skipped. 18 failing cases received narrower supplemental checks for environment/fixture constraints; those are not original-suite passes. One guard-boundary failure remains. The single-process run crashed in Qt. |
| Mutation checks | All 853 items across 111 recipe files executed: 846 red, 7 green, 0 broken. 25 red results had an already-failing baseline and are not valid mutation kills. |
| Source UI | 10/10 loading scenarios passed at each of two window sizes. |
| Packaging | PyInstaller and Inno compilation succeeded; the frozen UI passed 8/10 scenarios because `shell.js` was omitted (fixed in the spec afterwards; the frozen build was not rebuilt). A full install/upgrade/cancel cycle was not independently completed. |

After the review, four defects were fixed — import and sync path containment, installer folder ownership, and the missing packaged `shell.js`. Each fix has new tests and negative-control recipes, run in the maintainer's environment; the reviewer has not re-run them. Details in [CHANGELOG.md](CHANGELOG.md).

For development, the existing commands are:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe tools\verify_web_pages.py
.\.venv\Scripts\python.exe tools\negative_control.py --all
```

The mutation command temporarily edits source files before restoring them. Run development checks in a disposable checkout under an isolated Windows test user or VM, not against a valuable working environment. A test must pass before mutation for its later failure to count as useful evidence. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Limits of this snapshot

Import and sync path containment, installer folder ownership and the missing packaged `shell.js` were fixed after the review. Still open: deletion ownership, sensitive content reaching review prompts, archive resource limits and the other items in [SECURITY.md](SECURITY.md). These are disclosed defects, not features made safe by documentation. Read SECURITY.md before reuse.

The deletion guard currently protects a few important locations (such as the Windows user profile). It is not yet "only the project folder, and anything else needs the user's confirmation".

The "forget LINE credentials" button currently has no effect; to remove LINE settings, delete the LINE settings file under `%APPDATA%\AIControlCenter` by hand.

The app depends on external CLIs, their permission behavior and account limits; it is not a guarantee that an agent cannot touch other files. The role duties are fixed, the UI is not multilingual, and hitting usage limits requires user intervention. A hash-chained decision record can check retained links; it does not prove truth, completeness or resistance to a rewritten chain.

## Read the repository

| Location | What to look for |
|---|---|
| [`app/core/`](app/core/) | Workflow, roles, prompts, step dispatch and outcomes |
| [`app/adapters/`](app/adapters/) | Vendor CLI process boundary |
| [`app/services/`](app/services/) | Project state, knowledge, verification, evidence, imports and optional integrations |
| [`app/ui/`](app/ui/) | Qt presentation and web-panel bridges |
| [`design-pages/`](design-pages/) | Source HTML/CSS/JS; `app/ui/web/` is generated from it |
| [`tests/`](tests/), [`tools/`](tools/) | Behavior checks, mutation recipes and inspection tools |
| [`docs/`](docs/README.md) | Current architecture guide followed by historical design notes |
| [`packaging/`](packaging/) | Build and installer definitions; what was fixed and what remains is in SECURITY.md |

No regular updates or response times are promised. The historical roadmap records ideas, not a delivery commitment. Code is under the [MIT license](LICENSE); see [contribution guidance](CONTRIBUTING.md) for reuse.
