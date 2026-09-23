from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from app.i18n import t
from app.core import prompt_markers


@dataclass(frozen=True)
class TaskMilestone:
    index: int
    title: str
    status: str = "pending"


@dataclass(frozen=True)
class DecisionLogEntry:
    """One thing that happened at a Decision Gate.

    `answered_by` is the field the whole audit claim rests on, and it is
    deliberately not defaulted anywhere it is written: 「Codex 自己批准」 and
    「人看過才放行」 are the two things a reader most needs told apart, and the
    same orchestrator method serves both paths (see `run_claude_with_user_answer`).
    A default would let a future third caller inherit the wrong one silently -
    producing a complete-looking audit record that is a lie.
    """

    run_id: str
    action: str
    question: str
    reason: str = ""
    source: str = ""
    answer: str = ""
    created_at: str = ""
    # "user" | "codex" | "" (not an answer at all - e.g. the ASK_USER entry
    # that only records that a question was raised).
    answered_by: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = asdict(self)
        if not payload["created_at"]:
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
        return payload


class LongTaskPlanner:
    """Create a small resumable checklist from a user task."""

    MAX_TITLE = 96

    def split_task(
        self,
        task: str,
        max_milestones: int = 6,
    ) -> list[TaskMilestone]:
        candidates = self._candidate_lines(task)
        if not candidates:
            candidates = [self._compact(task) or "Complete requested task"]

        milestones = [
            TaskMilestone(index=index, title=self._truncate(title))
            for index, title in enumerate(candidates[:max_milestones], start=1)
        ]
        if not milestones:
            milestones = [TaskMilestone(index=1, title="Complete requested task")]
        return milestones

    def render_plan(self, task: str) -> str:
        milestones = self.split_task(task)
        lines = [
            "# Long Task Milestones",
            "",
            "Claude should complete work in small resumable steps.",
            "After each risky question, AICC should route it through Codex Decision Gate.",
            "",
        ]
        for item in milestones:
            lines.append(f"{item.index}. [{item.status}] {item.title}")
        return "\n".join(lines) + "\n"

    def write_plan(self, project_path: Path, run_id: str, task: str) -> Path:
        path = project_path / ".aicc" / "runs" / run_id / "milestones.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "milestones": [
                asdict(item) for item in self.split_task(task)
            ],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def _candidate_lines(self, task: str) -> list[str]:
        result: list[str] = []
        for raw_line in task.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^[-*]\s+", "", line)
            line = re.sub(r"^\d+[.)、]\s*", "", line)
            line = line.strip("#：: ")
            if len(line) < 3:
                continue
            if line.casefold() in prompt_markers.BARE_FIELD_NAMES:
                continue
            result.append(line)
        return result

    @staticmethod
    def _compact(text: str) -> str:
        return " ".join((text or "").split()).strip()

    def _truncate(self, text: str) -> str:
        text = self._compact(text)
        if len(text) <= self.MAX_TITLE:
            return text
        return text[: self.MAX_TITLE - 1] + "…"


#: Fields that are hashed. Deliberately a fixed list rather than "everything
#: except the chain fields": adding a field later must not silently change what
#: every previous entry's hash covered, which would break every existing chain
#: and make the warning meaningless. A new field gets added here on purpose,
#: with the knowledge that entries written before it are verified without it.
HASHED_FIELDS = (
    "run_id",
    "action",
    "question",
    "reason",
    "source",
    "answer",
    "created_at",
    "answered_by",
)

#: What the first entry's `prev_hash` points at. A constant rather than "" so a
#: file whose first line was deleted is distinguishable from a file that simply
#: starts at the beginning.
GENESIS = "aicc-decision-log-v1"


def _digest(payload: dict, prev_hash: str) -> str:
    """The link. Content + the previous link, so editing one breaks the rest.

    Canonical form matters more than the algorithm: `sort_keys` and fixed
    separators mean the same content always hashes the same, no matter what
    order the dict happened to be built in.
    """
    body = json.dumps(
        {key: payload.get(key, "") for key in HASHED_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{prev_hash}\n{body}".encode("utf-8")).hexdigest()


class DecisionLogUnreadable(OSError):
    """紀錄檔在硬碟上，但這台機器**現在讀不到它**。

    這個型別存在的理由只有一個：**「讀不到」不可以跟「還沒有任何紀錄」共用
    同一個值。**2026-08-24 實測——一份 4 筆、鏈完好的 `decision-log.jsonl`
    被獨占鎖住的時候，`verify()` 回的是
    `ChainStatus(total=0, verified=0, unchained=0, broken_at=None)`，
    `ok` 是 `True`，稽核頁最上面那顆燈是薄荷綠的，寫著「全部通過驗證」。

    空清單是一個**會被下游當成「乾淨」**的值，所以讀取失敗不能折進去。
    往上拋還救活了一段死碼：`decision_summary.summarise_project` 早就寫好
    `except Exception → unreadable／trustworthy=False` 的保護，而它一直沒有
    機會執行，因為例外在這一層就被吃掉了。

    是 `OSError` 的子類別，所以既有那些「讀不到就算了」的呼叫端
    （`workspace_portability._decision_entry_count`、
    `project_dashboard._waiting_question`）照舊接得住——**這一刀只讓沉默
    變成有型別的沉默，不製造新的當機。**
    """


@dataclass(frozen=True)
class ChainStatus:
    """The result of checking a decision log's chain.

    `unchained` is not damage: entries written before 2026-08-09 have no hash
    fields at all. Counting them as broken would light the tamper warning on
    every existing project, and a warning that is always on is not read.

    `unreadable` 是 2026-08-24 補的第三種：**查過了沒斷**與**根本沒查成**
    原本共用 `broken_at is None`，而它們在畫面上差一個綠勾勾。
    """

    total: int
    verified: int
    unchained: int
    broken_at: int | None = None
    reason: str = ""
    #: 讀不到的時候，OS 講的那句話（含路徑）。空字串＝真的讀到了。
    unreadable: str = ""

    @property
    def ok(self) -> bool:
        """能不能拿這份紀錄當憑據。

        **讀不到也算不 ok。**只問 `broken_at is None` 的話，這一格要同時
        代表兩件事——「掃過了，每一筆都對得上」與「一筆都沒掃到」——
        而後者不是一個通過，是一次沒有發生的檢查。
        """
        return self.broken_at is None and not self.unreadable

    @property
    def nothing_to_verify(self) -> bool:
        """一筆紀錄都沒有：**沒有東西可以驗，不是「全部通過驗證」。**

        刻意**不**讓它把 `ok` 翻成 `False`。`ok` 有一個既有的讀者
        （`decision_summary.ProjectDecisions.trustworthy`），翻掉的話每一個
        還沒觸發過 Decision Gate 的專案——那是預設狀態，不是邊角——都會被
        列進「以下專案沒有列入計算」。**一個永遠都在的警告等於沒有警告。**

        所以它是另外一格，留給畫面決定要不要換一種說法。
        """
        return self.total == 0 and not self.unreadable


class DecisionLogStore:
    FILE_NAME = "decision-log.jsonl"

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path.expanduser().resolve()
        self.path = self.project_path / ".aicc" / "decisions" / self.FILE_NAME

    def append(self, entry: DecisionLogEntry) -> None:
        """Write one entry, linked to the one before it.

        Read-then-append rather than keeping the last hash in memory: a second
        AICC window may have written to this project's log since, and a chain
        built on a stale tail would be broken by our own hand.

        讀不到舊紀錄的時候會**拋出** `DecisionLogUnreadable`，不會接著寫。
        接著寫的後果比拋出去嚴重得多：`previous` 是空的，於是新的一筆會從
        `GENESIS` 重新起鏈接在一份讀不到的紀錄後面——**那份紀錄之後就再也
        驗不過了，而弄壞它的是我們自己。**
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        previous = self._raw_entries()
        payload = entry.to_dict()
        payload["seq"] = len(previous)
        # A fresh chain starts after anything unverifiable, which is what
        # `verify()` does when it meets a pre-chaining entry. Linking to an
        # entry that has no hash of its own would produce a `prev_hash` of ""
        # that verification could never match - the file would report itself
        # as tampered the moment a real project upgraded.
        last_hash = str(previous[-1].get("hash", "")) if previous else ""
        payload["prev_hash"] = last_hash or GENESIS
        payload["hash"] = _digest(payload, payload["prev_hash"])
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _raw_entries(self) -> list[dict]:
        """磁碟上那幾行。**「還沒有這個檔」跟「讀不到這個檔」不是同一件事。**

        原本這裡有**兩處**都會回一個空清單，而兩處都是 fail open：

        * `self.path.is_file()` —— `os.path.isfile` 自己吞掉 OSError 回 False，
          所以權限錯誤走的是「這個專案還沒有紀錄」那一條。
        * `except OSError: return []` —— 檔案被別的程式獨占、磁碟掉線、
          目錄權限被改，三種都會變成同一個空清單。

        兩處合成一句話送到畫面上：**「全部通過驗證，0 / 0 筆已驗證」**。

        現在只有 `FileNotFoundError` 回空清單（那是真的還沒有），其餘 OS 層
        的失敗一律往上拋。`is_file()` 也一起拿掉了——先問再讀本來就有一個
        競態，而**問的那一步跟讀的那一步用的是兩套錯誤處理**。

        （收窄一句：非 UTF-8 的檔丟的是 `UnicodeDecodeError`，那是
        `ValueError` 不是 `OSError`，本來就會往上炸，不在這一刀的範圍。）
        """
        out: list[dict] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise DecisionLogUnreadable(f"{self.path}：{exc}") from exc
        for line in lines:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                out.append(raw)
        return out

    def verify(self) -> ChainStatus:
        """Recompute the chain and report the first link that does not hold.

        **What a hit means: someone edited, removed or inserted a line in the
        middle after it was written.**

        Three things it does not mean, all stated in the export too:

        * Not that the log is honest when it passes - anyone able to edit the
          file can recompute the whole chain.
        * **Not that the log is complete.** Nothing records how many entries
          there should be, so deleting entries off the *end* leaves no trace.
          Fixing that needs an external anchor (a signed count, or a timestamp
          service), which is the level-2/3 decision deferred in ROADMAP P8.
        * Not that the content was true when written.

        The two checks below are deliberately redundant: because `prev_hash`
        is part of the digest, either one alone would catch a deleted middle
        line. They are kept apart for the diagnosis, not the detection - 「中間
        少了一筆」 and 「這一筆被改過」 send a reader looking in different places,
        and the tests pin the wording for that reason.

        讀不到的時候**不回一個看起來像「查過了」的結果**：`unreadable` 有值、
        `ok` 是 False。這裡接住例外而不是往上拋，是因為稽核頁要拿這個結果去
        畫那一格——而 `list_entries()` 那一條路照樣會拋，讓
        `decision_summary` 的保護接得到。
        """
        try:
            raw = self._raw_entries()
        except DecisionLogUnreadable as exc:
            return ChainStatus(
                total=0,
                verified=0,
                unchained=0,
                reason=t("file.unreadable"),
                unreadable=str(exc),
            )
        prev = GENESIS
        verified = unchained = 0

        for index, payload in enumerate(raw):
            if "hash" not in payload:
                # Written before chaining existed. It cannot be verified, and
                # it also cannot vouch for what follows - so the next chained
                # entry starts a fresh chain from GENESIS rather than being
                # reported as broken.
                unchained += 1
                prev = GENESIS
                continue
            if str(payload.get("prev_hash", "")) != prev:
                return ChainStatus(
                    total=len(raw),
                    verified=verified,
                    unchained=unchained,
                    broken_at=index,
                    reason=t("chain.broken_link"),
                )
            if _digest(payload, prev) != str(payload.get("hash", "")):
                return ChainStatus(
                    total=len(raw),
                    verified=verified,
                    unchained=unchained,
                    broken_at=index,
                    reason=t("chain.digest_mismatch"),
                )
            verified += 1
            prev = str(payload["hash"])

        return ChainStatus(
            total=len(raw), verified=verified, unchained=unchained
        )

    def unanswered(self) -> list[DecisionLogEntry]:
        """Questions raised and never replied to, oldest first.

        The dashboard needs this to say **what** a waiting project is waiting
        on. 「等待決策」 on a card is the same non-answer 「錯誤」 was: the
        machine knows the question and makes the person go and look it up.

        Pairing rule, and it has to be exactly the audit page's or the two
        screens will disagree about whether something was answered: an
        `ANSWERED` clears the **most recent** still-open `ASK_USER` with the
        same `run_id` and the same question text. Most recent rather than
        first, because the same question can be raised twice in one run and
        the second answer belongs to the second asking.

        `CONTINUE` does not clear anything - that is Codex approving its own
        question, which the log records precisely so it is not mistaken for a
        person having replied.

        There are now two implementations of that rule (the other renders the
        audit page, which needs a merged shape rather than a filtered list).
        `test_decision_audit` pins them to the same verdict, because two rules
        for 「回答了沒」 disagreeing is the one thing an audit trail cannot do.
        """
        open_questions: list[DecisionLogEntry] = []
        for entry in self.list_entries():
            if entry.action == "ASK_USER":
                open_questions.append(entry)
                continue
            if entry.action != "ANSWERED":
                continue
            for index in range(len(open_questions) - 1, -1, -1):
                candidate = open_questions[index]
                if (
                    candidate.run_id == entry.run_id
                    and candidate.question == entry.question
                ):
                    del open_questions[index]
                    break
        return open_questions

    def list_entries(self) -> list[DecisionLogEntry]:
        entries: list[DecisionLogEntry] = []
        for raw in self._raw_entries():
            entries.append(
                DecisionLogEntry(
                    run_id=str(raw.get("run_id", "")),
                    action=str(raw.get("action", "")),
                    question=str(raw.get("question", "")),
                    reason=str(raw.get("reason", "")),
                    source=str(raw.get("source", "")),
                    answer=str(raw.get("answer", "")),
                    answered_by=str(raw.get("answered_by", "")),
                    created_at=str(raw.get("created_at", "")),
                )
            )
        return entries
