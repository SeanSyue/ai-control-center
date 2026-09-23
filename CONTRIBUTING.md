# Contributing

Thanks for looking. This project is early: it runs, it is covered by tests, and it has not completed a full run end to end yet. Issues that tell me what broke on your machine are worth more than features right now.

## Setup

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

The suite takes about four minutes.

## Two rules every change has to follow

**1. Zero failures and zero skips.** A skipped test is a test nobody is reading. The exit code has to be 0 as well — this project once spent weeks treating a real crash-on-exit as "just a quirk of the shell".

**2. A green test is not evidence until you have seen it red.** If you add or change a test, add a negative-control recipe that breaks the code it guards, and check the test actually turns red:

```powershell
.\.venv\Scripts\python.exe tools\negative_control.py --all
```

Recipes live in `tools/mutations/*.json`. A recipe is: in this file, replace this text with that text, then run this test and require a failure. The tool restores the file from a copy afterwards and checks the restore.

If a recipe can no longer find its text because the code moved, **update the recipe to the new location** — do not delete it.

## What is not in this repository

A handful of tests and negative-control recipes in the private working copy guard the maintainer's own workspace: a `CLAUDE.md` that sits above the repository, and a private folder of project memory. Those files are notes between one person and his agents, not product, so neither they nor the guards that read them ship here.

Everything that guards the product itself is here, and it all runs from a clean clone.

## Screens

`app/ui/web/` is generated. Edit `design-pages/` and rebuild:

```powershell
.\.venv\Scripts\python.exe app\ui\web\build_pages.py
.\.venv\Scripts\python.exe tools\verify_web_pages.py
```

Each page's `<style>` block is hashed before and after, so an accidental CSS change fails the build.

A field the engine sends must be read by the page: `tests/test_no_field_is_pushed_into_the_void.py` fails otherwise. That test exists because a field can be sent, never drawn, and the screen still looks fine.

## Language

Code comments, documentation and the interface are in Traditional Chinese; the repository's front page and these guides are in English. Pull requests in either language are fine.

## What this project will not take

- Anything that makes AICC hold an API key or proxy a sign-in. It drives the official CLIs under the user's own account, deliberately.
- Anything that sends project content to a service the user did not choose.
