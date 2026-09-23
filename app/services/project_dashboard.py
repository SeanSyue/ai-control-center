from __future__ import annotations

from app.i18n import t

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from app.services.project_registry import ProjectEntry
from app.core import outcomes
from app.core.lineup import shown_name
from app.services.timeline_store import RunInfo, TimelineStore


@dataclass(frozen=True)
class ProjectDashboardItem:
    name: str
    path: str
    # **存 key，不存字。**2026-08-24 之前這裡存的是「待命」「錯誤」那幾個
    # 中文，而它同時被五個地方拿去**比對**（`item.status == "待命"`、
    # `lane_for(status)`、要不要去查失敗在哪一格⋯⋯）。
    #
    # 那是【拿中文當查詢的鍵】的第五處，而且是最貴的一處：畫面上要看到
    # 什麼字，跟程式要怎麼分支，被綁成同一個值。英文版上線那天，任何一個
    # 沒改到的比對會**安靜地永遠不成立**——不是報錯，是那條泳道空掉、
    # 那個統計永遠是 0。
    status: str
    last_task: str
    last_updated: str
    waiting_count: int
    completed_today: int
    failed_today: int
    is_running: bool
    can_start: bool
    is_queued: bool = False
    stale_lock: bool = False
    # Which seat the most recent run died on, e.g. 「審查」, or "" when the run
    # did not fail (ROADMAP P4「對失敗任務提供 drill-down」). 「錯誤」 alone
    # sends a person to the log to find out something the machine already
    # recorded.
    failed_step: str = ""
    last_run_id: str = ""
    # What a waiting project is waiting on, or "". 「等待決策」 on its own is
    # the same non-answer 「錯誤」 was.
    waiting_question: str = ""
    # 上一次執行**宣稱自己還在跑、而沒有東西替它背書**的時候，它停在哪：
    # `""` / `"never_started"` / `"stopped_midway"`。**存 key 不存字**，
    # 理由同 `status`。
    #
    # 空字串的意思是「沒有這種事」，不是「不知道」——只有
    # `status == "dash.state.interrupted"` 的時候它才會有值。
    #
    # **哪一次**是 `last_run_id`，不另外存一份：兩個欄位裝同一個答案，
    # 就會有一天它們不一樣。
    interrupted_reason: str = ""
    # 使用者交辦裡被護欄擋下來的那幾句，`(他寫的原話, 擋它的那條規則)`。
    #
    # 2026-08-27 拍板（❓15）：「護欄贏，**而且畫面要明說哪一句被擋下來**」。
    # 兩邊都是**驗證過的原話**——見
    # `orchestrator_parsing.blocked_instructions()`，那裡驗不過的整組就丟了。
    blocked_instructions: tuple[tuple[str, str], ...] = ()

    @property
    def status_label(self) -> str:
        """畫面上那個字。**要顯示的人叫這個，要比對的人用 `status`。**"""
        return t(self.status)


@dataclass(frozen=True)
class DashboardSummary:
    total_projects: int
    idle_projects: int
    running_projects: int
    waiting_projects: int
    completed_today: int
    failed_today: int
    queued_projects: int = 0
    stale_locks: int = 0
    concurrency_limit: int = 1
    can_start_more: bool = True


@dataclass(frozen=True)
class StartBlock:
    """One project, and the reason it will not auto-start right now.

    `reason` is a stable key for the page to style on; `detail` is the
    sentence a person reads. Both come from the scheduler's own pass - see
    `ProjectDashboardService.start_readiness`.
    """

    name: str
    path: str
    reason: str
    detail: str


@dataclass(frozen=True)
class StartReadiness:
    candidates: list[ProjectEntry]
    blocked: list[StartBlock]


@dataclass(frozen=True)
class DashboardQueueState:
    queued_paths: tuple[str, ...] = ()
    paused: bool = False
    concurrency_limit: int = 1


def _statuses_in(*groups: str) -> frozenset[str]:
    """哪幾種收場屬於這幾組。**算出來的，不是抄一份的。**

    抄一份的下場 2026-08-23 量到了：這裡的 `WAITING_STATUSES` 只寫了
    `ASK_USER`，而需求確認停下來問使用者的時候吐的是 `INTAKE_ASKING`——
    於是**AI 正在等使用者回答，首頁卻說那個專案「閒置」**，還是灰點。

    一個看不到的停下來等於沒有停下來，而那一次它發生在首頁。
    """
    wanted = set(groups)
    found = set()
    for status in outcomes.known_statuses():
        probe = {"workflow_status": status}
        if status == outcomes.UNDECIDABLE_STATUS:
            # 它一種橫跨四組。首頁只需要知道「要不要人動手」，而六種判定
            # 沒有一種是「不用」——所以哪一組都算得上「要你處理」。
            probe["decision_verdict"] = "MISSING"
        if outcomes.describe(probe).group in wanted:
            found.add(status)
    if outcomes.GROUP_DONE not in wanted and outcomes.GROUP_ASK not in wanted:
        found.add(outcomes.UNDECIDABLE_STATUS)
    return frozenset(found)


class ProjectDashboardService:
    # `RUNNING` 不是收場，是時間軸自己的執行中狀態，所以它不從 outcomes 來。
    #
    # **它是一句宣稱，不是一個事實。**`create_run()` 在開跑的那一毫秒把它寫
    # 進 `run.json`，而把它改掉的只有 `finalize_run()`——也就是說，**寫下它
    # 的那個行程死掉的時候，沒有任何人會回來改它**。
    #
    # 2026-08-27 量到的：這個 repo 自己的 `.aicc/runs/` 七筆裡有六筆卡在
    # `RUNNING`，最舊的一筆從 2026-08-21 卡到那天，六天。而首頁直接信它，
    # 於是它說那個專案「執行中」——**而且佇列因此永遠不排它**
    #（`is_running` 為真 → `can_start=False`、`can_start_more=False`）。
    RUNNING_STATUSES = {"RUNNING"}
    WAITING_STATUSES = _statuses_in(outcomes.GROUP_ASK)
    COMPLETED_STATUSES = _statuses_in(outcomes.GROUP_DONE)
    # 「要你處理」：跑完了，沒人在等你回話，但有一件事在等你動手。
    # 這幾種以前全部掉進 `待命`——首頁上跟「從來沒跑過」長得一模一樣。
    NEEDS_YOU_STATUSES = _statuses_in(
        outcomes.GROUP_DECIDE,
        outcomes.GROUP_SETTING,
        outcomes.GROUP_RERUN,
        outcomes.GROUP_FIX,
    )
    # 「今日錯誤」數的是真的沒跑起來的那幾種（`style == "error"`）。
    # 「做了一半」與「少一項設定」不算錯誤——把它們數進去，使用者會去修一個
    # 沒壞的東西。
    ERROR_STATUSES = frozenset(
        status
        for status in outcomes.known_statuses()
        if status != outcomes.UNDECIDABLE_STATUS
        and outcomes.describe({"workflow_status": status}).style == "error"
    )
    STALE_LOCK_MINUTES = 240

    def __init__(self) -> None:
        self.queue_store = DashboardQueueStore()

    def build(
        self,
        projects: list[ProjectEntry],
    ) -> tuple[DashboardSummary, list[ProjectDashboardItem]]:
        queue_state = self.queue_store.load()
        queued = set(queue_state.queued_paths)
        items = [
            self._build_item(project, queued_paths=queued)
            for project in projects
        ]
        running = sum(item.is_running for item in items)
        summary = DashboardSummary(
            total_projects=len(items),
            idle_projects=sum(item.status == "dash.state.idle" for item in items),
            running_projects=sum(item.status == "run.status.running" for item in items),
            waiting_projects=sum(item.status == "outcome.group.ask" for item in items),
            completed_today=sum(item.completed_today for item in items),
            failed_today=sum(item.failed_today for item in items),
            queued_projects=sum(item.is_queued for item in items),
            stale_locks=sum(item.stale_lock for item in items),
            concurrency_limit=queue_state.concurrency_limit,
            can_start_more=(
                not queue_state.paused
                and running < queue_state.concurrency_limit
            ),
        )
        return summary, items

    def _build_item(
        self,
        project: ProjectEntry,
        queued_paths: set[str],
    ) -> ProjectDashboardItem:
        path = project.resolved_path
        if not path.is_dir():
            return ProjectDashboardItem(
                name=project.name,
                path=str(path),
                status="dash.state.error",
                last_task=t("project.folder_missing"),
                last_updated="",
                waiting_count=0,
                completed_today=0,
                failed_today=1,
                is_running=False,
                can_start=False,
                is_queued=False,
                stale_lock=False,
            )

        runs = TimelineStore(path).list_runs()
        latest = runs[0] if runs else None
        today = date.today()
        completed_today = 0
        failed_today = 0
        waiting_count = 0

        for run in runs:
            run_date = self._parse_date(run.updated_at or run.created_at)
            if run.status in self.WAITING_STATUSES:
                waiting_count += 1
            if run_date == today and run.status in self.COMPLETED_STATUSES:
                completed_today += 1
            if run_date == today and run.status in self.ERROR_STATUSES:
                failed_today += 1

        status = "dash.state.idle"
        is_running = False
        interrupted_reason = ""
        if latest is not None:
            if latest.status in self.RUNNING_STATUSES:
                # **宣稱要有人背書才算數。**背書的那個東西是鎖——見
                # `is_really_running()`。沒有背書的時候不是「執行中」，
                # 也不是「待命」，是**上一次跑到一半沒收尾**。
                if self.is_really_running(path, latest.run_id):
                    status = "run.status.running"
                    is_running = True
                else:
                    status = "dash.state.interrupted"
                    interrupted_reason = self._how_far_it_got(path, latest)
            elif latest.status in self.WAITING_STATUSES:
                status = "outcome.group.ask"
            elif latest.status in self.COMPLETED_STATUSES:
                status = "dash.state.done"
            elif latest.status in self.ERROR_STATUSES:
                status = "dash.state.error"
            elif latest.status in self.NEEDS_YOU_STATUSES:
                status = "dash.needs_you"
            else:
                # **這條 elif 鏈以前沒有 else。**沒有 else 的意思不是
                # 「不會發生」，是「發生了就沿用上面那個 `dash.state.idle`」
                # ——而 `dash.state.idle` 在畫面上是灰點、寫「待命」、算進
                # 統計列那句「N 個閒置」、排在出廠就收起來的那條泳道裡。
                # **跟一個從來沒跑過的專案一個像素都不差。**
                #
                # 這不是假設。`main_window.dismiss_pending_question` 在使用者
                # 按下「結束等待」時寫 `workflow_status="CANCELLED"`，
                # `finalize_run()` 把它原樣存進 `run.json` 的 `status`——
                # 而 `CANCELLED` 不在 `outcomes` 那張表裡，所以上面五個
                # `in` 一個都不成立。**使用者親手結束的那一次執行，首頁說
                # 它從來沒跑過。**
                #
                # 為什麼倒向「要你處理」而不是丟例外：這條路踩得到的值已經
                # 躺在使用者硬碟上的 `run.json` 裡了，丟例外是讓首頁為了一份
                # 舊紀錄整頁掛掉。倒向「要你處理」是**照實講**——引擎認不得
                # 這次收場，所以只有人看得出來發生什麼事；它排在 gate 那條
                # 出廠展開的泳道，而且**不算進「閒置」**。
                #
                # 為什麼不另外開一個 key（例如「我不認得這個」）：那要動
                # `app/i18n.py` 與 `app/ui/dashboard_web_panel.py` 的
                # `_LANE_BY_KEY`，兩份都不在這一輪的範圍裡。**寧可先讓它
                # 不說謊，也不要為了一句更精準的話跨進別人正在改的檔案。**
                # 那半件事寫在 `docs/WHY.md` 同一則裡。
                status = "dash.needs_you"

        task_exists = (path / ".aicc" / "task.md").is_file()
        stale_lock = self.is_stale_lock(path)
        is_queued = str(path) in queued_paths
        return ProjectDashboardItem(
            name=project.name,
            path=str(path),
            status=status,
            last_task=(latest.task_preview if latest else t("run.status.not_started")),
            last_updated=(latest.updated_at if latest else ""),
            waiting_count=waiting_count,
            completed_today=completed_today,
            failed_today=failed_today,
            is_running=is_running,
            can_start=task_exists and not is_running and not is_queued,
            is_queued=is_queued,
            stale_lock=stale_lock,
            failed_step=(
                self._failed_step(path, latest.run_id)
                if latest is not None and status == "dash.state.error"
                else ""
            ),
            last_run_id=(latest.run_id if latest is not None else ""),
            waiting_question=(
                self._waiting_question(path) if status == "outcome.group.ask" else ""
            ),
            interrupted_reason=interrupted_reason,
            blocked_instructions=(
                self._blocked_instructions(path, latest.run_id)
                if latest is not None
                else ()
            ),
        )

    @staticmethod
    def _blocked_instructions(
        project_path: Path, run_id: str
    ) -> tuple[tuple[str, str], ...]:
        """上一次執行裡，使用者哪幾句話被護欄擋下來了。

        讀的是引擎當時**驗證過之後寫下來**的那一份
        （`.aicc/runs/<run_id>/blocked-instructions.json`），不是在這裡重算
        一次——重算的那一份跟當時算的只會一致到其中一邊改動為止，然後畫面
        會很有自信地講一件當時沒有發生的事。

        那個檔案不在的意思是「這一次沒有這種事」，不是錯誤：絕大多數執行
        都不會有它。讀不出來也回空的——這是一列附註，不值得讓首頁掛掉。
        """
        path = project_path / ".aicc" / "runs" / run_id / "blocked-instructions.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            return ()
        if not isinstance(rows, list):
            return ()
        out: list[tuple[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            quote = str(row.get("quote", "")).strip()
            rule = str(row.get("rule_text", "")).strip()
            if quote and rule:
                out.append((quote, rule))
        return tuple(out)

    WAITING_QUESTION_MAX = 60

    @classmethod
    def _waiting_question(cls, project_path: Path) -> str:
        """The question this project is stopped on, trimmed to one line.

        Only read for projects already in 「等待決策」, so a healthy list costs
        no extra reads - same rule as `_failed_step`.

        Trimmed rather than wrapped: this goes on a card next to four other
        rows, and a paragraph there pushes everything else off. The full text
        is on the decision banner and in the audit page, both of which have
        room for it.
        """
        from app.services.long_task import DecisionLogStore

        try:
            pending = DecisionLogStore(project_path).unanswered()
        except Exception:
            return ""
        if not pending:
            return ""
        # The oldest still-open one: if two are outstanding, the one that has
        # been blocking longest is the one to name.
        question = " ".join(pending[0].question.split())
        if len(question) > cls.WAITING_QUESTION_MAX:
            return question[: cls.WAITING_QUESTION_MAX - 1] + "…"
        return question

    @staticmethod
    def _failed_step(project_path: Path, run_id: str) -> str:
        """Which seat the run died on, in words, or "" if it cannot be said.

        ROADMAP P4「對失敗任務提供 drill-down」. 「錯誤」 on its own sends a
        person to the log to look up something the machine wrote down at the
        time - the step log knows the seat, the step and the output.

        Only read for runs that already ended in an error, so the common case
        (a healthy project list) does no extra file reads.

        Runs from before the step log existed have nothing to read and get "",
        which the card then omits. Empty is 「不知道」 here, not 「沒有失敗」 -
        `status` is what says whether it failed.
        """
        from app.services.step_log import StepLog

        try:
            failure = StepLog(project_path).last_failure(run_id)
        except Exception:
            return ""
        if failure is None:
            return ""
        from app.core.orchestrator import Orchestrator

        # 直接印**那一格的名字**。
        #
        # 2026-08-25 之前這裡拿名字去查 `SEQUENCE_JOBS`，想把它翻成一個工作
        # 名稱——那張表的鍵是出廠名字，所以使用者自訂的一律查不到、退回原值。
        # 也就是說這段翻譯**只對出廠陣容有用**，而它還寫死了 "development"，
        # 專案跑的是計畫序列時翻出來的字是錯的。
        #
        # 而且卡片上要回答的是「**哪一格**出事」，那就是它的名字——
        # 使用者取的名字比一個推算出來的工作名稱更指得到人。
        return f"{shown_name(failure.role)}（{failure.event_type}）"

    @staticmethod
    def lock_path(project_path: Path) -> Path:
        return project_path / ".aicc" / "runtime" / "workflow.lock"

    @classmethod
    def acquire_lock(cls, project_path: Path, run_id: str) -> bool:
        lock_path = cls.lock_path(project_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "pid": os.getpid(),
            "created_at": datetime.now().astimezone().isoformat(),
        }
        # Atomic create-exclusive instead of exists()-then-write: the old
        # check-then-act pair had a small TOCTOU window where two callers
        # (e.g. two AICC windows/processes racing to start the same
        # project) could both pass the exists() check before either wrote
        # the file. O_CREAT|O_EXCL makes the OS itself the single point of
        # truth for "did I win the race" - only one caller's os.open can
        # ever succeed for a given path.
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, OSError):
            return False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, indent=2))
        except OSError:
            # We created the lock file but failed to write its contents -
            # remove it rather than leave the project stuck behind an
            # unreadable lock (is_stale_lock treats unparsable payloads as
            # stale anyway, but don't rely on that - clean up explicitly).
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    @classmethod
    def release_lock(cls, project_path: Path) -> None:
        try:
            cls.lock_path(project_path).unlink(missing_ok=True)
        except OSError:
            return

    @classmethod
    def lock_holder(cls, project_path: Path) -> str:
        """哪一次執行**正握著**這個專案的鎖，沒有人握著就回 ""。

        鎖是這個專案本來就用來擋重複啟動的東西：`acquire_lock()` 建立它、
        `release_lock()` 刪掉它，而它裡面寫著**是哪一次執行**建立的。

        所以「還在不在跑」這個問題，鎖答得出來而 `run.json` 答不出來——
        `run.json` 的 `RUNNING` 是開跑那一刻寫下去就沒有人回來改的一句話。

        讀不到、解析不出來、裡面沒有 `run_id`，一律回 ""：**「不知道是誰」
        不准算成「就是你要問的那一次」**。
        """
        lock_path = cls.lock_path(project_path)
        if not lock_path.is_file():
            return ""
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("run_id", "")).strip()

    @classmethod
    def is_really_running(cls, project_path: Path, run_id: str) -> bool:
        """這一次執行**真的**還在跑嗎。

        兩件事都要成立：**鎖在**，而且**鎖上寫的就是這一次**。鎖已經過期的
        時候不算——「過期」用的是這個專案本來就有的那條定義
        （`is_stale_lock`），這裡**不另外訂一個時間門檻**：兩套門檻會慢慢
        分岔，分岔之後畫面說的跟排程器做的就不是同一件事了。

        ## 猜錯的時候往哪一邊倒

        往「說它停了」倒：使用者按下開始執行 → `acquire_lock()` 看到鎖還在，
        當場回 `False`；`start_readiness()` 也會因為 `lock_path().exists()`
        把它擋成 `locked`。**兩個流程不可能同時跑起來**，因為擋住那件事的
        本來就是鎖，不是這個判斷。代價是使用者多按一次、被擋一次。

        往「說它還在跑」倒（**今天的行為**）：那個專案永遠不會被排程
        （`can_start=False`、`can_start_more=False`），而畫面上它看起來
        一切正常。沒有任何東西會講，而硬碟上那一筆已經這樣躺了六天。

        所以保守的那一邊是**不敢說它在跑**。而這不是「猜」：沒有鎖就是
        **沒有證據**，這裡要的是證據，不是預設值。
        """
        if not run_id:
            return False
        if cls.lock_holder(project_path) != run_id:
            return False
        return not cls.is_stale_lock(project_path)

    # 那一次中斷停在哪。**存 key 不存字**，畫面上那句話由 i18n 決定。
    INTERRUPTED_NEVER_STARTED = "never_started"
    INTERRUPTED_STOPPED_MIDWAY = "stopped_midway"

    @classmethod
    def _how_far_it_got(cls, project_path: Path, run: "RunInfo") -> str:
        """那一次中斷的執行，是「根本沒開始」還是「跑到一半停住」。

        分這兩種是因為**下一步不一樣**，而畫面上要看得出來：

        * 沒開始 → 直接重跑，沒有東西會被浪費。
        * 跑到一半 → `.aicc/runs/<run_id>/` 裡有東西可以看，先看一眼再決定。

        **不看時間，看有沒有留下痕跡。**判準是這一次執行有沒有真的寫出
        `timeline.jsonl` 或 `steps.jsonl`——那兩份是**每一個步驟跑過就會寫**
        的東西，空的或不存在就是一個步驟都沒跑過。這個 repo 硬碟上量得到
        差別：8/26 那兩筆連 `timeline.jsonl` 都沒有，8/25 那兩筆有。

        ## `created_at` 跟 `updated_at` 差多少**不能當證據**

        一開始這裡還看了「這筆紀錄被建立之後有沒有再被更新過」。
        2026-08-28 拿硬碟上的資料一量就知道那是錯的：8/26 那兩筆
        `updated_at` 比 `created_at` 晚 **1.5 毫秒**——也就是說，一次
        **什麼都沒跑**的執行照樣「被更新過」。

        要救它只能加一個「差超過 N 秒才算」的門檻，而那是**在猜**：
        沒有人知道 N 該是多少，而猜錯的時候它會很有自信地說錯話。
        痕跡不用猜——步驟跑過就有，沒跑過就沒有。
        """
        run_dir = project_path / ".aicc" / "runs" / run.run_id
        for name in ("timeline.jsonl", "steps.jsonl"):
            try:
                if (run_dir / name).stat().st_size > 0:
                    return cls.INTERRUPTED_STOPPED_MIDWAY
            except FileNotFoundError:
                continue
            except OSError:
                # 讀不到不等於沒有。**倒向「有東西」那一邊**：叫人去看一個
                # 空資料夾很便宜，叫人直接重跑掉一份還躺在那裡的紀錄不便宜。
                return cls.INTERRUPTED_STOPPED_MIDWAY
        return cls.INTERRUPTED_NEVER_STARTED

    @classmethod
    def is_stale_lock(cls, project_path: Path) -> bool:
        lock_path = cls.lock_path(project_path)
        if not lock_path.is_file():
            return False
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            created_at = datetime.fromisoformat(str(payload.get("created_at", "")))
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            return True
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc).astimezone()
        age = datetime.now(timezone.utc).astimezone() - created_at
        return age.total_seconds() > cls.STALE_LOCK_MINUTES * 60

    def start_readiness(
        self,
        projects: list[ProjectEntry],
    ) -> "StartReadiness":
        """Which queued projects can auto-start, and why the others cannot.

        ROADMAP P4「在 UI 顯示為什麼這個專案現在不能啟動」. The scheduler used
        to answer only the first half: six separate `continue`s, each one a
        real reason a person would want told, all discarded. From the screen
        the project simply sat in the queue.

        **The reasons are produced by the same pass that picks the
        candidates**, not by a second function that re-checks the same
        conditions. A separate 「為什麼不能跑」 would agree with the scheduler
        only until one of them changed, and then the screen would confidently
        explain a reason that is not the one actually holding the project
        back - which is worse than saying nothing.

        One reason per project: **the one stopping it right now**, in the
        order the scheduler meets them. Fixing it can reveal the next one, and
        that is honest - 「為什麼現在不能啟動」 has one answer at a time.

        Does not evaluate per-run settings such as high-usage confirmation -
        callers apply that themselves and move on to the next candidate.
        """
        queue_state = self.queue_store.load()
        queued = set(queue_state.queued_paths)
        by_path = {str(project.resolved_path): project for project in projects}

        def blocked(project: ProjectEntry, reason: str, detail: str) -> StartBlock:
            return StartBlock(
                name=project.name,
                path=str(project.resolved_path),
                reason=reason,
                detail=detail,
            )

        # Projects nobody asked to run. Reported rather than omitted: 「它不在
        # 佇列裡」 is the answer to 「為什麼它沒動」 far more often than any of
        # the failure reasons below, and it is the one with a one-click fix.
        blocks = [
            blocked(project, "not_queued", t("queue.why.not_queued"))
            for project in projects
            if str(project.resolved_path) not in queued
        ]

        if not queue_state.queued_paths:
            return StartReadiness(candidates=[], blocked=blocks)

        if queue_state.paused:
            blocks.extend(
                blocked(
                    by_path[raw],
                    "queue_paused",
                    t("queue.why.paused"),
                )
                for raw in queue_state.queued_paths
                if raw in by_path
            )
            return StartReadiness(candidates=[], blocked=blocks)

        running = sum(
            self._build_item(project, queued_paths=queued).is_running
            for project in projects
        )
        if running >= queue_state.concurrency_limit:
            blocks.extend(
                blocked(
                    by_path[raw],
                    "at_limit",
                    f"同時執行上限是 {queue_state.concurrency_limit}，"
                    f"目前已經有 {running} 個在跑。",
                )
                for raw in queue_state.queued_paths
                if raw in by_path
            )
            return StartReadiness(candidates=[], blocked=blocks)

        candidates: list[ProjectEntry] = []
        for raw_path in queue_state.queued_paths:
            project = by_path.get(raw_path)
            if project is None:
                # In the queue file but not in the project list. Nothing to
                # name, so nothing to report - the entry refers to a project
                # this app no longer knows about.
                continue
            path = project.resolved_path
            if not path.is_dir():
                blocks.append(
                    blocked(project, "missing", t("queue.why.folder_missing"))
                )
                continue
            if not (path / ".aicc" / "task.md").is_file():
                blocks.append(
                    blocked(
                        project,
                        "no_task",
                        t("queue.why.no_task"),
                    )
                )
                continue
            # Stale and live locks are told apart because the fixes differ:
            # one is 「清掉就好」, the other is 「等它跑完」. Reported as one
            # reason before this, which made the harmless case look alarming
            # and the real case look dismissible.
            if self.is_stale_lock(path):
                blocks.append(
                    blocked(
                        project,
                        "stale_lock",
                        t("queue.why.stale_lock"),
                    )
                )
                continue
            if self.lock_path(path).exists():
                blocks.append(
                    blocked(project, "locked", t("queue.why.already_running"))
                )
                continue
            candidates.append(project)
        return StartReadiness(candidates=candidates, blocked=blocks)

    def queued_start_candidates(
        self,
        projects: list[ProjectEntry],
    ) -> list[ProjectEntry]:
        """Queued projects that are structurally safe to auto-start now.

        Returned in queue order. Kept as the scheduler's entry point so its
        call sites read the same as before; the logic lives in
        `start_readiness`, which produces the reasons at the same time.
        """
        return self.start_readiness(projects).candidates

    @staticmethod
    def _parse_date(raw: str) -> date | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw).date()
        except ValueError:
            return None


class DashboardQueueStore:
    FILE_NAME = "dashboard-queue.json"

    def __init__(self, path: Path | None = None) -> None:
        if path is not None:
            self.path = path
        else:
            appdata = os.environ.get("APPDATA")
            root = Path(appdata) if appdata else Path.home() / ".aicontrolcenter"
            self.path = root / "AIControlCenter" / self.FILE_NAME

    def load(self) -> DashboardQueueState:
        if not self.path.is_file():
            return DashboardQueueState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return DashboardQueueState()
        if not isinstance(raw, dict):
            return DashboardQueueState()
        queued = tuple(
            str(item)
            for item in raw.get("queued_paths", [])
            if str(item).strip()
        )
        limit = int(raw.get("concurrency_limit", 1) or 1)
        return DashboardQueueState(
            queued_paths=queued,
            paused=bool(raw.get("paused", False)),
            concurrency_limit=max(1, min(limit, 5)),
        )

    def save(self, state: DashboardQueueState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "queued_paths": list(dict.fromkeys(state.queued_paths)),
            "paused": bool(state.paused),
            "concurrency_limit": max(1, min(int(state.concurrency_limit), 5)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Atomic write (temp file + rename), matching ProjectRegistry.save().
        # dashboard-queue.json is a single global file shared by every AICC
        # window on the machine (Stage 0 of true multi-project concurrency
        # is "N independent windows"), so two windows calling
        # enqueue/remove/set_paused/set_concurrency_limit around the same
        # moment must not be able to torn-write/corrupt it into the
        # loader's silent-default fallback.
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def enqueue(self, project_path: Path) -> DashboardQueueState:
        state = self.load()
        path = str(project_path.expanduser().resolve())
        queued = tuple(dict.fromkeys((*state.queued_paths, path)))
        next_state = DashboardQueueState(
            queued_paths=queued,
            paused=state.paused,
            concurrency_limit=state.concurrency_limit,
        )
        self.save(next_state)
        return next_state

    def remove(self, project_path: Path) -> DashboardQueueState:
        state = self.load()
        path = str(project_path.expanduser().resolve())
        next_state = DashboardQueueState(
            queued_paths=tuple(item for item in state.queued_paths if item != path),
            paused=state.paused,
            concurrency_limit=state.concurrency_limit,
        )
        self.save(next_state)
        return next_state

    def set_paused(self, paused: bool) -> DashboardQueueState:
        state = self.load()
        next_state = DashboardQueueState(
            queued_paths=state.queued_paths,
            paused=bool(paused),
            concurrency_limit=state.concurrency_limit,
        )
        self.save(next_state)
        return next_state

    def set_concurrency_limit(self, limit: int) -> DashboardQueueState:
        state = self.load()
        next_state = DashboardQueueState(
            queued_paths=state.queued_paths,
            paused=state.paused,
            concurrency_limit=max(1, min(int(limit), 5)),
        )
        self.save(next_state)
        return next_state
