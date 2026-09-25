# Working with this snapshot

No regular maintenance, review time or acceptance of contributions is promised. You may study, fork and modify the code under the [MIT license](LICENSE). The author reports successful end-to-end use; private project outputs are not required or included.

## Setup and verification

Follow the source setup in [README.md](README.md). For development tests, use a disposable checkout under an isolated Windows user or VM. Do not use a profile containing valuable project data or credentials; tests include file operations and cleanup code.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe tools\verify_web_pages.py
```

Record failures, skips, process exit codes and environment constraints honestly. The original verification declaration expects a clean pass; the review did not independently achieve a single-process all-green run. Current counts and limitations are in the README.

For a behavior change, run the relevant existing checks and test the failure case where useful. Mutation recipes live in `tools/mutations/` (111 files, 853 items in the reviewed snapshot):

```powershell
.\.venv\Scripts\python.exe tools\negative_control.py --all
```

This temporarily edits source before restoring it. First establish that the selected test passes without mutation; an already-failing baseline cannot show that the mutation was caught. Update stale or equivalent recipes and verify restoration. The current runner does not enforce all of those conditions automatically.

## Editing the UI

`design-pages/` is the editable source. Rebuild the committed pages with:

```powershell
.\.venv\Scripts\python.exe app\ui\web\build_pages.py
.\.venv\Scripts\python.exe tools\verify_web_pages.py
```

The builder checks the copied style blocks. For packaging changes, separately check required HTML/CSS/JS in the frozen output and test installation in a disposable Windows environment; source-page success is not enough.

## Keep private state outside the release

The author's private project memory and project outputs are deliberately absent. Your projects supply their own context. Never commit vendor login files, LINE tokens, `.env` values, real conversations, run artifacts or personal project memory. Ignore rules are incomplete in this snapshot: inspect staged files, not just the ignore configuration.

Keep documentation consistent with [SECURITY.md](SECURITY.md). Historical design notes explain earlier choices; they do not override current known defects or maintenance status.
