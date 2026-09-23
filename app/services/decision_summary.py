"""每個專案的決策紀錄，數起來放在一起看（ROADMAP P8「跨專案彙整」）。

`decision_export.py` 回答「這一個專案做過哪些決定」，一次一個專案、輸出給人看。
這裡回答的是另一個問題：**「這段時間我一共批准了幾件，其中幾件其實沒有人看過。」**

那個「沒有人看過」才是這份彙整存在的理由。Decision Gate 會自己批准低風險的
問題並繼續執行，那是設計上要的；但如果一個月下來 90% 的決定都是 Codex 自己
批的，那 AICC 對這個專案的實際狀態就不是「AI 做事、人把關」，而是「AI 做事」。
那件事只有把數字放在一起才看得出來，單看一個專案的紀錄不會有感覺。

三件刻意的事：

* **不彙整內容，只彙整計數。** 問題與答案的原文留在各專案自己的紀錄裡。
  搬紀錄跟搬案件檔案不是同一件事，但把十個客戶的問答湊成一份，就開始像了。
* **驗不過的鏈算一類，不是丟掉也不是混進去。** 一個鏈斷掉的專案仍然會被列出來，
  但它的數字標成「不可信」——安靜地把它算進總數，等於用一份可能被改過的紀錄
  去回答「我批准了幾件」。
* **這份輸出含專案名稱。** 專案名稱常常就是客戶名稱，所以它是本機閱讀用的東西，
  不是可以直接寄出去的報表。要給別人看請先走 `redact-export` skill。
"""
from __future__ import annotations

from app.i18n import t

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.services.long_task import ChainStatus, DecisionLogStore
from app.services.project_registry import (
    sensitivity_label,
    ProjectRegistry,
)

# What a decision log's `action` values mean for this count. Kept explicit
# rather than "anything that is not ANSWERED is automatic": a new action added
# later would silently join the automatic pile, which is the pile whose size is
# the whole point.
HUMAN_ACTIONS = ("ANSWERED",)
AUTOMATIC_ACTIONS = ("CONTINUE",)
PENDING_ACTIONS = ("ASK_USER",)


@dataclass(frozen=True)
class ProjectDecisions:
    """One project's counts."""

    name: str
    path: Path
    sensitivity: str
    answered_by_person: int = 0
    approved_by_codex: int = 0
    still_waiting: int = 0
    # The open questions themselves, not just how many. ROADMAP P2
    # 「將未解問題集中，避免散在 timeline」 - a count tells you there is
    # something to deal with and not what it is, which is the part that
    # decides whether you deal with it now.
    open_questions: tuple[str, ...] = ()
    other: int = 0
    first_at: str = ""
    last_at: str = ""
    chain: ChainStatus | None = None
    unreadable: str = ""

    @property
    def total(self) -> int:
        return (
            self.answered_by_person
            + self.approved_by_codex
            + self.still_waiting
            + self.other
        )

    @property
    def trustworthy(self) -> bool:
        """Whether these numbers may be added to a total.

        False for a broken chain *and* for a log that could not be read at all.
        The two are different problems with the same consequence: the count is
        not evidence of anything.
        """
        if self.unreadable:
            return False
        return self.chain is None or self.chain.ok


@dataclass(frozen=True)
class DecisionSummary:
    projects: list[ProjectDecisions] = field(default_factory=list)

    @property
    def counted(self) -> list[ProjectDecisions]:
        return [p for p in self.projects if p.trustworthy and p.total]

    @property
    def excluded(self) -> list[ProjectDecisions]:
        """Projects deliberately left out of the totals, with a reason each.

        Returned rather than dropped: a summary that quietly omits the projects
        it could not verify answers a narrower question than the one it appears
        to answer.
        """
        return [p for p in self.projects if not p.trustworthy]

    @property
    def answered_by_person(self) -> int:
        return sum(p.answered_by_person for p in self.counted)

    @property
    def approved_by_codex(self) -> int:
        return sum(p.approved_by_codex for p in self.counted)

    @property
    def still_waiting(self) -> int:
        return sum(p.still_waiting for p in self.counted)

    @property
    def decided(self) -> int:
        """Decisions that were actually settled, by anyone."""
        return self.answered_by_person + self.approved_by_codex

    @property
    def human_share(self) -> float | None:
        """Fraction of settled decisions a person actually saw.

        None, not 0.0 or 1.0, when nothing has been settled. A ratio computed
        from an empty set would print as 「0% 有人看過」, which reads as an
        alarming finding rather than as "no data".
        """
        if not self.decided:
            return None
        return self.answered_by_person / self.decided


def _within(at: str, since: str, until: str) -> bool:
    if since and at < since:
        return False
    if until and at > until:
        return False
    return True


def summarise_project(
    name: str,
    path: Path,
    *,
    since: str = "",
    until: str = "",
) -> ProjectDecisions:
    """Count one project's decisions.

    `since` / `until` are ISO date prefixes compared as strings, which works
    because `created_at` is written as an ISO timestamp - and is stated here so
    nobody later passes 「8/1」 and gets an empty month with no error.
    """
    sensitivity = "personal"
    try:
        sensitivity = ProjectRegistry.get_sensitivity(path)
    except Exception:
        pass

    store = DecisionLogStore(path)
    try:
        entries = store.list_entries()
        chain = store.verify()
    except Exception as exc:
        return ProjectDecisions(
            name=name,
            path=path,
            sensitivity=sensitivity,
            unreadable=str(exc) or exc.__class__.__name__,
        )

    # Still open, from the pairing rule rather than by counting ASK_USER rows.
    # **This was wrong before**: an answered question writes two entries
    # (ASK_USER then ANSWERED), so counting the first put every question ever
    # asked into 「還在等」 - a project with one open question and nine settled
    # ones reported ten. Measured, not reasoned: a log with one answered and
    # one open question reported 2.
    open_questions = tuple(
        entry
        for entry in store.unanswered()
        if _within(entry.created_at or "", since, until)
    )

    counts = {"person": 0, "codex": 0, "other": 0}
    stamps: list[str] = []
    for entry in entries:
        at = entry.created_at or ""
        if not _within(at, since, until):
            continue
        stamps.append(at)
        if entry.action in HUMAN_ACTIONS:
            counts["person"] += 1
        elif entry.action in AUTOMATIC_ACTIONS:
            counts["codex"] += 1
        elif entry.action in PENDING_ACTIONS:
            # Counted above, by pairing. Nothing to add here - it is either
            # still open (in `open_questions`) or its ANSWERED row already
            # counted it as settled.
            continue
        else:
            counts["other"] += 1

    return ProjectDecisions(
        name=name,
        path=path,
        sensitivity=sensitivity,
        answered_by_person=counts["person"],
        approved_by_codex=counts["codex"],
        still_waiting=len(open_questions),
        open_questions=tuple(
            " ".join((entry.question or t("summary.no_question_text")).split())
            for entry in open_questions
        ),
        other=counts["other"],
        first_at=min(stamps) if stamps else "",
        last_at=max(stamps) if stamps else "",
        chain=chain,
    )


def summarise_all(
    *,
    since: str = "",
    until: str = "",
    registry: ProjectRegistry | None = None,
) -> DecisionSummary:
    """Every registered project, including the ones that could not be read.

    A project whose folder has gone missing is reported rather than skipped:
    「這個專案不見了」 and 「這個專案沒有決策」 must not look the same on a
    page whose job is to say what happened.
    """
    entries, _ = (registry or ProjectRegistry()).load()
    projects = []
    for entry in entries:
        path = entry.resolved_path
        if not path.is_dir():
            projects.append(
                ProjectDecisions(
                    name=entry.name,
                    path=path,
                    sensitivity="personal",
                    unreadable=t("project.path_missing.title"),
                )
            )
            continue
        projects.append(
            summarise_project(entry.name, path, since=since, until=until)
        )
    return DecisionSummary(projects=projects)


def render_open_questions(summary: DecisionSummary) -> str:
    """Every still-open question, in one place (ROADMAP P2).

    「未解問題散在 timeline 裡」 means: each one is a line inside one run's log
    inside one project's folder, and finding them means remembering which runs
    stopped. This is the one place.

    **Not written into `OPEN_QUESTIONS.md`, and that is deliberate.** That file
    is a shared-memory file - the memory maintainer rewrites it whole on every
    passing run (`write_memory_from_text` replaces the section keyed by the
    file name). Anything AICC appended there would be gone the next time a run
    finished, and the version that survived would be whichever wrote last.
    Two writers, one file, no merge: the fix is not to be the second writer.

    Kept behind its own call because it carries the question text, which is a
    step up in sensitivity from the counts - a question can name a client.
    """
    lines: list[str] = []
    for project in summary.projects:
        if not project.open_questions:
            continue
        label = sensitivity_label(project.sensitivity)
        lines.append(f"{project.name}（{label}）")
        for question in project.open_questions:
            lines.append(f"  · {question}")
        lines.append("")
    if not lines:
        return t("summary.nothing_waiting")
    return "\n".join(
        [
            t("summary.warn_raw_questions"),
            "",
            *lines,
        ]
    ).rstrip()


def render_summary(summary: DecisionSummary, *, since: str = "", until: str = "") -> str:
    """Plain text, for the terminal and for pasting into a note.

    Deliberately not Markdown-with-a-title: this is not an export. The header
    says so, because the file it would become carries project names.
    """
    window = ""
    if since or until:
        window = f"（{since or '最早'} 到 {until or '現在'}）"

    lines = [
        f"決策紀錄彙整{window}",
        f"產生時間：{datetime.now().isoformat(timespec='seconds')}",
        "",
        t("summary.warn_project_names"),
        t("summary.warn_local_only"),
        "",
    ]

    counted = summary.counted
    if counted:
        lines.append(
            f"{'專案':<24}{'人答':>6}{'Codex 自批':>12}{'還在等':>8}  敏感度"
        )
        lines.append("─" * 72)
        for project in counted:
            label = sensitivity_label(project.sensitivity)
            lines.append(
                f"{project.name[:22]:<24}"
                f"{project.answered_by_person:>6}"
                f"{project.approved_by_codex:>12}"
                f"{project.still_waiting:>8}  {label}"
            )
        lines.append("─" * 72)
        lines.append(
            f"{'合計':<24}"
            f"{summary.answered_by_person:>6}"
            f"{summary.approved_by_codex:>12}"
            f"{summary.still_waiting:>8}"
        )
    else:
        lines.append(t("summary.no_records_in_range"))

    lines.append("")
    share = summary.human_share
    if share is None:
        lines.append(t("summary.no_decided"))
    else:
        lines.append(
            f"已定案 {summary.decided} 件，其中 {summary.answered_by_person} 件"
            f"由人回答，占 {share:.0%}。"
        )
        if share < 0.5:
            lines.append(
                t("summary.mostly_auto_1")
            )
            lines.append(
                t("summary.mostly_auto_2")
            )

    if summary.still_waiting:
        lines.append("")
        lines.append(
            f"還有 {summary.still_waiting} 個問題在等你回答。"
            "加 --questions 看是哪些（會印出問題原文）。"
        )

    excluded = summary.excluded
    if excluded:
        lines.append("")
        lines.append(t("summary.excluded_projects"))
        for project in excluded:
            if project.unreadable:
                reason = project.unreadable
            elif project.chain is not None and project.chain.broken_at is not None:
                reason = (
                    f"雜湊鏈在第 {project.chain.broken_at} 筆斷掉"
                    f"（{project.chain.reason}）"
                )
            else:
                reason = t("summary.unverifiable")
            lines.append(f"  · {project.name}：{reason}")
        lines.append("")
        lines.append(
            t("summary.broken_chain_note")
        )

    return "\n".join(lines)
