from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# The `[A-Za-z0-9_]*` prefix on each name matters more than it looks. With a
# plain `\b` in front, `LINE_CHANNEL_ACCESS_TOKEN=...` did NOT match
# `access_token` - `\b` needs a non-word character before `ACCESS`, and `_` is a
# word character. The most common real shape of a leaked credential, an
# env-var-style name, was therefore never caught by the name rules at all; it
# only ever tripped `long_secret`, by accident, for having a long value.
SECRET_PATTERNS = (
    ("api_key", re.compile(r"(?i)\b[A-Za-z0-9_]*api[_-]?key\b\s*[:=]\s*[^\s,;]+")),
    ("access_token", re.compile(r"(?i)\b[A-Za-z0-9_]*(access[_-]?)?token\b\s*[:=]\s*[^\s,;]+")),
    ("secret", re.compile(r"(?i)\b[A-Za-z0-9_]*secret\b\s*[:=]\s*[^\s,;]+")),
    ("password", re.compile(r"(?i)\b[A-Za-z0-9_]*(password|passwd|pwd)\b\s*[:=]\s*[^\s,;]+")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b")),
    ("jwt", re.compile(r"\b[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b")),
    ("long_secret", re.compile(r"\b[A-Za-z0-9_\-]{48,}\b")),
)

# Not every pattern is equally sure of itself, and `has_blocking_findings` used
# to treat them as if they were: every match was "high", and one high finding is
# enough to stop a file syncing. Measured on this project's own DECISIONS.md -
# three findings, all high, all on ordinary prose, and the result was that the
# single most important handoff document was the one file Drive sync silently
# refused to upload.
#
# The graded ones below still appear in the report; they just do not block.
SEVERITY_BY_KIND = {
    # Real credential shapes. A string matching these is a secret or a very
    # deliberate imitation of one.
    "openai_key": "high",
    "jwt": "high",
    # `name = value` shapes. Suggestive, and worth blocking on: a line that
    # literally reads `password=...` is not something to wave through.
    "api_key": "high",
    "access_token": "high",
    "secret": "high",
    "password": "high",
    # Pure length heuristic with no structure behind it - it cannot tell a
    # credential from a long file name. Reported, never blocking.
    "long_secret": "medium",
}

# A real high-entropy token is one run of characters. A long name is words
# joined by separators - `AIControlCenter-PRE-FILTER-REPO-BACKUP-2026-08-05`
# is 49 characters and matched `long_secret` purely for being long. Base64 and
# hex secrets have no separators at all; `sk-`-style keys have one.
MAX_SEPARATORS_IN_SECRET = 2

SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "token.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}

SKIPPED_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "_update_backup",
    ".tmp.driveupload",
}

# `withhold_reason()` 回的兩種理由。**是理由不是介面文字**——顯示的句子在
# `app/i18n.py`，這裡的值是拿來比對的。
WITHHOLD_SECRET = "secret"
WITHHOLD_RUNTIME = "runtime"


def withhold_reason(relative_path: str) -> str:
    """這個檔案的**內容**可不可以離開這台機器。回 `""` 代表可以。

    `SECURITY_MODEL.md`：憑證（`.env`、金鑰、token）與執行期狀態（`.venv`、
    快取、相依套件）**永不同步或匯出**，而且不准用「先遮蔽再送出」達成。

    改壞會怎樣：那一輪新建的 `.env` 會整份被貼進派給廠商的提示詞——
    **而那一輪的畫面、紀錄、章，跟一輪沒有 .env 的執行長得一模一樣**，
    沒有任何一個地方會變紅。

    這裡刻意用上面那兩份清單，不另外抄一份：抄的那一份漏掉 `id_rsa` 的那天，
    掃描器會說有問題、送出去的那一條路不會。
    """
    parts = [part.casefold() for part in Path(relative_path).parts]
    if not parts:
        return ""
    if parts[-1] in {name.casefold() for name in SENSITIVE_NAMES}:
        return WITHHOLD_SECRET
    if set(parts) & {item.casefold() for item in SKIPPED_PARTS}:
        return WITHHOLD_RUNTIME
    return ""

# 掃描器願意打開多大的檔案。
#
# **這個數字有一個下限，而那個下限不是品味問題**：`GoogleDriveFolderSync`
# 准 5 MB 以下的檔案上傳到共用的 Drive 資料夾（`MAX_FILE_SIZE`），而這裡
# 2026-08-24 之前寫的是 512 KB。中間那 4.5 MB 是一個「會同步、但永遠不會
# 被讀」的窗口——而白名單准出去的 `.aicc/timeline/*.jsonl` 與 `.aicc/runs/**`
# 裝的正是逐字 prompt 與逐字輸出（貼上的 API key 就躺在那裡），也正好動不動
# 就超過 512 KB。
#
# 實測過，不是推論：同一把 sk- 金鑰放在同一個資料夾的兩個檔案裡跑一次同步，
# 42 bytes 那個被擋下並附上「疑似 api_key」，840 KB 那個**顯示已上傳**、
# `success=True`、`errors=[]`，金鑰原封不動躺在 Drive 副本第一行。畫面上跟
# 一次真的通過掃描的上傳一個字都不差。
#
# 這一整支檔案裡每一個常數都有一段理由註解，只有這一個沒有——而它就是漏掉
# 的那一個。兩條界線各自演進，中間就會長出縫；所以它現在被
# `tests/test_a_file_too_big_to_scan_is_not_uploaded_unscanned.py` 釘在同步
# 的上限上，任何一邊先動都會變紅。
MAX_SCAN_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class SecurityFinding:
    path: str
    kind: str
    severity: str
    line: int = 0
    detail: str = ""


@dataclass(frozen=True)
class SecurityScanReport:
    scanned_files: int
    skipped_files: int
    findings: tuple[SecurityFinding, ...]

    @property
    def has_blocking_findings(self) -> bool:
        return any(item.severity == "high" for item in self.findings)

    def summary(self) -> str:
        high = sum(item.severity == "high" for item in self.findings)
        medium = sum(item.severity == "medium" for item in self.findings)
        if not self.findings:
            return f"安全掃描完成：掃描 {self.scanned_files} 個檔案，未發現阻擋項目。"
        return (
            f"安全掃描完成：掃描 {self.scanned_files} 個檔案，"
            f"高風險 {high}、中風險 {medium}。"
        )

    def format_markdown(self) -> str:
        lines = [
            "# Security Scan Report",
            "",
            self.summary(),
            "",
            f"- Scanned files: {self.scanned_files}",
            f"- Skipped files: {self.skipped_files}",
            f"- Blocking findings: {'yes' if self.has_blocking_findings else 'no'}",
        ]
        if self.findings:
            lines.extend(["", "## Findings"])
            for finding in self.findings:
                location = finding.path
                if finding.line:
                    location += f":{finding.line}"
                lines.append(
                    f"- [{finding.severity}] {finding.kind} at {location}"
                    + (f" - {finding.detail}" if finding.detail else "")
                )
        return "\n".join(lines) + "\n"


class SecurityScanner:
    def scan_text(self, text: str, path: str = "<memory>") -> SecurityScanReport:
        findings = tuple(self._scan_text_findings(text, path))
        return SecurityScanReport(
            scanned_files=1,
            skipped_files=0,
            findings=findings,
        )

    def scan_paths(self, paths: list[Path], root: Path | None = None) -> SecurityScanReport:
        # 略過清單比對的是**專案裡面**的資料夾名，所以要先知道專案在哪。
        # 沒有 root 的時候（`cloud_sync` 逐檔問「這一個能不能傳」就是這樣叫的）
        # 一律不略過——理由見 `_should_skip`。
        boundary = root.expanduser().resolve() if root is not None else None
        scanned = 0
        skipped = 0
        findings: list[SecurityFinding] = []
        for raw in paths:
            path = raw.expanduser().resolve()
            if not path.is_file():
                skipped += 1
                continue
            if self._should_skip(path, boundary):
                skipped += 1
                continue
            try:
                if path.stat().st_size > MAX_SCAN_BYTES:
                    skipped += 1
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                skipped += 1
                continue
            scanned += 1
            display = self._display_path(path, root)
            findings.extend(self._scan_file_name(path, display))
            findings.extend(self._scan_text_findings(text, display))
        return SecurityScanReport(
            scanned_files=scanned,
            skipped_files=skipped,
            findings=tuple(findings),
        )

    def scan_project(self, root: Path) -> SecurityScanReport:
        project = root.expanduser().resolve()
        if not project.is_dir():
            return SecurityScanReport(
                scanned_files=0,
                skipped_files=1,
                findings=(
                    SecurityFinding(
                        path=str(project),
                        kind="missing_project",
                        severity="high",
                        detail="Project folder does not exist.",
                    ),
                ),
            )
        paths = [path for path in project.rglob("*") if path.is_file()]
        return self.scan_paths(paths, root=project)

    def _scan_file_name(self, path: Path, display: str) -> list[SecurityFinding]:
        if path.name.casefold() not in SENSITIVE_NAMES:
            return []
        return [
            SecurityFinding(
                path=display,
                kind="sensitive_file_name",
                severity="high",
                detail="This file name usually contains credentials or secrets.",
            )
        ]

    def _scan_text_findings(self, text: str, path: str) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        for index, line in enumerate(text.splitlines(), start=1):
            for kind, pattern in SECRET_PATTERNS:
                match = pattern.search(line)
                if match is None:
                    continue
                if kind == "long_secret" and not self._looks_unstructured(
                    match.group(0)
                ):
                    # A long hyphenated name, not a token. Keep looking: a real
                    # secret elsewhere on the same line should still be found.
                    continue
                findings.append(
                    SecurityFinding(
                        path=path,
                        kind=kind,
                        severity=SEVERITY_BY_KIND.get(kind, "high"),
                        line=index,
                        detail="Potential credential-like value.",
                    )
                )
                break
        return findings

    @staticmethod
    def _looks_unstructured(value: str) -> bool:
        """Whether a long match reads like a token rather than a name."""
        return value.count("-") + value.count("_") <= MAX_SEPARATORS_IN_SECRET

    @staticmethod
    def _display_path(path: Path, root: Path | None) -> str:
        if root is None:
            return str(path)
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _should_skip(path: Path, root: Path | None) -> bool:
        """這個檔案在不該掃的資料夾裡嗎。**只看專案界線以內的那幾層。**

        2026-08-24 之前比對的是整條絕對路徑的每一層，包含專案上面那幾層——
        而那幾層是使用者的資料夾結構，不是這個專案的。把專案放在
        `D:\\node_modules\\我的專案` 或任何一層叫 `.git`／`.venv` 的地方，
        **整個專案的每一個檔案都會被靜悄悄略過**，然後同步照樣把它們傳上去。
        報告上看起來是「掃了 0 個、略過 N 個、沒有阻擋項目」，而呼叫端只問
        「有沒有阻擋項目」。

        `root` 是 `None` 的時候（`cloud_sync._is_upload_blocked` 逐檔問的那條路）
        **沒有專案界線可以參考，所以一層都不略過**——寧可多掃一個檔案，
        也不要因為使用者把專案放在一個名字剛好撞上的資料夾底下就整批不掃。
        失敗往「掃下去」倒，不往「跳過」倒。

        擋不住什麼：略過清單本身漏掉的資料夾（例如某個廠商的快取目錄）
        照樣會被掃，那不是這條在守的東西——它守的是「該掃的沒被掃到」。
        """
        if root is None:
            return False
        try:
            inside = path.relative_to(root)
        except ValueError:
            # 不在專案底下的檔案（呼叫端自己挑進來的），同樣沒有界線可用。
            return False
        parts = {part.casefold() for part in inside.parts}
        return bool(parts & {item.casefold() for item in SKIPPED_PARTS})
