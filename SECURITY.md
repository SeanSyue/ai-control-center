# Security

## Reporting a vulnerability

Please use GitHub's **"Report a vulnerability"** button on the Security tab of this repository. That opens a private advisory. Do not open a public issue for a security problem.

I am one person and this is a side project; expect a reply in days, not hours.

## What AICC does with credentials

- **It never asks for an API key.** The sign-in screen has no function that can accept one, and three tests fail if such a function appears.
- **It never proxies a sign-in.** It starts the official Claude Code and Codex command-line tools, which use the sign-in already on the machine.
- **The only secret it stores** is a notification token you paste in yourself, encrypted with Windows DPAPI.
- **Nothing is uploaded.** AICC is local-first: no cloud sync, no telemetry.

## What it does with your files

- Each project carries a sensitivity level. If that value cannot be read, the strictest level is used, not the loosest.
- The writing step runs with write access limited to the project folder. Review steps run read-only.
- AICC deletes only folders it created itself.
- Replies arriving through a notification channel never count as approval.

## Known boundaries

- Agent safety rules are enforced through their instructions and each tool's permission mode, not by blocking individual commands.
- The decision log detects an entry that was edited or removed. It does not prove the log is honest: anyone who can edit the file can recompute the whole chain. The verifier says so itself.
