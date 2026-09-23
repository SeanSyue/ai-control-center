"""Turn a project's Decision Gate log into something you can hand to someone.

`PITCH.md` used to claim AICC could 「證明 AI 生成的判斷有經過人員審核」. On
2026-08-08 that was walked back to 「本機查閱、匯出還在路線圖上」, because the
record only existed as one JSONL file per project on one person's machine. This
is what earns the sentence back.

Two files, because there are two readers:

* `決策紀錄_<專案>_<日期>.md` - for a person. Question, who answered, what they
  said, when.
* `決策紀錄_<專案>_<日期>.jsonl` - the raw chain, byte-identical to what was
  recorded, so `tools/verify_decision_log.py` can check it independently.

**What the chain proves, stated the same way everywhere:** a line edited or
removed after the fact will not verify. It does **not** prove the log is
honest - whoever can edit the file can also recompute the whole chain. Saying
「無法竄改」 would be the same overclaim that had to be retracted once already.
"""
from __future__ import annotations

from app.i18n import t

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.services.handover import handover_marker
from app.services.long_task import DecisionLogEntry, DecisionLogStore
from app.services.project_registry import ProjectRegistry

# **存 key 不存字**（模組層取出來會在 import 的當下凍住）。
ACTION_LABELS = {
    "ASK_USER": "audit.kind.needs_human",
    "ANSWERED": "audit.kind.human_answered",
    "CONTINUE": "audit.kind.codex_approved",
    "EXPORT_AUTHORISED": "export.kyc.authorised",
}

ANSWERED_BY_LABELS = {
    "user": "audit.answered_by.user",
    "codex": "audit.answered_by.codex",
}


class DecisionExportBlocked(Exception):
    """Raised instead of writing anything. Carries the reason for the UI."""


@dataclass(frozen=True)
class ExportAuthorisation:
    """One person, one export, one reason. The controlled exception
    SECURITY_MODEL.md imagined and ROADMAP P5 never designed.

    Deliberately narrow, because every widening here is a hole in the one rule
    that makes AICC usable for case work at all:

    * **Only the audit log.** Workspace export and Drive sync stay hard-blocked
      for `sensitive_kyc`. Those move the case files; this moves a record of
      decisions about them.
    * **Never persisted.** There is no 「這個專案以後都可以」 setting, because
      that is not an exception, it is a downgrade of the project's sensitivity
      with none of the thinking that would deserve.
    * **Reason required, and it goes into the log** before the export runs - so
      the exported file contains the record of its own authorisation. Someone
      reading the export later can see why it left the machine.
    * **Contents acknowledged explicitly.** The file carries raw questions and
      answers, which for a KYC project may name a client. Nothing here redacts
      that (ROADMAP P8's redaction tool is not built), so the person authorising
      has to say they know what is in it.
    """

    reason: str
    acknowledged_contents: bool = False


@dataclass(frozen=True)
class DecisionExportResult:
    markdown_path: Path
    jsonl_path: Path
    entries: int
    verified: int
    unchained: int
    broken_at: int | None

    @property
    def intact(self) -> bool:
        return self.broken_at is None


class DecisionExportService:
    """Writes the pair of files. Never decides policy on its own."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = Path(project_path).expanduser().resolve()
        self.store = DecisionLogStore(self.project_path)

    def export(
        self,
        destination: Path,
        authorisation: ExportAuthorisation | None = None,
    ) -> DecisionExportResult:
        """Write both files into `destination`.

        A `sensitive_kyc` project refuses unless `authorisation` is supplied -
        see `ExportAuthorisation` for why that exception is as narrow as it is.
        Without one the refusal is the same rule workspace export and Drive
        sync already enforce, and this is a third way out of the machine: it
        must not be the one that quietly doesn't check.

        What is deliberately *not* the mechanism: running the log through
        `SecurityScanner` and letting it pass. The scanner finds API keys, not
        client names - it would be a check that looks like protection while
        guarding the wrong thing.
        """
        sensitivity = ProjectRegistry.get_sensitivity(self.project_path)
        if sensitivity == "sensitive_kyc":
            self._authorise(authorisation)

        entries = self.store.list_entries()
        status = self.store.verify()
        destination = Path(destination).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d")
        base = f"決策紀錄_{self.project_path.name}_{stamp}"
        jsonl_path = destination / f"{base}.jsonl"
        markdown_path = destination / f"{base}.md"

        # Copied verbatim, not re-serialised: a re-serialised line may differ
        # by key order or spacing and then fail its own hash, which would make
        # the exported copy unverifiable through no fault of the log.
        source_text = (
            self.store.path.read_text(encoding="utf-8")
            if self.store.path.is_file()
            else ""
        )
        jsonl_path.write_text(source_text, encoding="utf-8")
        markdown_path.write_text(
            self._render(entries, status, jsonl_path.name), encoding="utf-8"
        )

        return DecisionExportResult(
            markdown_path=markdown_path,
            jsonl_path=jsonl_path,
            entries=len(entries),
            verified=status.verified,
            unchained=status.unchained,
            broken_at=status.broken_at,
        )

    def _authorise(self, authorisation: ExportAuthorisation | None) -> None:
        """Check the exception, then record it - in that order, and before the
        export reads the log, so the authorisation appears in the file it
        authorised.
        """
        if authorisation is None:
            raise DecisionExportBlocked(
                t("export.kyc.refused")
            )
        if not authorisation.reason.strip():
            raise DecisionExportBlocked(
                t("export.kyc.reason_required")
            )
        if not authorisation.acknowledged_contents:
            raise DecisionExportBlocked(
                t("export.kyc.ack_required")
            )

        self.store.append(
            DecisionLogEntry(
                run_id="",
                action="EXPORT_AUTHORISED",
                question=t("export.kyc.gate_question"),
                reason=authorisation.reason.strip(),
                source="user",
                answer=authorisation.reason.strip(),
                answered_by="user",
            )
        )

    # ---- rendering -------------------------------------------------------
    def _render(self, entries, status, jsonl_name: str) -> str:
        lines = [
            f"# 決策紀錄：{self.project_path.name}",
            "",
            f"- 匯出時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"- 專案路徑：`{self.project_path}`",
            f"- 紀錄筆數：{len(entries)}",
        ]

        # 這條鏈從哪裡開始。使用者 2026-08-16 定案，起因是他自己的專案：舊機
        # 的 AICC 資料在搬家後被刪光，2026-08-07 以前那段永遠不存在了。而這份
        # 檔案原本只會說「N 筆通過驗證」——真的、看起來完整，**而且不會說它只
        # 涵蓋一段時間**。收到的人沒有理由懷疑手上這份不是全部。
        #
        # 不寫死日期：讀第一筆的時間。寫死的話這句話只對這一台機器是真的。
        if entries:
            lines.append(f"- 涵蓋期間：{self._readable(entries[0].created_at)} 起")

        if status.broken_at is not None:
            lines += [
                "",
                f"> **完整性檢查未通過：第 {status.broken_at + 1} 筆{status.reason}。**",
                t("export.tampered"),
            ]
        else:
            detail = f"{status.verified} 筆通過驗證"
            if status.unchained:
                detail += f"，另有 {status.unchained} 筆為指紋機制上線前寫入、無法驗證"
            lines += ["", f"- 完整性：{detail}"]

        lines += [
            "",
            t("export.proof.heading"),
            "",
            t("export.proof.how"),
            "",
            t("export.proof.can"),
            t("export.proof.cannot_honest"),
            t("export.proof.cannot_complete"),
            # 不要寫成「上面那個『涵蓋期間』」——一筆紀錄都沒有的時候那一行
            # 不會出現，而一句指著不存在欄位的說明，讀起來像是有東西被砍掉了。
            t("export.proof.cannot_history"),
            "",]
        lines += [
            *self._left_behind_lines(),
            f"獨立驗證（不需要開 AICC）：`python tools/verify_decision_log.py {jsonl_name}`",
            "",
            t("export.contains_raw"),
            "",
            "---",
            "",
        ]

        if not entries:
            lines += [t("export.no_records"), ""]
            return "\n".join(lines)

        for index, entry in enumerate(entries, start=1):
            when = self._readable(entry.created_at)
            # 那張表存的是 key。查不到就用引擎的代號本身——它至少講得出
            # 「這一筆是哪一種」，而空白講不出任何事。
            key = ACTION_LABELS.get(entry.action)
            label = t(key) if key else (entry.action or t(t("export.unlabelled")))
            lines += [f"## {index}. {when}　{label}", ""]
            if entry.question:
                lines.append(f"- **問題**：{entry.question}")
            if entry.reason:
                lines.append(f"- 判斷理由：{entry.reason}")
            if entry.answer:
                lines.append(f"- **答覆**：{entry.answer}")
            if entry.answered_by:
                lines.append(
                    t("export.answered_by")
                    + (
                        t(ANSWERED_BY_LABELS[entry.answered_by])
                        if entry.answered_by in ANSWERED_BY_LABELS
                        else entry.answered_by
                    )
                )
            elif entry.action == "CONTINUE":
                # Written before `answered_by` existed. Say so rather than
                # leaving a blank that reads as "a person answered".
                lines.append(t("export.answered_by_codex"))
            if entry.run_id:
                lines.append(f"- 執行編號：`{entry.run_id}`")
            lines.append("")

        return "\n".join(lines)

    def _left_behind_lines(self) -> list[str]:
        """搬過來的專案：舊機留了幾筆，用匯出當下記的數字講。

        「這條鏈從 X 開始」對每個專案都成立，但**只有搬過來的專案講得出
        「而且舊機還有 N 筆」**，因為那個數字只有舊機知道（記在
        `handover.json` 的 `decision_entries`，見 `workspace_portability`）。

        分開成兩句是刻意的：一句是每份匯出都該有的界線，另一句是「你手上這份
        少了一段**已知**的東西」。後者不成立時不要硬講——一句講不出數字的
        警告，讀的人只會學會忽略它。
        """
        marker = handover_marker(self.project_path)
        if not isinstance(marker, dict):
            return []
        left_behind = marker.get("decision_entries")
        if not isinstance(left_behind, int) or left_behind <= 0:
            return []
        return [
            f"> **這個專案是從另一台機器搬過來的，舊機另有 {left_behind} 筆決策"
            "紀錄沒有跟過來。** 要完整的紀錄，得在舊機上另外匯出一份。",
            "",
        ]

    @staticmethod
    def _readable(created_at: str) -> str:
        try:
            return (
                datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                .astimezone()
                .strftime("%Y-%m-%d %H:%M")
            )
        except (ValueError, AttributeError):
            return created_at or t("export.no_timestamp")


def load_chain(path: Path) -> list[dict]:
    """Read an exported `.jsonl` back. Used by the standalone verifier."""
    out: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            out.append(raw)
    return out
