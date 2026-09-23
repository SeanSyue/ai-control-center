from __future__ import annotations

from app.i18n import t

import json
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.adapters.cli_runner import CliResult, CliRunner
from app.adapters.no_console import NO_WINDOW
from app.core import orchestrator_parsing, prompt_builder
from app.core.lineup import (
    _DUTY_BY_LEGACY_ROLE,
    CLASSIC_ROLES,
    DUTY_APPROVE,
    DUTY_PLAN,
    DUTY_REVIEW,
    DUTY_WORK,
    ROLE_AUTHOR,
    ROLE_FINISHER,
    ROLE_PLANNER,
    shown_name,
    ROLE_REVIEWER,
    AgentSlot,
    Lineup,
    criteria_seat,
    duty_of,
)
from app.services.ai_settings import AIExecutionSettings
from app.services.context_ledger import ContextLedger
from app.services.long_task import (
    DecisionLogEntry,
    DecisionLogStore,
    LongTaskPlanner,
)
from app.services.project_registry import ProjectRegistry
from app.services.project_verification import (
    TESTS_NOT_DECLARED,
    VerificationNotDeclared,
    VerificationOutcome,
    VerifyCommandLookup,
    combined_output,
    never_started_problem,
    read_verify_command,
    timed_out_verdict,
)
from app.services.security_scan import (
    WITHHOLD_RUNTIME,
    WITHHOLD_SECRET,
    withhold_reason,
)
from app.services.session_registry import SessionRegistry
from app.services.vendor_session_index import ClaudeSidebarIndex
from app.services.step_log import StepLog, StepRecord
from app.services.timeline_store import TimelineStore
from app.services.project_knowledge import (
    MemoryTruncatedError,
    ProjectKnowledgeStore,
)


# 提示詞裡「這一段是啟動脈絡」的記號。**測試要 import 這個常數，不要自己
# 抄一份字串**——抄一份就會漂開，而漂開的那天測試會說「沒有包進去」，
# 事實是包了，只是字換了。
#
# 刻意不是第一行：第一行是給廠商的對話清單當標題用的（見
# `_prompt_with_bootstrap`）。
# 搬去 `app/core/prompt_markers.py` 了（2026-08-24）：它是**比對用的字**。
from app.core.prompt_markers import BOOTSTRAP_MARKER  # noqa: E402


# 「這一輪做出來的東西」**不算誰**。
#
# 這份清單有兩個讀者：`get_git_diff()` 把它翻成 git 的 pathspec，
# `get_changed_files()` 拿它比對 `git status` 的每一行。兩邊各抄一份的下場
# 這個檔案裡就有——`.aicc/conflicts/` 只被其中一邊排掉，於是同一次執行的
# 「變更檔案」與「diff」講的是兩件不同的事，而兩張單子都看起來很正常。
GENERATED_PATH_PREFIXES = (
    ".aicc/runs/",
    ".aicc/timeline/",
    ".aicc/conflicts/",
    # 共同記憶是 **AICC 自己寫的**。專案把 `.aicc/` 一起進版控的時候（會的，
    # 那八份本來就要跟著 repo 走），引擎每一輪更新的 DECISIONS.md／
    # NEXT_STEPS.md 會混進「這一格做了什麼」——拍板那一格於是對著 AICC
    # 自己的筆記蓋章，而那不是任何人做的事。
    ".aicc/knowledge/",
)

# 同上，只是這幾個是檔案不是資料夾。`.aicc/project-brief.md` 也在這裡：
# 它是引擎跟使用者確認完自己寫下的計畫書。
GENERATED_PATH_EXACT = (
    ".aicc/sessions.json",
    ".aicc/workflow-result.json",
    ".aicc/sync-state.json",
    ".aicc/.gitignore",
    ".aicc/ai-settings.json",
    ".aicc/planning-request.md",
    ".aicc/project.json",
    ".aicc/project-brief.md",
)

# 一個新檔案最多放進「這一輪做出來的東西」幾行、幾個字，以及全部新檔案
# 合起來的上限。**超過就要在同一段文字裡講出來省略了多少**——無聲截斷的
# 檔案讀起來跟完整的一模一樣，而拍板那一格只讀得到這段文字。
NEW_FILE_MAX_LINES = 400
NEW_FILE_MAX_CHARS = 20_000
NEW_FILE_TOTAL_CHARS = 80_000

# `withhold_reason()` 回的理由，對到使用者（與廠商）讀到的那一句。
# 查不到的理由**照樣扣住內容**，用最嚴的那一句——新的理由出現的時候，
# 預設值要是「不送」而不是「送」。
WITHHOLD_TEXT_KEYS = {
    WITHHOLD_SECRET: "git.new_file.secret",
    WITHHOLD_RUNTIME: "git.new_file.runtime",
}


def generated_path_excludes() -> list[str]:
    """上面那份清單翻成 `git` 的排除語法。"""
    return [
        f":(exclude){prefix}**" for prefix in GENERATED_PATH_PREFIXES
    ] + [f":(exclude){path}" for path in GENERATED_PATH_EXACT]


def is_generated_path(file_path: str) -> bool:
    """這個路徑是 AICC 自己產的，不是這一輪有人做出來的。"""
    normalized = file_path.replace("\\", "/")
    return normalized in GENERATED_PATH_EXACT or normalized.startswith(
        GENERATED_PATH_PREFIXES
    )


class NoSeatWithDuty(RuntimeError):
    """這一步要派給某一種職責，而這份陣容裡一格都沒有。

    ## 為什麼是一個例外，不是一個退路

    `seat_with_duty(..., fallback=ROLE_REVIEWER)` 在一格複查都沒有的時候
    回 `"reviewer"`——**一個這份陣容裡不存在的名字**。下游
    `_run_role_prompt` 只看得到那個名字，於是它只講得出

    > 這個專案的陣容裡沒有「reviewer」這一格，但工作流要派工給它。
    > 目前排的是：node1、node2、node3、node4、node5。請到陣容頁重新編組。

    那句話**把使用者送去改一個沒有壞的東西**：他排的五格一點問題都沒有，
    真正的原因是那五格裡沒有任何一格的職責是「複查」，而計畫序列每一步
    都派給複查。他照著訊息去陣容頁「重新編組」，改的是名字——而名字不是
    那條路要的東西。2026-08-26 實機量到的就是這一次。

    退回一個不存在的名字，等於**把「為什麼」丟在這裡，只把「什麼」往下傳**。
    這裡是唯一知道為什麼的地方，所以理由要在這裡就帶上。

    ## 為什麼不挑一格代打

    跟 `steps/building.py` 與 `_no_worker_result()` 同一句話：挑到的那一格
    會拿到使用者從來沒有授權給它的工作，而畫面上看起來一切正常。

    `duty` 與 `seats` 存的是**代號與真名**，不是組好的中文——講哪一句由
    `workflow_worker.why_this_sequence_cannot_run()` 決定，而那要看序列。
    在這裡就把話寫死的話，同一個缺法在兩條序列上會講同一句，
    而它們的出路不一樣。
    """

    def __init__(self, duty: str, seats: tuple[str, ...]) -> None:
        # **這一句刻意是英文。**它只出現在 traceback 裡——使用者看到的是
        # `workflow_worker.why_this_sequence_cannot_run()` 挑的那一句中文。
        # 寫成中文的話它會被當成介面文字（`tools/i18n_scan`），而它不是：
        # 沒有任何一個畫面讀 `str(exc)`。
        super().__init__(
            f"no seat has duty {duty!r}; seated: {', '.join(seats) or '(none)'}"
        )
        self.duty = duty
        self.seats = tuple(seats)


class Orchestrator:
    """Runs one workflow.

    Steps are addressed by **role** (`author`, `reviewer`), and which provider
    fills a role comes from `self.lineup` (ROADMAP P10). Before this, the
    method name *was* the role - `run_claude()` meant "the author writes" and
    `run_codex_review()` meant "the reviewer reviews" - so the two could not be
    told apart, and two positions held by the same vendor would have shared one
    session id.

    One thing the role names do not describe well, and it is worth knowing
    before trusting them: in **planning mode the duties swap while the agents
    do not**. `run_codex_plan` has the reviewer produce the plan and
    `run_claude_plan_review` has the author critique it - the opposite of the
    development sequence, with the same two seats. An earlier version of this
    refactor "fixed" that by inverting the line-up for planning mode; it was
    wrong, and the orchestrator tests caught it immediately, because
    `run_claude()` is a *writing* step that exists in both modes and would have
    been handed to Codex. The naming is imperfect but the behaviour is intact;
    whether planning deserves its own role names is a question for the line-up
    composer's design, not something to settle by renaming here.
    """

    MAX_FIX_ATTEMPTS = 2

    # Who fills each role when the caller does not compose a line-up. This
    # reproduces today's behaviour exactly; it is the starting point a user
    # edits, not a rule.
    DEFAULT_LINEUP = (("author", "claude"), ("reviewer", "codex"))

    # Every seat this engine knows how to dispatch, in the order it would run
    # them. **Not** the seats a given project runs - that is the project's own
    # line-up, and `seated_roles` reads it.
    #
    # The distinction was one name until ROADMAP P10 Stage B (`EXECUTED_ROLES`),
    # which was safe only while every project ran the same two seats. It is the
    # cap the composer enforces: a composer offering a seat this list does not
    # contain would draw a control that looks like it does something.
    # What each seat is doing in a given workflow sequence. The duties swap
    # between sequences while the seats do not - which is exactly why the seat
    # names are neutral and the duty is pushed separately to the UI.
    #
    # Keyed by **role**, not by seat index. Index keys were correct only while
    # every line-up had the same seats in the same positions; seating a planner
    # in front shifts the author from 0 to 1, and index keys would have
    # relabelled the author 「擬做法」 without anything failing.
    # 這一次序列裡，**每一種職責在做什麼**。
    #
    # ## 2026-08-25 從「角色名」改成「職責」
    #
    # 這張表本來的鍵是 `ROLE_AUTHOR` / `ROLE_REVIEWER` / `ROLE_PLANNER`，
    # 而使用者從那天起可以把座位改成任何名字。實測的下場（兩個方向都會說謊）：
    #
    # | 使用者做了什麼 | 畫面說 | 引擎做 |
    # |---|---|---|
    # | 改名字 | 整排虛線紅框「不會被執行」 | **照跑** |
    # | 只改職責下拉 | 「寫程式」，一則警示都沒有 | **一格都不做** |
    #
    # 第二種更貴：他要按下開始、付完前置呼叫才知道。而
    # `_run_role_prompt` 上面那段註解早就在警告這件事（「兩邊漂開的話，
    # 畫面會讓使用者排出一個引擎會拒絕的陣容」）——**同一個檔案裡的
    # `sequence_for` 自己就在製造它**。
    #
    # 職責是封閉的四個值，名字不是。**用會漂的東西當鍵，就是這個結果。**
    SEQUENCE_JOBS = {
        "development": {
            "label": "sequence.development",
            "jobs": {
                DUTY_PLAN: "job.plan",
                DUTY_WORK: "job.build",
                DUTY_REVIEW: "job.review",
                DUTY_APPROVE: "job.approve",
            },
        },
        # 計畫序列裡**沒有**擬做法那一種：這條序列本身就是在擬，
        # 而一格 `plan` 職責的位子在裡面沒有第二件事可做。組陣容的那一側
        # 會把它標成「這次不會跑」，而不是替它發明一個工作。
        #
        # ## 2026-08-27 核准那一種也從這裡拿掉了
        #
        # 這一行本來寫著 `DUTY_APPROVE: "job.approve"`，而
        # `workflow_worker._run_planning` **一次都沒有碰過核准那一格**
        # ——拍板那一關（`_approval_phase`）只長在開發序列那條路上。
        # 實測老闆那五格：**陣容頁說「派工給 2 格」，引擎實際派 0 格。**
        #
        # 拿掉之後他的第五格會從「這一輪：拍板」變成虛線圈。那是誠實的：
        # 引擎真的不會派工給它。**這張表上的每一列都是一句承諾**，
        # 而一句畫在畫面上、引擎不打算兌現的承諾，比空白貴——
        # 空白他會去問，承諾他不會。
        "planning": {
            "label": "sequence.planning",
            "jobs": {
                DUTY_WORK: "job.read_plan",
                DUTY_REVIEW: "job.write_plan",
            },
        },
    }

    @classmethod
    def sequence_for(
        cls, workflow_mode: str, roles: tuple[str, ...] | None = None,
        duties: tuple[str, ...] | None = None,
    ) -> dict:
        """Label and per-seat duty for the UI, keyed by seat index.

        `duties` 是每一格的職責，依座位順序。`roles` 只剩相容用途——
        呼叫端沒給 `duties` 的時候，照舊角色名推一次職責，**而那是給還沒
        改過的呼叫端用的相容路徑，不是預設值**。

        沒有職責對應的那一格不算被派工。組陣容的那一側要明講「這次不會跑」
        ——一個空白的標籤讀起來是「沒有名字」，不是「什麼都不做」。
        """
        mode = "planning" if workflow_mode == "planning" else "development"
        entry = cls.SEQUENCE_JOBS[mode]
        if duties is None:
            # 相容路徑：照舊角色名推職責，跟讀舊 `sessions.json` 走同一張表。
            # **這不是預設值**——推不出來的名字會落到 `review`，而那正是
            # 「用會漂的東西當鍵」的老毛病。呼叫端該給 `duties`。
            seated = roles if roles is not None else CLASSIC_ROLES
            duties = tuple(
                _DUTY_BY_LEGACY_ROLE.get(role.strip().lower(), DUTY_REVIEW)
                for role in seated
            )
        # 那張表存的是 key（模組層存字會在 import 的當下凍住）。用到才取。
        jobs = {
            str(index): t(entry["jobs"][duty])
            for index, duty in enumerate(duties)
            if duty in entry["jobs"]
        }
        return {
            "label": t(entry["label"]),
            # **代號，不是那句翻譯過的標籤。**畫面靠它決定「這一輪」那一格
            # 是下拉還是鎖著（老闆 2026-08-27：「計畫序列的要鎖定，
            # 開發序列的用下拉式選單，畢竟是不同功能」）。
            #
            # `label` 答不了那一題：它是 `t()` 出來的字，英文版上線那天
            # 判斷會永遠不成立，**而畫面看起來完全正常**。
            # 這一頁已經為「拿中文當查詢的鍵」壞過一次。
            "mode": mode,
            "jobs": jobs,
            "executed_slots": len(jobs),
        }

    def __init__(
        self,
        project_path: Path,
        claude_session_id: str = "",
        codex_session_id: str = "",
        run_id: str = "",
        settings: AIExecutionSettings | None = None,
        lineup: Lineup | None = None,
    ) -> None:
        self.project_path = project_path.resolve()
        self.aicc_path = self.project_path / ".aicc"
        self.settings = (settings or AIExecutionSettings()).normalized()
        task_file_name = (
            "planning-request.md"
            if self.settings.workflow_mode == "planning"
            else "task.md"
        )
        self.task_path = self.aicc_path / task_file_name
        if (
            self.settings.workflow_mode == "planning"
            and not self.task_path.is_file()
        ):
            self.task_path = self.aicc_path / "task.md"

        if not self.project_path.is_dir():
            raise ValueError(
                f"Project directory does not exist: {self.project_path}"
            )

        if not self.task_path.is_file():
            raise ValueError(
                f"Task file does not exist: {self.task_path}"
            )

        self.aicc_path.mkdir(parents=True, exist_ok=True)
        self.runner = CliRunner(self.project_path)
        self.max_fix_attempts = self.settings.max_fix_attempts
        self.session_registry = SessionRegistry(self.project_path)
        self.timeline = TimelineStore(self.project_path)
        self.knowledge = ProjectKnowledgeStore(self.project_path)
        self.context_ledger = ContextLedger(self.project_path)
        # What each seat was asked and what it said (ROADMAP P10 Stage C).
        self.step_log = StepLog(self.project_path)
        # Size of the project context sent into the current CLI session. Later
        # rounds reuse that session instead of re-sending, and this is how much
        # they skip - see _knowledge_context_for_round().
        self._knowledge_context_characters = 0
        self.long_task_planner = LongTaskPlanner()
        self.decision_log = DecisionLogStore(self.project_path)
        # Called with the seat about to run, from the one funnel every model
        # call goes through. The UI lights that seat in the line-up summary.
        #
        # A callback rather than the worker announcing it beside each
        # `stage_changed`: the worker would then be restating which seat each
        # orchestrator method uses, and the day the two disagree the wrong seat
        # lights up - which is worse than none lighting up, because a lit seat
        # is a confident-looking claim. One source, set where the dispatch
        # actually happens.
        self.on_role_start: Callable[[str], None] | None = None
        self.lineup = self._resolve_lineup(
            lineup,
            claude_session_id.strip(),
            codex_session_id.strip(),
        )
        # Session ids are keyed by role, not by provider. That is the whole
        # change: two Claude slots have two sessions, where a single
        # `claude_session_id` field could only ever hold one and the second
        # agent would silently resume the first one's conversation.
        self._role_sessions = {
            slot.role: slot.session_id for slot in self.lineup
        }
        task = self.task_path.read_text(encoding="utf-8")
        self.run_id = self.timeline.create_run(
            task=task,
            run_id=run_id,
            lineup=self.lineup,
        )
        self.timeline.update_run(
            self.run_id,
            extra={"ai_settings": self.settings.to_dict()},
        )
        # Long-task mode. This was unconditional, which made the settings page's
        # toggle unimplementable: there was nothing to turn off. A short task
        # gains nothing from a milestone plan and pays for it in tokens.
        if self.settings.long_task_mode:
            self.long_task_planner.write_plan(
                self.project_path,
                self.run_id,
                task,
            )
            self.timeline.save_artifact(
                self.run_id,
                "milestones.md",
                self.long_task_planner.render_plan(task),
            )

    # ---- line-up and roles ----------------------------------------------
    def _resolve_lineup(
        self,
        lineup: Lineup | None,
        claude_session_id: str,
        codex_session_id: str,
    ) -> Lineup:
        if lineup is not None and len(lineup):
            return lineup
        by_provider = {"claude": claude_session_id, "codex": codex_session_id}
        return Lineup.of(
            AgentSlot(role, provider, by_provider.get(provider, ""))
            for role, provider in self.DEFAULT_LINEUP
        )

    @property
    def seated_roles(self) -> tuple[str, ...]:
        """The roles **this project** runs, in order, filtered to what the
        engine can dispatch.

        A stored line-up can name a role no step exists for (the data model
        never restricted it). Those are dropped here rather than dispatched,
        because the alternative is a crash halfway through a paid run.
        """
        # **不再用一份寫死的清單過濾。**
        #
        # 2026-08-22 之前這裡是 `if role in self.DISPATCHABLE_ROLES`，而那份
        # 清單只有三個名字——於是「上限三格」變成了產品的規格，儘管
        # 【資料結構現在就做成 node 圖】那條定案的不變式寫著「兩角色版必須是
        # node 圖的一個特例，**不得另立一套結構**」。一份寫死的角色清單就是
        # 另立的那一套。
        #
        # 現在每一個排進陣容的位子都會被派工；它**做哪一種事**由它自己的
        # `duty` 決定，不由名字決定。使用者要把位子叫「發想」「粗剪」都可以。
        return self.lineup.roles

    def duty_for(self, role: str) -> str:
        """這一格**做哪一種事**。排不到的位子回空字串。

        職責跟名字分開：名字是使用者的（「發想」「上字幕」都行），職責是
        引擎唯一看得懂的東西。見 `app/core/lineup.py` 的 `VALID_DUTIES`。
        """
        target = role.strip().lower()
        for slot in self.lineup:
            if slot.role == target:
                return slot.duty
        return ""

    def may_write_files(self, role: str) -> bool:
        """這一格**動不動得了這個專案的檔案**。排不到的位子一律 False。

        跟 `duty_for` 是兩個問題，不是一個問題的兩種問法：職責回答「它在
        流程裡是什麼」，這一支回答「它動不動得了我的檔案」。使用者關掉
        寫檔的那一格職責**還是 work**，所以它照樣被 `steps/building.py`
        挑中、照樣講話、話照樣接給下一格。
        """
        return self.lineup.may_write_files(role)

    def seat_with_duty(self, duty: str, *, fallback: str = "") -> str:
        """做這一種事的那一格**叫什麼名字**。排不到就回 `fallback`。

        ## 為什麼要有這一支

        2026-08-25 掃出來：這個檔案裡有**九個地方**直接拿 `ROLE_AUTHOR` /
        `ROLE_REVIEWER` 這種出廠名字去派工。使用者一改名字，那幾行就走到
        `_run_role_prompt` 的 `NotImplementedError`——實測訊息是

        > 這個專案的陣容裡沒有「reviewer」這一格，但工作流要派工給它。
        > 目前排的是：node1、node2、node3、node4、node5。

        **而那是在五格全部跑完、複查回 PASS、章都齊了之後才炸的**（維護共同
        記憶那一步，出廠預設開著）。五次派工的額度已經花完，收場是「執行失敗」
        加一整坨 traceback。

        `workflow_worker._roles_with()` 早就在做同一件事，而這個檔案沒有跟上
        ——**同一個修法只落在一邊，另一邊照舊**。

        ## 為什麼是最後一格

        `duty` 是封閉的四個值，一份陣容裡可以有好幾格同一種職責。挑最後一格
        跟 `workflow_worker` 挑修正者、`chain_checker` 挑驗鏈的那一格同一套：
        **後面的看過前面的**。

        ## `fallback` 為什麼不給預設值

        呼叫端必須自己回答「排不到的時候要怎樣」。回一個空字串往下傳，
        會在三層之後變成一句看不懂的錯誤；而**猜一格代打更糟**——那一格會
        拿到使用者從來沒有授權給它的工作。
        """
        seats = [slot.role for slot in self.lineup if slot.duty == duty]
        return seats[-1] if seats else fallback

    def required_seat_with_duty(self, duty: str) -> str:
        """做這一種事的那一格。**排不到就停下來。**

        `seat_with_duty` 的另一半：那一支問的是「排不到的時候退回誰」，
        這一支說的是「這一步沒有那一格就走不下去」。

        用它的地方是**整條序列的形狀就是那個職責**的那幾步——計畫序列的
        每一步都派給複查（`run_codex_plan`），審計畫那一步派給執行
        （`run_claude_plan_review`）。這幾步沒有「退回誰」這個答案：
        退回一個名字就是退回一個陣容裡沒有的人，而挑一格代打會把使用者
        沒有授權的工作交出去。

        丟 `NoSeatWithDuty`，由 `workflow_worker` 收成一個講得出理由的結局。
        **不要在這裡組中文**——同一個缺法在兩條序列上出路不一樣。
        """
        seat = self.seat_with_duty(duty)
        if not seat:
            raise NoSeatWithDuty(
                duty, tuple(slot.role for slot in self.lineup)
            )
        return seat

    @property
    def has_planner(self) -> bool:
        return ROLE_PLANNER in self.seated_roles

    def provider_for_role(self, role: str) -> str:
        slot = self.lineup.for_role(role)
        return slot.provider if slot else ""

    def session_for_role(self, role: str) -> str:
        return self._role_sessions.get(role.strip().lower(), "")

    def _set_role_session(self, role: str, session_id: str) -> None:
        role = role.strip().lower()
        self._role_sessions[role] = session_id
        self.lineup = self.lineup.with_session(role, session_id)
        self.timeline.update_run(self.run_id, lineup=self.lineup)

    # The two names the rest of the codebase still uses. Read-only, and they
    # answer for the *first* slot that provider holds - so a line-up with two
    # Claudes cannot be read through them. Anything that needs to know which
    # one asks by role.
    @property
    def claude_session_id(self) -> str:
        return self.lineup.session_for_provider("claude")

    @property
    def codex_session_id(self) -> str:
        return self.lineup.session_for_provider("codex")

    def _run_role_prompt(
        self,
        role: str,
        prompt: str,
        event_type: str,
        metadata: dict | None = None,
        *,
        read_only: bool = False,
        title_prefix: str = "Codex",
    ) -> CliResult:
        """Run a step as whoever holds `role`.

        The dispatch that used to be spelled out at each call site by naming
        `_run_claude_prompt` or `_run_codex_prompt` directly.

        `read_only` is the step's *intent*, and each provider translates it -
        `-s read-only` for Codex, `--permission-mode plan` for Claude. The call
        sites used to state the provider's own flag, which is another way the
        two were welded together: a step could not say what it needed without
        already knowing who would do it.
        """
        provider = self.provider_for_role(role)
        if not provider:
            # Nobody is seated in this role. Before the engine read the stored
            # line-up (ROADMAP P10 Stage B) this was unreachable - the line-up
            # was always the built-in default - and the fall-through below
            # would have quietly run it as Claude, because "not codex" was the
            # only other branch. A hand-edited `sessions.json` can reach it now,
            # and running a step as a vendor nobody chose is exactly what the
            # Codex-cannot-write refusal exists to prevent, one door over.
            raise NotImplementedError(
                f"這個專案的陣容裡沒有「{role}」這一格，但工作流要派工給它。"
                f"目前排的是：{'、'.join(self.lineup.roles) or '（空）'}。"
                "請到陣容頁重新編組。"
            )
        if not read_only and self.duty_for(role) != DUTY_WORK:
            # 會寫檔的位子，職責必須是 work。這一條不是形式檢查：職責決定
            # 編組畫面允不允許把這一格指派給 Codex，也決定它拿不拿得到寫入
            # 權限。**兩邊漂開的話，畫面會讓使用者排出一個引擎會拒絕的陣容，
            # 而他要按下開始執行才會知道。**
            raise NotImplementedError(
                f"位子「{role}」執行了需要寫入的步驟，但它的職責是"
                f"「{self.duty_for(role)}」不是「{DUTY_WORK}」。"
                "要它能寫檔就把職責改成 work，否則這一步不該派給它。"
            )
        if not read_only and not self.may_write_files(role):
            # 使用者把這一格的寫檔開關關掉了。**這一次派工降級成唯讀，
            # 而不是拒絕、也不是跳過。**
            #
            # 拒絕的話跟改職責沒有兩樣——`steps/building.py` 照樣會挑中它
            # （它的職責還是 work），然後整條線停在一個例外上。跳過更糟：
            # 那一格這一輪一句話都不會說，而**後面每一格看得到的東西是靠
            # AICC 把前一格說的話接下來貼過去的**，不是靠檔案。少了它，
            # 下一格會拿到一份中間缺一塊的脈絡，然後把同一件事再做一次。
            #
            # 降級寫在這裡而不是在呼叫端：這是每一次模型呼叫的**唯一**漏斗，
            # 而它下面那幾行（Codex 的沙箱旗標、`permission_mode="plan"`、
            # 寫進 step log 的 `read_only`）全部吃這個變數。改在呼叫端的話，
            # 漏掉的那一條路會照舊拿到寫入權，而畫面上看不出來。
            read_only = True
        # ⛔ 這裡 2026-09-01 之前有一條「Codex 不准寫檔」的拒絕。
        #
        # 老闆當天看到那個警示的反應：
        #
        #   > 哪個智障說只有 claude 可以寫的，我之前明明就說都可以，
        #   > 只是在預設設定的時候說過那個 node 用 codex 剛好只有審閱、
        #   > 不需要寫資料而已
        #
        # **他是對的。** 翻遍全部對話紀錄（他講 codex/claude ＋ 寫／檔案／權限
        # 的 142 則）、`DECISIONS.md`、`WHY.md`——沒有一句是他講的。
        # 源頭是這一段自己的註解：「AICC 今天跑的每個 Codex 步驟都是唯讀的，
        # 所以會寫檔的那個沙箱旗標從來沒被驗證過，猜一個等於用一個沒人選過的
        # 權限跑他的 repo」。
        #
        # 那是一個**還沒填的值**，不是一條規則。而它被寫死成
        # `WRITING_PROVIDERS = ("claude",)`，散進畫面三道閘門與三個測試檔，
        # 還反過來被 `WHY.md` 引用成前提。
        #
        # 2026-09-01 老闆拍板填 `workspace-write`（只能寫專案資料夾裡面，
        # 跟 Claude 那一半拿到的權限對等）。`danger-full-access` 明確否決。
        # Announced only past the refusals above: a seat that is about to be
        # rejected never runs, and lighting it would leave the line-up showing
        # a step that did not happen.
        if self.on_role_start is not None:
            self.on_role_start(role)

        started_at = datetime.now().isoformat(timespec="seconds")
        if provider == "codex":
            result = self._run_codex_prompt(
                prompt,
                event_type,
                # 這一步的**意圖**翻譯成 Codex 的沙箱等級。
                # ⚠️ 寫死成 `"read-only"` 的那一版（2026-09-01 之前）不只是
                # 「擋住寫入」——它讓一個真的需要寫檔的步驟**安靜地跑成唯讀**，
                # 而畫面上那一格看起來跑完了。上面那條拒絕存在的理由就是這個：
                # 沒有值可填，所以寧可拒絕。現在有值了。
                permission="read-only" if read_only else "workspace-write",
                title_prefix=title_prefix,
                metadata=metadata,
                role=role,
            )
        else:
            result = self._run_claude_prompt(
                prompt,
                event_type,
                metadata=metadata,
                permission_mode="plan" if read_only else None,
                role=role,
            )

        # Recorded here rather than in the two provider methods: this is the
        # one funnel every model call passes through, so a step cannot be run
        # without being recorded. Recording in both branches would be two
        # places to forget, and the one that gets forgotten is the new one.
        self.step_log.append(
            self.run_id,
            StepRecord(
                index=self.step_log.next_index(self.run_id),
                role=role,
                provider=provider,
                event_type=event_type,
                read_only=read_only,
                prompt=prompt,
                output=result.stdout,
                success=result.success,
                started_at=started_at,
                finished_at=datetime.now().isoformat(timespec="seconds"),
                custom_identity=bool(self._identity_for(role)),
                metadata=dict(metadata or {}),
            ),
        )
        return result

    def _stop_on_cut_memory(self, cut: MemoryTruncatedError) -> None:
        """共同記憶被截斷時，**停下這一輪**——而且三條路停法一樣。

        以前這裡只有一條路會停。`_knowledge_context` 用下面那個
        `except Exception` 把 `MemoryTruncatedError` 接住、寫一行「專案資料
        索引讀取失敗」、然後 `return ""` 讓這一輪照跑；而 decision gate 與
        memory update 那兩條路則是讓它一路炸成 traceback。**同一個守衛、三條
        路、兩種行為。**

        吞掉那條路比「炸掉」更糟，而且糟在一個不明顯的地方：空字串到了
        `prompt_builder` 會被換成「（尚未建立專案資料索引，請依目前專案檔案
        判斷。）」——索引明明建好了，被切掉的是安全規則，而模型收到的是一句
        **與事實不符的話**，外加一句「你自己看著辦」。一個被截斷的安全規則
        讀起來跟完整的一模一樣；一句「還沒建索引」讀起來則像是可以繼續。

        所以現在三條路都走這裡：在時間軸留下一條說得出原因的紀錄，然後把
        例外原封不動往上丟。`WorkflowWorker` 會把它收成一個 `partial` 結局
        （`MEMORY_TRUNCATED`），也就是「這一輪停了，而且要人處理」，而不是
        一個沒有人看的 traceback，也不是一個看起來很正常的空脈絡。
        """
        self.timeline.append(
            self.run_id,
            "aicc",
            "memory_truncated",
            f"共同記憶沒有完整送出，這一輪停下來：{cut}",
        )

    def _memory_context(self) -> str:
        """八份共同記憶的全文，截斷就停。

        `run_codex_decision_gate` 與 `run_codex_memory_update` 走這裡，
        `_knowledge_context` 走它自己那條（它還要組文件與統計），但兩邊對
        「被切了」的答案是同一個：記一筆、往上丟、這一輪不跑。
        """
        try:
            return self.knowledge.build_memory_context()
        except MemoryTruncatedError as cut:
            self._stop_on_cut_memory(cut)
            raise

    def _with_project_brief(self, context: str) -> str:
        """把專案計畫書接在脈絡最前面。**每一格都要看得到。**

        老闆 2026-08-24 的原話：**「每個 node 都要能看到專案計畫書啊，
        不然怎麼做專案」**。

        在這之前只有需求確認那一格讀得到它（它是寫的人），後面每一格都在
        對著一份自己沒看過的計畫書做事——而它們交出來的東西看起來完全正常。

        接在**最前面**是刻意的：它是「這個專案在做什麼」，是讀後面所有東西
        的前提。排在共同記憶後面的話，長脈絡裡它會被推到很下面。
        """
        brief = self.project_brief().strip()
        if not brief:
            return context
        head = t("brief.heading") + brief
        return head + "\n\n" + context if context else head

    def _knowledge_context(self, task: str, role: str = "") -> str:
        """這一格拿到的脈絡。**跟職責走**——見 `app/core/rule_layers.py`。

        `role` 空的、或者陣容裡沒有那個名字，`duty_of` 會回空字串，而
        `build_context` 會走最保守的那條（點名已定案的那一份）。
        **不在這裡猜一個職責**：猜錯的代價是那一格收到不對的規則，
        而畫面上看不出來。
        """
        try:
            context = self._with_project_brief(
                self.knowledge.build_context(
                    task,
                    context_profile=self.settings.context_profile,
                    duty=duty_of(self.lineup, role) or DUTY_WORK,
                )
            )
            stats = self.knowledge.context_stats(
                task,
                context_profile=self.settings.context_profile,
            )
        except MemoryTruncatedError as cut:
            # 這一條**不准**掉進下面那個 `except Exception`。它不是「索引讀
            # 不到」，它是「索引讀到了，但安全規則少了一截」——兩件事的正確
            # 反應相反：前者可以沒有脈絡照跑，後者不行。
            self._stop_on_cut_memory(cut)
            raise
        except Exception as exc:
            self.timeline.append(
                self.run_id,
                "aicc",
                "knowledge_error",
                f"專案資料索引讀取失敗：{exc}",
            )
            return ""

        if not context:
            return ""

        # Why these documents, not just how many (ROADMAP P0.5). Empty for the
        # ordinary keyword match: a line on every run saying 「照關鍵字挑的」
        # is a line people stop reading, and then it is not there on the run
        # where the answer is 「沒有為什麼，這是最近更新的」.
        try:
            note = self.knowledge.selection_note(
                task, context_profile=self.settings.context_profile
            )
        except Exception:
            note = ""
        self.timeline.append(
            self.run_id,
            "aicc",
            "knowledge_context",
            (
                "已選取專案資料："
                f"{stats['documents']} 份文件、"
                f"{stats['chunks']} 個區段、"
                f"約 {stats['estimated_tokens']} tokens。"
            )
            + (f"\n{note}" if note else ""),
        )
        self.timeline.save_artifact(
            self.run_id,
            "knowledge-context.md",
            context,
        )
        # stats is the breakdown this run really carried. It was computed above
        # for the timeline line and then discarded; keeping it is what lets the
        # dashboard report context usage from a record instead of a guess.
        self.context_ledger.record_sent(
            self.run_id, stats, self.settings.context_profile
        )
        self._knowledge_context_characters = len(context)
        return context

    def _knowledge_context_for_round(self, task: str, first_round: bool) -> str:
        """Project context for one round of a multi-round conversation.

        Only the first round carries it. The rounds after that continue the same
        CLI session, which still has it, so re-sending would pay for the same
        payload again and teach the model nothing new. The size skipped is
        recorded rather than estimated: it is exactly what the first round sent.
        """
        if first_round:
            return self._knowledge_context(task)
        self.context_ledger.record_resend_skipped(
            self.run_id, self._knowledge_context_characters
        )
        return ""

    def _session_bootstrap_context(self, task: str, provider: str) -> str:
        try:
            path = self.knowledge.write_session_bootstrap(task)
            content = path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            self.timeline.append(
                self.run_id,
                "aicc",
                "bootstrap_error",
                f"{provider} session bootstrap 產生失敗：{exc}",
            )
            return ""

        self.timeline.append(
            self.run_id,
            "aicc",
            "session_bootstrap",
            f"已為新的 {provider} session 載入啟動提示：{path.name}",
        )
        self.timeline.save_artifact(
            self.run_id,
            f"{provider}-session-bootstrap.md",
            content,
        )
        return content

    def _prompt_with_bootstrap(
        self,
        prompt: str,
        task: str,
        provider: str,
        has_session: bool,
        role: str = ROLE_AUTHOR,
    ) -> str:
        """把啟動脈絡包在提示詞前面。

        **第一行是刻意當標題用的。**Codex 的對話清單直接拿使用者的第一句話
        當標題（2026-08-21 查它自己的資料庫確認的：`title` 欄位就等於
        `first_user_message`）。原本第一句是「AI Control Center 新 session
        啟動提示：」，所以他的 Codex 清單上每一筆長得一模一樣，全都是同一句
        廢話——等於看得到卻認不出來。

        改成「AICC·專案·角色｜任務」之後，那份清單不用任何整合就變得可讀。
        **這是完全走官方介面的做法**：沒有碰任何私有檔案，只是換一句話。
        """
        if has_session or not self.settings.auto_session_bootstrap:
            return prompt

        bootstrap = self._session_bootstrap_context(task, provider)
        if not bootstrap:
            return prompt

        return f"""
{self._session_title_line(role, task)}

{BOOTSTRAP_MARKER}，之後才是本次的實際請求。

{bootstrap}

---

本次實際請求：

{prompt}
""".strip()

    def _identity_for(self, role: str) -> str:
        """This seat's own 「你是⋯⋯」 line, or "" for the shipped one.

        ROADMAP P10 Stage D. Keyed by **seat**, not by step: a seat is what
        the composer draws and what the user names, and one box per seat is
        what 「每個 NODE 讓客戶自己加 prompt」 asks for. The consequence is
        worth stating because it is not obvious - the author runs five steps
        with five different shipped identities, and one custom identity
        replaces all five. The safety half of each opening line still differs
        per step, so a read-only step keeps 「現在是唯讀檢查，不得修改檔案。」
        after the identity a person typed.

        Read from the line-up rather than a separate map, because the line-up
        is already what the composer writes and the engine dispatches from. A
        second store would be a second answer to 「這一格是誰」.
        """
        return self.lineup.identity_for(role)

    def _milestone_plan(self) -> str:
        """The step list to hand the author, or "" when there is nothing to say.

        ROADMAP P3「將長 Claude 任務拆成真正可恢復的小段」. The plan was already
        being generated and written to `milestones.json` / `milestones.md` on
        every long-task run - and **nothing read it**. Not the prompt, not the
        index, not a screen. Verified by running a workflow and checking what
        reached the CLI: the files appeared, the prompt never mentioned them.

        The rendered text is addressed to Claude in the second person
        (「Claude should complete work in small resumable steps」), so it was
        written to be shown and then left unplugged. This connects it.

        Empty when the split produced **one** milestone, which is what happens
        for a task written as prose: a one-item plan is the task repeated back
        to the model that already has it, and paying tokens to say something
        twice is worse than saying it once.
        """
        if not self.settings.long_task_mode:
            return ""
        try:
            task = self.task_path.read_text(encoding="utf-8")
        except OSError:
            return ""
        if len(self.long_task_planner.split_task(task)) < 2:
            return ""
        return self.long_task_planner.render_plan(task)

    def _output_instruction(self) -> str:
        return prompt_builder.output_instruction(self.settings.claude_verbosity)

    def _claude_safety_instruction(self) -> str:
        return prompt_builder.claude_safety_instruction()

    def retry_step(self, index: int) -> CliResult:
        """Run one recorded step again, with the prompt it was given.

        ROADMAP P10 Stage C. **Does not resume the workflow** - it re-dispatches
        one seat and records the outcome as a new step. That is deliberately
        less than it sounds, and saying so is the point: the development
        sequence is a hand-written procedure, not a resumable state machine,
        so "carry on from step 4" is not something this can honestly offer yet.

        What it does buy today is the expensive half of the answer to 「這一格
        是偶發失敗還是 prompt 有問題」, without paying for the seats in front
        of it. For the planner it buys more: a successful retry writes
        `work-plan.md`, and the next full run reads that instead of planning
        again.

        Refuses when the line-up no longer seats that role on that provider.
        Running the recorded prompt as a different agent would produce a
        result filed under a step that never happened that way, and the
        difference is invisible afterwards.
        """
        steps = self.step_log.read(self.run_id)
        match = next((s for s in steps if s.index == index), None)
        if match is None:
            raise ValueError(
                f"這一輪（{self.run_id}）沒有第 {index} 步。"
                f"目前有 {len(steps)} 步，序號 0 到 {len(steps) - 1}。"
            )

        provider_now = self.provider_for_role(match.role)
        if provider_now != match.provider:
            raise ValueError(
                f"第 {index} 步當初是「{match.role}」由 {match.provider} 執行，"
                f"但現在這個角色是 {provider_now or '沒有人'}。"
                "陣容已經改過，重跑會變成另一個人做同一件事——"
                "先把陣容改回來，或者重新跑一整輪。"
            )

        result = self._run_role_prompt(
            match.role,
            match.prompt,
            event_type=match.event_type,
            read_only=match.read_only,
            metadata={**match.metadata, "retry_of": index},
        )
        self._persist_step_output(match.event_type, result)
        return result

    WORK_PLAN_ARTIFACT = "work-plan.md"

    # Steps whose output the next run reads back instead of producing again.
    # 需求確認問清楚之後訂下來的那份東西。**它就是驗收標準**，後面每一格
    # 照它做、最後拿它驗收，所以它跟做法一樣要落地——中斷續跑不能重問一次。
    ACCEPTANCE_ARTIFACT = "acceptance-criteria.md"
    # 到目前為止問過什麼、使用者答了什麼。存的是過程不是結論：續跑的時候
    # 要讓那一格看得到自己問過什麼，否則它會從頭再問一遍同樣的問題。
    INTAKE_ROUNDS_ARTIFACT = "intake-rounds.json"
    # 執行的那一格退回驗收標準時附的理由，一次執行累積起來。
    # 累積而不是只留最後一次：訂標準的那一格要看得到它前面已經被打回幾次、
    # 為了什麼，否則它會把剛剛才被否掉的那一版再寫一次。
    INTAKE_OBJECTIONS_ARTIFACT = "criteria-objections.json"

    # 做事的那一格自己交的那一份做法。一個節點一份。
    WORK_INTENT_ARTIFACT = "work-intent.md"

    DURABLE_STEP_ARTIFACTS = {
        "work_plan": WORK_PLAN_ARTIFACT,
        "work_intent": WORK_INTENT_ARTIFACT,
    }

    @classmethod
    def _artifact_name(cls, event_type: str, key: str = "") -> str:
        """成品的檔名。`key` 通常是角色名——一個節點一份。"""
        base = cls.DURABLE_STEP_ARTIFACTS.get(event_type, "")
        if not base or not key:
            return base
        stem, _, ext = base.rpartition(".")
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)
        return f"{stem}-{safe}.{ext}"

    def _persist_step_output(
        self, event_type: str, result: CliResult, key: str = ""
    ) -> None:
        """Save a step's output where the next run looks for it.

        Called from the step itself **and** from `retry_step`, from one
        definition, because the two drifting apart is what makes a retry look
        like it worked and change nothing: the plan would be on screen and not
        on disk, and the next run would plan again.
        """
        name = self._artifact_name(event_type, key)
        if not name or not result.success or not result.stdout.strip():
            return
        self.timeline.save_artifact(self.run_id, name, result.stdout)

    def run_intake(
        self, role: str, rounds: tuple[tuple[str, str], ...] = ()
    ) -> CliResult:
        """跟使用者來回確認需求的那一格，跑一輪。

        一輪 = 一次派工。它要嘛丟回一個問題（引擎停下來問使用者），要嘛丟回
        一份訂好的驗收標準。**輪數沒有上限**——定案原文不准用輪數收尾，設了
        上限，到最後一輪不管清楚沒有它都會為了結束而宣告定案。

        `rounds` 是到目前為止的問答。整份帶回去而不是只帶最後一題，因為
        中斷續跑會換一個行程，而那一格自己不記得問過什麼。
        """
        task = self.task_path.read_text(encoding="utf-8")
        knowledge_context = self._knowledge_context(task, role)
        prompt = prompt_builder.build_intake_prompt(
            task=task,
            knowledge_context=knowledge_context,
            rounds=rounds,
            objections=self.stored_criteria_objections(),
            previous=self.project_brief(),
            identity=self._identity_for(role),
        )
        prompt = self._prompt_with_bootstrap(
            prompt,
            task,
            provider=self.provider_for_role(role),
            has_session=bool(self.session_for_role(role)),
            role=role,
        )
        self.timeline.append(
            self.run_id,
            "aicc",
            "intake_requested",
            t("step.intake_running"),
        )
        return self._run_role_prompt(
            role,
            prompt,
            event_type="intake",
            read_only=True,
            title_prefix=t("step.intake"),
        )

    def run_consult(
        self, role: str, asked_by: str, question: str, context: str = ""
    ) -> CliResult:
        """一格指名找另一格討論，被指名的那一格回答一次。

        老闆的五節點流程裡有三處寫著「有問題向 NODE2 討論」「與 NODE3 討論」
        「丟回 NODE1 確認可行性」。少了這條路，那三句話在引擎裡不存在——每一
        格只能對著它拿到的文字自己猜，而猜錯要等到最後複查才會被發現，那時候
        要重做的不只是它那一格。

        **唯讀**，不管被問的那一格平常是什麼職責。它現在的身分是被諮詢，不是
        被派工；給它寫入權限的話，一次討論會變成兩個人同時在改同一批檔案，
        而使用者從來沒有把這件事排進流程裡。
        """
        task = self.task_path.read_text(encoding="utf-8")
        prompt = prompt_builder.build_consult_prompt(
            task=task,
            asked_by=asked_by,
            question=question,
            acceptance_criteria=self.stored_acceptance_criteria(),
            context=context,
            identity=self._identity_for(role),
        )
        prompt = self._prompt_with_bootstrap(
            prompt,
            task,
            provider=self.provider_for_role(role),
            has_session=bool(self.session_for_role(role)),
            role=role,
        )
        self.timeline.append(
            self.run_id,
            "aicc",
            "consult_requested",
            f"{asked_by} 卡住了，指名找 {role} 討論。",
        )
        return self._run_role_prompt(
            role,
            prompt,
            event_type="consult",
            read_only=True,
            title_prefix=t("step.consult"),
        )

    def run_approval(
        self, role: str, chain: str, readiness: str, deliverable: str = ""
    ) -> CliResult:
        """最後那一格：看完所有的章，說這件事能不能拍板。

        老闆的五節點流程最後一句就是「NODE5 審閱所有簽章」。它**不做事、不
        驗證**——它回答一個所有機器都回答不了的問題：這一整條鏈站得住嗎。

        `chain` 與 `readiness` 是**引擎算好的事實**，不是叫它自己去翻紀錄。
        叫它自己翻的話它會翻錯，而錯的方式看起來完全正常。
        """
        task = self.task_path.read_text(encoding="utf-8")
        prompt = prompt_builder.build_approval_prompt(
            task=task,
            acceptance_criteria=self.stored_acceptance_criteria(),
            chain=chain,
            readiness=readiness,
            seats=self.lineup.roles,
            identity=self._identity_for(role),
            project_brief=self.project_brief(),
            deliverable=deliverable,
        )
        prompt = self._prompt_with_bootstrap(
            prompt,
            task,
            provider=self.provider_for_role(role),
            has_session=bool(self.session_for_role(role)),
            role=role,
        )
        self.timeline.append(
            self.run_id,
            "aicc",
            "approval_requested",
            f"{role} 正在審閱所有簽章。",
        )
        return self._run_role_prompt(
            role,
            prompt,
            event_type="approval",
            read_only=True,
            title_prefix=t("step.approve"),
        )

    # 專案自己的那份計畫書。**留在專案裡，不在某一次執行的資料夾裡。**
    #
    # 老闆 2026-08-22 的原話：「我們的發想角色會把專案跟使用者討論完的計畫書
    # 寫成檔案，然後讓後續的 NODE 去看專案目標與對應欄位對吧??」
    #
    # 在這之前答案是一半：那份東西存在，但存在 `.aicc/runs/<run_id>/`——**下
    # 一次執行從零開始問**。而《AI 協作手冊》裡「還沒吸收」那一欄第一條寫的
    # 正是這件事：「每次重新摸專案，前半場都浪費在問背景」。
    PROJECT_BRIEF_FILE = "project-brief.md"

    @property
    def project_brief_path(self) -> Path:
        return self.project_path / ".aicc" / self.PROJECT_BRIEF_FILE

    def project_brief(self) -> str:
        """專案自己那份計畫書。沒有就是空字串。

        讀壞了也回空字串——當成「還沒問過」比當成「問過了但內容是亂碼」安全：
        前者的代價是使用者再答幾題，後者是後面每一格照著一段亂碼做事。
        """
        try:
            return self.project_brief_path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def save_project_brief(self, brief: str) -> None:
        """把確認完的那份東西寫進專案。

        **原子寫**：寫到一半斷電的話，留下的是完整的舊版而不是半份新的——
        而半份計畫書讀起來跟完整的一模一樣。
        """
        if not brief.strip():
            return
        try:
            path = self.project_brief_path
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".tmp")
            temp.write_text(brief, encoding="utf-8")
            temp.replace(path)
        except Exception:
            return

    def save_acceptance_criteria(self, criteria: str) -> None:
        """把訂好的驗收標準落地。

        兩個地方各寫一份，而且兩份的意義不同：

        * **這一次執行的資料夾**——中斷續跑時沿用的就是它，一次執行一份，
          事後回溯得出「那一輪照的是哪一版」。
        * **專案的計畫書**——下一次執行的起點。少了它，每一次都從零開始問。
        """
        if criteria.strip():
            self.timeline.save_artifact(
                self.run_id, self.ACCEPTANCE_ARTIFACT, criteria
            )
            self.save_project_brief(criteria)

    def stored_acceptance_criteria(self) -> str:
        """這次執行已經訂好的驗收標準，沒有就是空字串。

        **只看這一次執行的那一份**，不回頭找專案計畫書。理由是章：章綁在內容
        版本上，而拿專案那份去頂替的話，這一輪根本沒有人蓋過的東西會被當成
        「這一輪訂的標準」——那個章不存在，而畫面上會顯示它存在。

        專案計畫書是**下一次執行的起點**，由需求確認那一步讀（見
        `build_intake_prompt` 的 `previous`）。
        """
        return self.timeline.read_artifact(
            self.run_id, self.ACCEPTANCE_ARTIFACT
        )

    def clear_acceptance_criteria(self) -> None:
        """把訂好的驗收標準作廢，讓下一輪重訂。

        **不刪掉問答歷史**：那一格要記得自己已經跟使用者確認過什麼，重訂的是
        標準，不是整段對話。清掉歷史等於叫使用者從頭再答一次同樣的問題。
        """
        self.timeline.save_artifact(self.run_id, self.ACCEPTANCE_ARTIFACT, "")

    def clear_work_plans(self) -> None:
        """把這一次執行已經擬好的做法全部作廢。

        驗收標準被退回重訂之後才叫得到。【重簽範圍由依賴決定】：做法是**照著
        舊標準擬的**，標準換了它就過期了。留著的話，執行的那一格會拿到「新的
        標準」配上「照舊標準擬的做法」——兩份文件互相矛盾，而它只能自己選一份
        照做，選哪一份都不會有人知道。

        會多付一次擬做法的錢。那是標準真的改了的代價，不是浪費——照著過期做法
        做出來的東西，錢一樣要付，而且還要再退一次。
        """
        for slot in self.lineup:
            name = self._artifact_name("work_plan", slot.role)
            if name:
                self.timeline.save_artifact(self.run_id, name, "")
        # 2026-08-22 以前一次執行只有一份做法，檔名是 `work-plan.md`。
        # `stored_work_plan` 還會回頭找它，所以這裡也要清掉——不清的話，
        # 舊執行續跑時會拿回一份照舊標準擬的做法。
        self.timeline.save_artifact(self.run_id, self.WORK_PLAN_ARTIFACT, "")

    def record_criteria_objection(self, seat: str, reason: str) -> None:
        """記下一次「這份標準做不到」的退回。"""
        if not reason.strip():
            return
        existing = list(self.stored_criteria_objections())
        existing.append((seat, reason.strip()))
        self.timeline.save_artifact(
            self.run_id,
            self.INTAKE_OBJECTIONS_ARTIFACT,
            json.dumps(
                [list(pair) for pair in existing], ensure_ascii=False, indent=2
            ),
        )

    def stored_criteria_objections(self) -> tuple[tuple[str, str], ...]:
        """這一次執行到目前為止，驗收標準被退回過幾次、為了什麼。

        讀壞了當成沒有退回過。壞掉的檔案讓標準少收到一條意見，代價是可能再被
        退一次；把它讀成別的東西，代價是訂標準的那一格照著一段亂碼改。
        """
        raw = self.timeline.read_artifact(
            self.run_id, self.INTAKE_OBJECTIONS_ARTIFACT
        )
        if not raw.strip():
            return ()
        try:
            data = json.loads(raw)
        except Exception:
            return ()
        if not isinstance(data, list):
            return ()
        out: list[tuple[str, str]] = []
        for item in data:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                out.append((str(item[0]), str(item[1])))
        return tuple(out)

    def save_intake_rounds(
        self, rounds: tuple[tuple[str, str], ...]
    ) -> None:
        self.timeline.save_artifact(
            self.run_id,
            self.INTAKE_ROUNDS_ARTIFACT,
            json.dumps(
                [list(pair) for pair in rounds], ensure_ascii=False, indent=2
            ),
        )

    def stored_intake_rounds(self) -> tuple[tuple[str, str], ...]:
        """問過的每一輪。讀壞了就當成沒問過——**不准當成問完了**。

        壞掉的檔案讓它重問一次，代價是使用者再答幾題；把它讀成「已經定案」，
        代價是後面每一格照著一份不存在的標準做事。
        """
        raw = self.timeline.read_artifact(
            self.run_id, self.INTAKE_ROUNDS_ARTIFACT
        )
        if not raw.strip():
            return ()
        try:
            data = json.loads(raw)
        except Exception:
            return ()
        if not isinstance(data, list):
            return ()
        out: list[tuple[str, str]] = []
        for item in data:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                out.append((str(item[0]), str(item[1])))
        return tuple(out)

    def run_work_plan(
        self,
        role: str = ROLE_PLANNER,
        discussion: tuple[tuple[str, str, str], ...] = (),
    ) -> CliResult:
        """The planner seat: produce the approach the author will implement.

        ROADMAP P10 Stage B, 形狀一. Only called when the project's line-up
        seats a planner; a two-seat project never reaches this method and its
        prompts are byte-identical to before the seat existed.

        The output is saved as an artifact because the run can stop at the
        decision banner and resume in a new process. Re-running the planner on
        resume would be a second paid call producing a second, possibly
        different plan for work already half done.
        """
        task = self.task_path.read_text(encoding="utf-8")
        knowledge_context = self._knowledge_context(task, role)
        prompt = prompt_builder.build_work_plan_prompt(
            task=task,
            knowledge_context=knowledge_context,
            output_instruction=self._output_instruction(),
            identity=self._identity_for(role),
            acceptance_criteria=self.stored_acceptance_criteria(),
            seats=self.lineup.roles,
            me=role,
            discussion=discussion,
        )
        prompt = self._prompt_with_bootstrap(
            prompt,
            task,
            provider=self.provider_for_role(role),
            has_session=bool(self.session_for_role(role)),
            role=role,
        )
        self.timeline.append(
            self.run_id,
            "aicc",
            "work_plan_requested",
            t("step.planning_running"),
        )
        result = self._run_role_prompt(
            role,
            prompt,
            event_type="work_plan",
            read_only=True,
            # `shown_name` 查不到回原值，所以自訂名字會原樣進 Claude 的
            # /resume 選單——而那正是使用者要在那份清單裡認出它的方式。
            title_prefix=shown_name(role),
        )
        # 一個節點一份成品。以前只有一格擬做法，所以檔名可以寫死；現在
        # 「發想」跟「規劃」都是 plan，共用一個檔名的話後跑的會蓋掉先跑的，
        # 而中斷續跑時會拿到別人的做法——畫面上完全看不出來。
        self._persist_step_output("work_plan", result, key=role)
        return result

    def run_work_intent(
        self,
        role: str,
        discussion: tuple[tuple[str, str, str], ...] = (),
    ) -> CliResult:
        """做事的那一格**第一次只交做法，不交成品**。

        定案原文：

        > 【執行角色第一次交做法，不交成品】不管產出是程式、文件、圖片還是
        > 別的，第一次只能交：我理解的需求／我打算怎麼做／我不確定的地方。
        > **否決：寫成規則叫它先別動手（那是叮嚀，會漏）。**
        > 不變式：第一步的輸出根本沒有「成品」那一格。

        所以這一步是**唯讀的一次派工**，用的是跟擬做法那一格同一份提示詞
        （四格、產出無關）。差別只在誰跑：那一格是別人替它想，這一步是**它
        自己用自己的話重講一遍**——講不出來就代表還沒對齊，而那件事只有它
        自己講得出來。

        有沒有規劃者不影響這一步。規劃者的做法是別人的理解；這一份是它的。
        """
        task = self.task_path.read_text(encoding="utf-8")
        prompt = prompt_builder.build_work_plan_prompt(
            task=task,
            knowledge_context=self._knowledge_context(task, role),
            output_instruction=self._output_instruction(),
            identity=self._identity_for(role),
            acceptance_criteria=self.stored_acceptance_criteria(),
            seats=self.lineup.roles,
            me=role,
            discussion=discussion,
            step="work_intent",
        )
        prompt = self._prompt_with_bootstrap(
            prompt,
            task,
            provider=self.provider_for_role(role) or "claude",
            has_session=bool(self.session_for_role(role)),
            role=role,
        )
        self.timeline.append(
            self.run_id,
            "aicc",
            "work_intent_requested",
            f"{role} 先交一份做法，還不動手。",
        )
        result = self._run_role_prompt(
            role,
            prompt,
            event_type="work_intent",
            read_only=True,
            title_prefix=t("step.approach"),
        )
        self._persist_step_output("work_intent", result, key=role)
        return result

    def stored_work_intent(self, role: str) -> str:
        """這一格這一輪已經交過的做法。續跑時沿用，不重問。"""
        return self.timeline.read_artifact(
            self.run_id, self._artifact_name("work_intent", role)
        )

    def stored_work_plan(self, role: str = ROLE_PLANNER) -> str:
        """The plan this run already produced, or "" if there is none.

        Read on resume so the planner is paid for once per run, not once per
        interruption.
        """
        stored = self.timeline.read_artifact(
            self.run_id, self._artifact_name("work_plan", role)
        )
        if stored.strip():
            return stored
        # 2026-08-22 之前一次執行只有一份做法，檔名是 `work-plan.md`。改成
        # 一個節點一份之後，**中斷的舊執行續跑時會找不到它自己的做法，
        # 於是重新規劃——再付一次錢，而且拿到一份不一樣的做法。**
        # 回頭找舊檔名，讓那些執行還接得下去。
        return self.timeline.read_artifact(self.run_id, self.WORK_PLAN_ARTIFACT)

    def run_claude(
        self,
        work_plan: str = "",
        role: str = ROLE_AUTHOR,
        discussion: tuple[tuple[str, str, str], ...] = (),
        own_intent: str = "",
    ) -> CliResult:
        """派工給一個「執行」職責的位子。

        `role` 預設是出廠的 author，所以既有呼叫端一個字都不用改。指名之後
        使用者自己排的位子（「實作」「粗剪」）也派得動——2026-08-22 之前這裡
        寫死 ROLE_AUTHOR，於是他排的五節點裡那一格根本不會被派到工。
        """
        task = self.task_path.read_text(encoding="utf-8")
        knowledge_context = self._knowledge_context(task, role)

        prompt = prompt_builder.build_task_prompt(
            task=task,
            knowledge_context=knowledge_context,
            safety_instruction=self._claude_safety_instruction(),
            output_instruction=self._output_instruction(),
            identity=self._identity_for(role),
            work_plan=work_plan,
            milestones=self._milestone_plan(),
            # 驗收標準從這一次執行的成品讀，不從呼叫端傳。**一份標準只准有
            # 一個來源**：呼叫端各自傳的話，駁回之後改過的標準只會出現在改
            # 過的那條路上，另一條路照舊拿舊的去做，而兩邊都不會報錯。
            acceptance_criteria=self.stored_acceptance_criteria(),
            criteria_seat=criteria_seat(self.lineup),
            seats=self.lineup.roles,
            me=role,
            discussion=discussion,
            own_intent=own_intent,
        )
        prompt = self._prompt_with_bootstrap(
            prompt,
            task,
            provider=self.provider_for_role(role) or "claude",
            has_session=bool(self.session_for_role(role)),
            role=role,
        )
        return self._run_role_prompt(role, prompt, "task")

    def run_claude_with_user_answer(
        self,
        question: str,
        answer: str,
        *,
        answered_by: str,
        role: str = "",
    ) -> CliResult:
        """Continue the run with an answer to a gate question.

        `answered_by` is keyword-only and has **no default** on purpose. This
        method serves both paths - a person typed the answer, or Codex's
        Decision Gate generated it - and the whole audit claim is the ability
        to tell those apart. A default would let a third caller added later
        inherit the wrong one, producing an audit record that says a human
        reviewed something no human saw.
        """
        if answered_by not in ("user", "codex"):
            raise ValueError(
                f"answered_by 必須是 'user' 或 'codex'，收到 {answered_by!r}。"
                "稽核紀錄靠這個欄位分辨「人看過」與「Codex 自己批准」。"
            )
        # **續給誰**：呼叫端指名的那一格，指不出來才自己找做事的那一格。
        # 寫死 `ROLE_AUTHOR` 的話，使用者改過名的陣容在「他剛打完字按下送出」
        # 那一刻炸掉——**那是整條線上他最不預期出錯的一步**。
        #
        # **`required_` 不是 `fallback=`**：退回 `author` 只是把同一句話往後
        # 挪一行（他的陣容裡也沒有 `author`）。排不到的正確收場寫在呼叫端
        # （`workflow_worker._work_loop` 續跑那一段）：這一次派工還沒發生，
        # 所以停下來講「這條線上沒有一格會寫檔案」（`NO_WORKING_SEAT`）。
        # 這裡丟例外是最後一道門。
        seat = role.strip().lower() or self.required_seat_with_duty(DUTY_WORK)
        task = self.task_path.read_text(encoding="utf-8")

        prompt = prompt_builder.build_user_answer_prompt(
            task=task,
            question=question,
            answer=answer,
            safety_instruction=self._claude_safety_instruction(),
            output_instruction=self._output_instruction(),
            identity=self._identity_for(seat),
        )
        self.timeline.append(
            self.run_id,
            "user",
            "answer",
            f"問題：{question}\n\n答案：{answer}",
        )
        # The audit half. The timeline entry above has always existed, but it
        # lives in that run's own file mixed in with every CLI line - so the
        # decision log recorded 「我們問了」 and never 「他們答了什麼」, which is
        # precisely the half 「證明有經過人員審核」 needs.
        #
        # Only the human path is written here. The Codex path logs its own
        # CONTINUE entry at the gate, before calling this - logging again here
        # would double-count it, and worse, under a field that is supposed to
        # mean a person looked.
        if answered_by == "user":
            self.decision_log.append(
                DecisionLogEntry(
                    run_id=self.run_id,
                    action="ANSWERED",
                    question=question,
                    answer=answer,
                    source="user",
                    answered_by="user",
                )
            )
        return self._run_role_prompt(seat, prompt, "user_answer")

    def run_codex_decision_gate(
        self,
        question: str,
        source: str = "claude",
    ) -> CliResult:
        # 決策關卡是「代替人回答」，所以派給複查那一種職責——它是唯讀的，
        # 而且是陣容裡最後一格複查（後面的看過前面的）。
        #
        # **`required_` 不是 `fallback=`**：這一步的形狀就是那個職責，沒有
        # 「排不到的時候退回誰」這個答案。舊寫法退回 `reviewer`，那是使用者
        # 改過名的陣容裡不存在的名字，於是第五次派工之後整輪收成
        # 「這個專案的陣容裡沒有「reviewer」這一格」——他問的那一題連看都
        # 沒看到。**排不到的正確收場寫在唯一的呼叫端**
        # （`resolve_claude_question_with_codex`）：這一題本來就要問人，
        # 所以跳過這扇門、直接問他。這裡丟例外是最後一道門，不是那條路：
        # 它擋的是「明天多一個呼叫端忘了先問」。
        #
        # 擺在最前面，是為了在 `_memory_context()`（讀八份共同記憶）之前就停。
        seat = self.required_seat_with_duty(DUTY_REVIEW)
        task = self.task_path.read_text(encoding="utf-8")
        # 沒有字數參數了：決策閘門判斷的正是「這件事該不該問使用者」，而那張
        # 「不准自己決定」的清單就在 OPEN_QUESTIONS.md 裡。以前這裡寫 20000，
        # 隔壁寫 40000，另外兩處寫 12000 與 50000——四個數字沒有一個說得出
        # 為什麼是那個數字，而它們決定的是安全規則被切在第幾個字。
        memory_context = self._memory_context()
        sensitivity = ProjectRegistry.get_sensitivity(self.project_path)
        prompt = prompt_builder.build_decision_gate_prompt(
            task=task,
            source=source,
            question=question,
            memory_context=memory_context,
            sensitivity=sensitivity,
            gate_on=self.settings.gate_on,
        )
        self.timeline.append(
            self.run_id,
            "aicc",
            "decision_gate_requested",
            t("step.question_to_gate"),
        )
        return self._run_role_prompt(
            seat,
            prompt,
            event_type="decision_gate",
            read_only=True,
            title_prefix="Codex Decision Gate",
        )

    def resolve_claude_question_with_codex(
        self,
        question: str,
        source: str = "claude",
    ) -> dict:
        # 決策關卡派給「複查」那一格（`run_codex_decision_gate`）。排不到的
        # 時候**直接問人**，不要往下派。
        #
        # 2026-08-26 實機量到（他的五格，序列換成開發序列，做事那一格問了
        # 一題）：`seat_with_duty(DUTY_REVIEW, fallback=ROLE_REVIEWER)` 退回
        # `reviewer`，於是第五次派工之後整輪收成
        #
        #   NotImplementedError: 這個專案的陣容裡沒有「reviewer」這一格⋯⋯
        #
        # **這一題本來就是要問人的。**這扇門的用途是「先讓一格複查替你判斷
        # 這題要不要打擾你」——沒有那一格的時候，正確答案不是停下整輪，
        # 更不是挑一格代打（代打的那一格會替使用者回答一個他沒有授權的問題，
        # 而畫面上看起來像有人審過），是**把問題原封不動交給他**。
        #
        # 那正是這一支對「關卡本身失敗」的答案（下面三行），所以這裡走同一條路。
        if not self.seat_with_duty(DUTY_REVIEW):
            # **跳過的那一步要在時間軸上留一筆。**這是三種收場裡唯一「本來
            # 就可以不做」的那一種，而沒有留痕的跳過事後跟「有跑過而且通過」
            # 長得一模一樣：`tools/inspect_run.py` 讀的是這條時間軸，
            # 使用者事後問「那一題到底有沒有人先看過」的時候，
            # 答案只能從這裡來。
            #
            # 英文，跟隔壁 `memory_update_no_seat` 同一個理由：這一行是給
            # 紀錄與工具讀的代號，**使用者讀到的中文是下面 `reason` 那一句**
            # （`gate.no_seat_to_judge`，由具名的鍵挑）。
            self.timeline.append(
                self.run_id,
                "aicc",
                "decision_gate_no_seat",
                f"no seat has duty {DUTY_REVIEW!r}; asked the user instead",
            )
            return {
                "action": "ASK_USER",
                "question": question,
                "reason": t(
                    "gate.no_seat_to_judge",
                    count=len(self.lineup),
                    seats="、".join(slot.role for slot in self.lineup),
                ),
                "codex_result": None,
            }

        gate_result = self.run_codex_decision_gate(question, source=source)
        if not gate_result.success:
            return {
                "action": "ASK_USER",
                "question": question,
                "reason": gate_result.stderr or gate_result.stdout,
                "codex_result": gate_result,
            }

        decision = self.parse_decision_gate(gate_result.stdout)
        if decision["decision"] == "ASK_USER":
            self.decision_log.append(
                DecisionLogEntry(
                    run_id=self.run_id,
                    action="ASK_USER",
                    question=decision.get("question") or question,
                    reason=decision.get("reason", ""),
                    source=source,
                )
            )
            return {
                "action": "ASK_USER",
                "question": decision.get("question") or question,
                "reason": decision.get("reason", ""),
                "codex_result": gate_result,
            }

        # The floor, enforced rather than requested (ROADMAP P3). `GATE_FLOOR`
        # is built into the gate prompt, which means it works by Codex
        # agreeing with it. This is the same floor checked on the way back: if
        # the instruction Codex is about to hand Claude force-pushes, drops a
        # table or carries a secret, the run stops for a person whatever the
        # gate decided.
        #
        # Escalating, never approving: a veto can only turn CONTINUE into
        # ASK_USER. There is no path here that lets a check *permit* something
        # the gate refused.
        veto = orchestrator_parsing.gate_veto(
            question,
            decision.get("answer", ""),
            decision.get("reason", ""),
        )
        if veto:
            escalated = (
                f"AICC 自動升級：{veto}。"
                f"Codex 判斷可以自行處理（理由：{decision.get('reason', '')}），"
                "但這件事屬於一律要人確認的範圍，所以還是問你。"
            )
            self.decision_log.append(
                DecisionLogEntry(
                    run_id=self.run_id,
                    action="ASK_USER",
                    question=decision.get("question") or question,
                    reason=escalated,
                    source=source,
                )
            )
            self.timeline.append(
                self.run_id,
                "aicc",
                "decision_gate_vetoed",
                escalated,
            )
            return {
                "action": "ASK_USER",
                "question": decision.get("question") or question,
                "reason": escalated,
                "codex_result": gate_result,
            }

        answer = (
            "Codex Decision Gate 判斷："
            f"{decision['decision']}\n"
            f"理由：{decision.get('reason', '')}\n"
            f"指示：{decision.get('answer', '')}"
        )
        # Codex answered this one itself - the CONTINUE entry below is the
        # audit record for it, and it must not also be filed as a human review.
        claude_result = self.run_claude_with_user_answer(
            question, answer, answered_by="codex"
        )
        self.timeline.append(
            self.run_id,
            "aicc",
            "decision_gate_auto_answered",
            t("step.gate_answered"),
            metadata={
                "decision": decision["decision"],
                "reason": decision.get("reason", ""),
            },
        )
        self.decision_log.append(
            DecisionLogEntry(
                run_id=self.run_id,
                action="CONTINUE",
                question=question,
                reason=decision.get("reason", ""),
                source=source,
                answer=decision.get("answer", ""),
            )
        )
        return {
            "action": "CONTINUE",
            "question": "",
            "reason": decision.get("reason", ""),
            "codex_result": gate_result,
            "claude_result": claude_result,
            "decision": decision,
        }

    def run_claude_fix(
        self,
        review_data: dict,
        attempt_number: int,
        role: str = ROLE_AUTHOR,
    ) -> CliResult:
        task = self.task_path.read_text(encoding="utf-8")

        prompt = prompt_builder.build_fix_prompt(
            task=task,
            attempt_number=attempt_number,
            summary=review_data.get("summary", ""),
            issues=review_data.get("issues", []),
            instructions=review_data.get("instructions", []),
            safety_instruction=self._claude_safety_instruction(),
            output_instruction=self._output_instruction(),
            identity=self._identity_for(role),
        )
        return self._run_role_prompt(
            role,
            prompt,
            "fix",
            metadata={"attempt_number": attempt_number},
        )

    def verification(self) -> VerifyCommandLookup:
        """What this project says its verification step is.

        Read fresh each time rather than cached at construction: the answer
        to 「沒宣告」 is 「請先設定」, and a user who then sets it should not
        have to restart the app for the next run to see it.

        **A missing declaration is written into the run record here**, at
        the one place every caller passes through, rather than at the one
        that happened to be discovered first. `run_tests` used to log it and
        the up-front guard in `WorkflowWorker._run_development` did not - so
        the *common* ending (a project that never declared anything, caught
        before anything is dispatched) left a run folder with no
        `timeline.jsonl` at all: a run that just stops, with the sentence
        naming the file, the field and the fix nowhere a human can read it.
        """
        lookup = read_verify_command(self.project_path)
        if lookup.command is None:
            self._record_verification_not_declared(lookup.problem)
        return lookup

    def _record_verification_not_declared(self, problem: str) -> None:
        self.timeline.append(
            self.run_id,
            "aicc",
            "verification_not_declared",
            problem,
            metadata={"tests_status": TESTS_NOT_DECLARED},
        )
        # And in run.json, which is what `inspect_run.py` reads first.
        self.timeline.update_run(
            self.run_id,
            extra={"verification_problem": problem},
        )

    def run_tests(self) -> VerificationOutcome:
        lookup = self.verification()
        if lookup.command is None:
            # 「沒得驗」 is not 「驗了但是紅的」. Returning a failed result here
            # would put an un-earned red light into the same channel a real
            # failure uses, and the caller would send the author off to fix
            # code that nothing has found fault with. (`verification()` has
            # already written the reason into the run record.)
            raise VerificationNotDeclared(lookup.problem)

        command = lookup.command
        result = self.runner.run(
            command.argv,
            timeout_seconds=command.timeout_seconds,
        )

        if result.never_started:
            # Nothing was executed, so there is no verdict to reach. The
            # `return_code` on this result is a placeholder -1 that
            # `SuccessRule.judge` would faithfully report as 「離開碼 -1 不在
            # 宣告的通過清單內」 → FAILED, and the workflow would then spend
            # its single paid fix attempt on code no one has found fault
            # with. Same ending as an undeclared command instead: stop and
            # ask. This check comes **before** the judge deliberately - the
            # judge has no way to tell an invented exit code from a real one.
            problem = never_started_problem(command.display, result)
            self._record_verification_not_declared(problem)
            raise VerificationNotDeclared(problem)

        # 「算不算過」 is the project's sentence, not the engine's. The engine
        # used to answer it with `returncode == 0`, which is wrong for this
        # very repo: the suite prints 「1224 passed」 and then exits
        # 0xC0000005 as QtWebEngine tears down.
        if result.timed_out:
            verdict = timed_out_verdict(command.timeout_seconds)
        else:
            verdict = command.success_rule.judge(
                result.return_code, combined_output(result)
            )

        output = result.stdout or result.stderr
        self.timeline.append(
            self.run_id,
            "aicc",
            "test_result",
            output or f"{command.display} 沒有輸出。",
            metadata={
                "success": verdict.passed,
                "tests_status": verdict.status,
                "verdict_reason": verdict.reason,
                "success_rule": command.success_rule.describe,
                "return_code": result.return_code,
                "command": command.display,
            },
        )
        self.timeline.save_artifact(
            self.run_id,
            "test-output.txt",
            (result.stdout + "\n" + result.stderr).strip(),
        )
        return VerificationOutcome(verdict=verdict, result=result)

    def get_git_diff(self) -> str:
        """這一輪做出來的東西，**包含新建的檔案**。

        `git diff` 只比對 git 已經認得的檔案。一格做的事全部是新增檔案的
        時候（開一個新模組、寫一份新文件——很常見），diff 是空字串，拍板
        那一格讀到「這一輪沒有產出可以比對」，然後**照樣蓋章**。真的做了事
        跟完全沒做事，在最後一關長得一模一樣。
        """
        completed = subprocess.run(
            [
                "git",
                "diff",
                "--no-ext-diff",
                self._diff_base(),
                "--",
                ".",
                *generated_path_excludes(),
            ],
            cwd=self.project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            creationflags=NO_WINDOW,
        )

        if completed.returncode != 0:
            diff = "GIT_DIFF_ERROR:\n" + (
                completed.stderr or completed.stdout
            ).strip()
        else:
            diff = completed.stdout.strip()
            new_files = self._new_files_report()
            if new_files:
                diff = f"{diff}\n\n{new_files}" if diff else new_files

        self.timeline.save_artifact(
            self.run_id,
            "git-diff.patch",
            diff,
        )
        self.timeline.append(
            self.run_id,
            "aicc",
            "git_diff",
            (
                f"已取得 Git diff，共 {len(diff.splitlines())} 行。"
                if diff
                else t("git.diff_empty")
            ),
        )
        return diff

    def _diff_base(self) -> str:
        """這一輪的產出要跟**哪一個版本**比。

        `git diff` 不給參數比的是「工作區 vs 索引」——`git add` 過的東西在
        它眼裡已經沒有差異了。廠商的 CLI 邊做邊 `git add` 是常態，於是一格
        真的寫完了程式、順手 add 起來，這裡回空字串，拍板那一格讀到
        「這一輪沒有產出可以比對」然後照樣蓋章。**跟未追蹤檔案那個洞是同一
        種病**：真的做了事跟完全沒做事，在最後一關長得一模一樣。`HEAD` 比的
        是「工作區 vs 最後一次 commit」，暫存起來的跟還沒暫存的都算進去。

        還沒有任何一次 commit 的專案沒有 `HEAD`，`git diff HEAD` 會直接失敗
        ——而失敗那條路上**整段新檔案報告根本不會被組出來**，於是一個全新
        專案的第一輪（產出必然全部是新檔案）會退回一句錯誤訊息。那種專案
        改跟 `--cached` 比。
        """
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
            cwd=self.project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            creationflags=NO_WINDOW,
        )
        return "HEAD" if completed.returncode == 0 else "--cached"

    def _untracked_files(self) -> list[str]:
        """git 還不認得的檔案——也就是這一輪新建出來的那些。

        用 `-z`：`git ls-files` 預設會把非 ASCII 檔名印成 `"\\346\\226\\260"`
        這種跳脫字串，而那個檔名接下來要拿去讀檔。
        """
        completed = subprocess.run(
            [
                "git",
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                ".",
                *generated_path_excludes(),
            ],
            cwd=self.project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            creationflags=NO_WINDOW,
        )
        if completed.returncode != 0:
            return []
        return sorted(
            entry.replace("\\", "/")
            for entry in completed.stdout.split("\0")
            if entry.strip()
        )

    def _new_files_report(self) -> str:
        """新建的檔案長什麼樣子，寫成一段能接在 diff 後面的文字。

        接在**同一段文字**裡而不是另外開一個欄位：拍板那一格讀的是
        `deliverable` 這一個字串，多出來的欄位它不會去讀，而沒有人會發現。
        每一段都標著「新增檔案」，所以「改了」跟「新增了」分得出來。
        """
        paths = self._untracked_files()
        if not paths:
            return ""

        blocks = [t("git.new_files.header", count=len(paths))]
        budget = NEW_FILE_TOTAL_CHARS
        for path in paths:
            block, used = self._new_file_block(path, budget)
            budget -= used
            blocks.append(block)
        return "\n\n".join(blocks)

    def _new_file_block(self, path: str, budget: int) -> tuple[str, int]:
        """一個新檔案那一段，以及它用掉的字數。

        截斷一律要講。**「只放前面幾行」跟「這個檔案就這麼短」在下游長得
        一模一樣**——而下游是拿它來判斷這一輪做了多少事的那一格。

        有一種檔案是**檔名留著、內容一個字都不送**：憑證與執行期狀態
        （`withhold_reason()`）。這一段文字會被貼進派給廠商的提示詞，而
        `SECURITY_MODEL.md` 對那一類的規定是「永不同步或匯出，也不准用
        『先遮蔽再送出』達成」。整條刪掉也不行——那一輪真的產生的檔案，
        在最後一關會等於不存在。
        """
        withheld = withhold_reason(path)
        if withheld:
            return t(
                WITHHOLD_TEXT_KEYS.get(withheld, "git.new_file.secret"),
                path=path,
            ), 0

        try:
            data = (self.project_path / path).read_bytes()
        except OSError as error:
            return t("git.new_file.unreadable", path=path, error=error), 0

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return t("git.new_file.binary", path=path, size=len(data)), 0

        if budget <= 0:
            return (
                t("git.new_file.no_room", path=path, chars=len(text)),
                0,
            )

        lines = text.splitlines()
        body = "\n".join(lines[:NEW_FILE_MAX_LINES])
        notes = ""
        if len(lines) > NEW_FILE_MAX_LINES:
            notes += t(
                "git.new_file.cut_lines",
                shown=NEW_FILE_MAX_LINES,
                dropped=len(lines) - NEW_FILE_MAX_LINES,
            )

        limit = min(NEW_FILE_MAX_CHARS, budget)
        if len(body) > limit:
            notes += t(
                "git.new_file.cut_chars",
                shown=limit,
                dropped=len(body) - limit,
            )
            body = body[:limit]

        header = t(
            "git.new_file.header",
            path=path,
            lines=len(lines),
            chars=len(text),
        )
        return f"{header}{notes} ---\n{body}", len(body)

    def get_changed_files(self) -> list[str]:
        """這一輪動到哪些檔案。**檔名要是檔名，不是跳脫字串。**

        `core.quotepath` 出廠是開著的，於是一行一行讀 `git status --short`
        會把非 ASCII 檔名讀成 `"\\350\\207\\252\\347\\224\\250..."`。這份清單
        有兩個讀者：使用者，以及下一格的提示詞。中文檔名的專案（這一個就是）
        會拿到一張看不出來是哪個檔案的單子，而 `is_generated_path()` 也永遠
        比不中那種字串。

        `-z` 是 git 自己給機器讀的格式：檔名原字、用 `\\0` 分隔、一個字都不
        跳脫。**刻意不用 `git -c core.quotepath=false`**——那個寫法會讓
        `tests/test_aicc_runs_nothing_destructive.py` 讀到的子指令變成 `-c`，
        而那條檢查在守的是「AICC 不自動改使用者的 repo」。為了自己方便去鬆
        一條安全檢查，比這個問題本身貴。
        """
        completed = subprocess.run(
            ["git", "status", "--short", "-z"],
            cwd=self.project_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            creationflags=NO_WINDOW,
        )

        if completed.returncode != 0:
            return []

        changed_files: list[str] = []
        entries = completed.stdout.split("\0")
        position = 0
        while position < len(entries):
            entry = entries[position]
            position += 1
            if len(entry) < 4:
                continue
            status_code, file_path = entry[:2], entry[3:].strip()
            # 改名／複製那一筆，`-z` **不寫 `->`**：舊路徑在下一格，
            # 而這一格已經是新路徑（跟 `舊 -> 新` 左右相反）。少了這一跳，
            # 舊路徑會被當成另一個「這一輪動到的檔案」列上去——**一個已經
            # 不存在的檔案，列在「他這一輪做了什麼」上面。**
            if "R" in status_code or "C" in status_code:
                position += 1
            # 排除清單跟 `get_git_diff()` 是**同一份**（`is_generated_path`）。
            if is_generated_path(file_path):
                continue
            if file_path:
                changed_files.append(file_path)

        self.timeline.append(
            self.run_id,
            "aicc",
            "changed_files",
            t("git.changed_files") + (
                "\n".join(f"- {item}" for item in changed_files)
                if changed_files
                else t("common.none_bullet")
            ),
        )
        return changed_files

    def run_codex_review(
        self,
        claude_result: CliResult,
        test_result: VerificationOutcome,
        git_diff: str,
        changed_files: list[str],
        role: str = ROLE_REVIEWER,
    ) -> CliResult:
        task = self.task_path.read_text(encoding="utf-8")
        knowledge_context = self._knowledge_context(task, role)

        review_package = {
            "task": task,
            "claude_success": claude_result.success,
            "claude_output": claude_result.stdout,
            "claude_error": claude_result.stderr,
            "tests_success": test_result.success,
            "test_stdout": test_result.stdout,
            "test_stderr": test_result.stderr,
            "changed_files": changed_files,
            "git_diff": git_diff,
            "project_context": knowledge_context,
        }

        # 這個專案有沒有質化驗收標準。**沒有就是空字串，不是一段場面話**
        # ——「請依一般標準判斷」那種句子會讓它以為自己有標準可以依。
        from app.services import qualitative_acceptance

        rubric = qualitative_acceptance.read(self.project_path)
        rubric_text = (
            prompt_builder.build_acceptance_block(rubric)
            if rubric is not None and not qualitative_acceptance.problems_with(rubric)
            else ""
        )

        prompt = prompt_builder.build_review_prompt(
            review_package=review_package,
            acceptance_rubric=rubric_text,
            identity=self._identity_for(role),
            # 要它指名退給誰，就得先讓它知道有誰。不給名冊而要求指名，
            # 它只能猜一個名字，而猜錯的名字在畫面上跟猜對的長得一樣。
            seats=tuple((slot.role, slot.duty) for slot in self.lineup),
        )
        prompt = self._prompt_with_bootstrap(
            prompt,
            task,
            provider="codex",
            has_session=bool(self.session_for_role(role)),
            role=role,
        )

        self.timeline.save_artifact(
            self.run_id,
            "codex-review-package.json",
            json.dumps(review_package, ensure_ascii=False, indent=2),
        )
        self.timeline.append(
            self.run_id,
            "aicc",
            "codex_review_requested",
            t("step.sent_for_review"),
            session_id=self.codex_session_id,
        )

        result = self._run_role_prompt(
            role,
            prompt,
            event_type="review",
            read_only=True,
            title_prefix="Codex Reviewer",
        )
        self.timeline.save_artifact(
            self.run_id,
            "codex-output.txt",
            result.stdout,
        )
        if result.raw_stdout:
            self.timeline.save_artifact(
                self.run_id,
                "codex-events.jsonl",
                result.raw_stdout,
            )
        return result

    def run_codex_memory_update(
        self,
        claude_result: CliResult,
        codex_review: CliResult,
        git_diff: str,
        changed_files: list[str],
    ) -> CliResult:
        task = self.task_path.read_text(encoding="utf-8")
        current_memory = self._memory_context()
        package = {
            "task": task,
            "changed_files": changed_files,
            "git_diff": git_diff,
            "claude_output": claude_result.stdout,
            "codex_review": codex_review.stdout,
            "current_memory": current_memory,
        }
        # **不是 `ROLE_REVIEWER`，也不是 `fallback=`。**寫死名字的話，使用者
        # 改過名的陣容會在這裡炸掉——而這一步跑在整輪的最後，五次派工的額度
        # 都已經花完。退回一個出廠名字是同一件事往後挪一行：`reviewer` 一樣
        # 不在他的陣容裡。
        #
        # **排不到的正確收場寫在唯一的呼叫端**（`maintain_memory_after_pass`
        # 上面那一段）：這一輪跑完了、只差這一步，所以收成
        # `MEMORY_NO_SEAT_FOR_DUTY`（「跑完了，但共同記憶沒有人維護」），
        # **不是**「這一輪沒有開始」——挑錯那一句，他會整輪重跑一次。
        # 這裡丟例外是最後一道門：擋的是明天多一個呼叫端忘了先問。
        seat = self.required_seat_with_duty(DUTY_REVIEW)
        prompt = prompt_builder.build_memory_update_prompt(
            package=package,
            # `_identity_for` 查不到位子是回空字串**不是丟例外**，所以寫死
            # 名字在這裡不會紅，只會安靜地把使用者替那一格打的「你是⋯⋯」
            # 整句丟掉、換回出廠人設。**那比炸掉更難發現。**
            identity=self._identity_for(seat),
        )
        self.timeline.append(
            self.run_id,
            "aicc",
            "memory_update_requested",
            t("step.memory_drafting"),
        )
        return self._run_role_prompt(
            seat,
            prompt,
            event_type="memory_update",
            read_only=True,
            title_prefix="Codex Memory",
        )

    def run_claude_memory_review(
        self,
        memory_update: str,
    ) -> CliResult:
        task = self.task_path.read_text(encoding="utf-8")
        # 同上一支的理由。只修一支的話，traceback 往後挪二十行、
        # 訊息裡的名字從「reviewer」換成「author」，畫面一樣紅。
        #
        # 缺這一種的收場**跟缺複查是同一種**（跑完了、只差這一步），但講的
        # 不是同一句：缺複查是沒有人擬草稿，缺執行是草稿擬得出來而沒有人審
        # 它——而這個產品不會把一份沒有人看過的東西寫進八份共同記憶。
        # 挑哪一句由 `workflow_worker.why_memory_was_not_maintained()` 決定。
        seat = self.required_seat_with_duty(DUTY_WORK)
        prompt = prompt_builder.build_memory_review_prompt(
            task=task,
            memory_update=memory_update,
            identity=self._identity_for(seat),
        )
        return self._run_role_prompt(
            seat,
            prompt,
            event_type="memory_review",
            read_only=True,
        )

    def maintain_memory_after_pass(
        self,
        claude_result: CliResult,
        codex_review: CliResult,
        git_diff: str,
        changed_files: list[str],
    ) -> dict:
        if not self.settings.auto_memory_maintenance:
            return {"status": "SKIPPED", "files": [], "question": ""}
        if self._should_skip_memory_maintenance(changed_files):
            self.timeline.append(
                self.run_id,
                "aicc",
                "memory_update_skipped",
                "Shared memory update skipped for low-impact files.",
            )
            return {
                "status": "SKIPPED",
                "files": [],
                "question": "",
                "summary": "Skipped for low-impact file changes.",
            }

        # 維護共同記憶是**兩次派工**：草稿給「複查」那一格（下面
        # `run_codex_memory_update`），審它給「執行」那一格
        # （`run_claude_memory_review`）。排不到的時候**這裡就要停**。
        #
        # 2026-08-26 實機量到（他的五格：三格擬做法、一格執行、一格核准，
        # **沒有一格複查**，序列換成開發序列——那正是計畫序列排不到複查的
        # 時候，畫面請他走的第二條路）：
        #
        #   AttributeError: 'NoneType' object has no attribute 'stdout'
        #
        # 沒有一格複查的時候 `steps/reviewing.look()` 回 `NO_REVIEWING_SEAT`，
        # 而 2026-08-24 起那不再停下整輪——`codex_result` 是 `None`，一路
        # 傳到這裡當成 `CliResult` 用。把那一個補起來也只是往前挪一行：
        # 下一行 `seat_with_duty(DUTY_REVIEW, fallback=ROLE_REVIEWER)` 會退回
        # `reviewer`，那是一個他的陣容裡不存在的名字，於是
        #
        #   NotImplementedError: 這個專案的陣容裡沒有「reviewer」這一格⋯⋯
        #
        # **也就是這一整輪要消滅的那一句話**——只是它躲在整輪的最後一步，
        # 七次派工的額度都花完之後才炸。
        #
        # 不丟 `NoSeatWithDuty`：那個例外收成「這一輪沒有開始」，而這一輪
        # 跑完了——程式寫了、測試過了、拍板也蓋了。回一個狀態讓呼叫端說
        # 出真正的那一句：**做完了，但共同記憶沒有人維護。**
        missing = next(
            (
                duty
                for duty in (DUTY_REVIEW, DUTY_WORK)
                if not self.seat_with_duty(duty)
            ),
            "",
        )
        if missing:
            self.timeline.append(
                self.run_id,
                "aicc",
                "memory_update_no_seat",
                f"no seat has duty {missing!r}; memory left untouched",
            )
            return {
                "status": "NO_SEAT",
                "duty": missing,
                "seats": tuple(slot.role for slot in self.lineup),
                "files": [],
                "question": "",
            }

        codex_memory = self.run_codex_memory_update(
            claude_result=claude_result,
            codex_review=codex_review,
            git_diff=git_diff,
            changed_files=changed_files,
        )
        if not codex_memory.success:
            return {
                "status": "ERROR",
                "files": [],
                "question": "",
                "summary": codex_memory.stderr or codex_memory.stdout,
            }

        self.timeline.save_artifact(
            self.run_id,
            "memory-update-proposal.md",
            codex_memory.stdout,
        )

        claude_review = self.run_claude_memory_review(codex_memory.stdout)
        if not claude_review.success:
            return {
                "status": "ERROR",
                "files": [],
                "question": "",
                "summary": claude_review.stderr or claude_review.stdout,
            }

        review = self.parse_memory_review(claude_review.stdout)
        if not review.get("parsed", True):
            # **讀不懂複查的回覆，不准當成通過。**底下那一段會把 Codex 的
            # 草稿整檔寫進八份共同記憶——而那份草稿此刻**沒有人審過**。
            #
            # 草案不會掉：`memory-update-proposal.md` 已經存檔，
            # 使用者只是要按一下。停在這裡的代價是一個對話框，
            # 不停的代價是 `SECURITY_MODEL.md` 被一份沒人看過的東西蓋掉。
            return {
                "status": "ASK_USER",
                "files": [],
                "question": t("memory.review_unreadable"),
                "summary": review.get("summary", ""),
            }
        if review["status"] == "ASK_USER":
            question = review.get("question", "").strip()
            return {
                "status": "ASK_USER",
                "files": [],
                "question": question or review.get("summary", ""),
            }

        memory_text = review.get("memory_update", "").strip()
        if not memory_text:
            memory_text = codex_memory.stdout

        safety = self.knowledge.scan_memory_update_safety(memory_text)
        self.timeline.save_artifact(
            self.run_id,
            "memory-update-diff.patch",
            self.knowledge.preview_memory_update_diff(memory_text),
        )
        if not safety.get("safe", False):
            summary = str(safety.get("summary", "Memory update needs review."))
            return {
                "status": "ASK_USER",
                "files": [],
                "question": (
                    "Shared memory update may contain sensitive or high-risk "
                    f"content. Please review before applying.\n\n{summary}"
                ),
                "summary": summary,
            }

        # This text is run_codex_memory_update()'s output - Codex is the
        # memory maintainer (see DECISIONS.md "AI Role Decisions").
        written = self.knowledge.write_memory_from_text(memory_text, author="codex")
        self.knowledge.export_manifest()
        self.timeline.save_artifact(
            self.run_id,
            "memory-update-applied.md",
            memory_text,
        )
        self.timeline.append(
            self.run_id,
            "aicc",
            "memory_update_applied",
            t("step.memory_updated")
            + (", ".join(written) if written else t("common.none")),
        )
        return {
            "status": "UPDATED",
            "files": list(written),
            "question": "",
            "summary": review.get("summary", ""),
        }

    @staticmethod
    def _should_skip_memory_maintenance(changed_files: list[str]) -> bool:
        return orchestrator_parsing.should_skip_memory_maintenance(
            changed_files
        )

    def run_codex_plan(
        self,
        task: str,
        previous_plan: str = "",
        claude_feedback: str = "",
        round_number: int = 0,
        final_round: bool = False,
    ) -> CliResult:
        # 計畫序列這兩步的預設出廠陣容是 author/reviewer，而使用者排的
        # 五格自訂名字裡不見得有那兩個名字。寫死名字的代價在這條路上最大。
        #
        # **`workflow_mode` 出廠 2026-08-27 起是 `development`**，所以這條路
        # 不再是「什麼都不改按下開始」會走到的那一條——它現在要使用者
        # 自己把序列切成計畫序列。那降低了撞上這件事的機率，**沒有降低代價**：
        # 走到這裡的時候，他的陣容一樣可能一格 `reviewer` 都沒有。
        #
        # **`required_` 不是 `fallback=`**（2026-08-26 換掉）：這條序列的
        # 每一步都派給複查，所以「排不到的時候退回誰」這一題沒有答案。
        # 舊寫法退回 `reviewer`，而那是一個他的五格裡不存在的名字——
        # 他看到的是「陣容裡沒有 reviewer 這一格，請重新編組」，
        # 於是去改名字，而壞的不是名字。見 `NoSeatWithDuty`。
        seat = self.required_seat_with_duty(DUTY_REVIEW)
        knowledge_context = self._knowledge_context_for_round(
            task, first_round=round_number == 0
        )
        prompt = prompt_builder.build_plan_prompt(
            task=task,
            knowledge_context=knowledge_context,
            previous_plan=previous_plan,
            claude_feedback=claude_feedback,
            round_number=round_number,
            final_round=final_round,
            output_instruction=self._output_instruction(),
            identity=self._identity_for(seat),
        )
        prompt = self._prompt_with_bootstrap(
            prompt,
            task,
            provider="codex",
            has_session=bool(self.session_for_role(seat)),
            role=seat,
        )
        return self._run_role_prompt(
            seat,
            prompt,
            event_type=("planning_final" if final_round else "planning"),
            read_only=True,
            title_prefix="Codex Planner",
        )

    def run_claude_plan_review(
        self,
        task: str,
        codex_plan: str,
        round_number: int,
    ) -> CliResult:
        # 同 `run_codex_plan`：審計畫這一步的形狀就是「執行」那一格，
        # 排不到沒有第二個人選。退回 `author` 的話，一份沒有排執行的陣容
        # 會在第一輪討論的時候拿到「陣容裡沒有 author 這一格」。
        seat = self.required_seat_with_duty(DUTY_WORK)
        knowledge_context = self._knowledge_context_for_round(
            task, first_round=round_number == 1
        )
        prompt = prompt_builder.build_plan_review_prompt(
            task=task,
            codex_plan=codex_plan,
            knowledge_context=knowledge_context,
            round_number=round_number,
            output_instruction=self._output_instruction(),
            identity=self._identity_for(seat),
        )
        prompt = self._prompt_with_bootstrap(
            prompt,
            task,
            provider="claude",
            has_session=bool(self.session_for_role(seat)),
            role=seat,
        )
        return self._run_role_prompt(
            seat,
            prompt,
            event_type="planning_review",
            metadata={"round_number": round_number},
            read_only=True,
        )

    # Every argument of every vendor call passes through here, and a
    # multi-line one is refused before the runner ever sees it. The rule
    # itself is not new - CliRunner has enforced it since the shim work -
    # but it lived where the tests could not reach it: unit tests swap
    # CliRunner for a fake, so the whole suite stayed green while the
    # engine's very first step (`claude -p <a multi-line prompt>`) could
    # not run on this machine at all. Stated here, on the engine's own
    # side of the seam, putting a prompt back into argv turns every
    # vendor-call test red instead of only a real run.
    #
    # This raises rather than returning a failed CliResult on purpose:
    # a payload in argv is a mistake in AICC, not a failure of the tool
    # being called, and dressing it up as a failed call is precisely the
    # kind of false red light the rest of this file exists to prevent.
    # WorkflowWorker.run catches it and reports the traceback, so a
    # released build says something instead of dying quietly.
    @staticmethod
    def _refuse_a_payload_in_argv(arguments: list[str]) -> None:
        for index, item in enumerate(arguments):
            if "\r" in item or "\n" in item or "\x00" in item:
                raise ValueError(
                    f"AICC 想把第 {index} 個命令列參數送給 "
                    f"「{arguments[0]}」，但它含有換行或 NUL。"
                    "Windows 的命令列裝不下換行，送出去只會到達第一行。"
                    "整段內容要走標準輸入（input_text），不是 argv。"
                )

    def _send_prompt_to_vendor(
        self,
        arguments: list[str],
        prompt: str,
        *,
        timeout_seconds: int | float | None,
    ) -> CliResult:
        """The one door a vendor prompt leaves the engine through.

        The prompt goes on standard input for both providers; argv
        carries only flags.
        """
        self._refuse_a_payload_in_argv(arguments)
        return self.runner.run(
            arguments,
            timeout_seconds=timeout_seconds,
            input_text=prompt,
        )

    def _run_codex_prompt(
        self,
        prompt: str,
        event_type: str,
        permission: str,
        title_prefix: str,
        metadata: dict | None = None,
        role: str = ROLE_REVIEWER,
    ) -> CliResult:
        """Run one Codex turn on behalf of `role`.

        `metadata` is accepted so both providers answer to the same call shape
        from _run_role_prompt; Codex's own fields are merged over it.
        """
        session_id = self.session_for_role(role)
        self.timeline.append(
            self.run_id,
            "user",
            f"codex_{event_type}",
            prompt.strip(),
            session_id=session_id,
            metadata={
                **dict(metadata or {}),
                "model": self.settings.codex_model,
                "effort": self.settings.codex_effort,
                "verbosity": self.settings.codex_verbosity,
                "permission": permission,
            },
        )

        arguments = [
            "codex",
            "exec",
            "-m",
            self.settings.codex_model,
            "-c",
            f'model_reasoning_effort="{self.settings.codex_effort}"',
            "-c",
            f'model_verbosity="{self.settings.codex_verbosity}"',
            "-s",
            permission,
            "--json",
        ]

        if session_id:
            arguments.extend(
                [
                    "resume",
                    session_id,
                    "-",
                ]
            )
        else:
            arguments.append("-")

        raw_result = self._send_prompt_to_vendor(
            arguments,
            prompt,
            timeout_seconds=600,
        )
        result = self._normalize_codex_result(raw_result)

        if result.session_id:
            self._set_role_session(role, result.session_id)
            self.session_registry.touch(
                "codex",
                result.session_id,
                title=self._task_title(title_prefix),
                source="aicc",
                select=True,
            )

        self.timeline.append(
            self.run_id,
            "codex",
            "assistant_message",
            result.stdout or result.stderr,
            session_id=result.session_id or self.session_for_role(role),
            metadata={
                "success": result.success,
                "return_code": result.return_code,
                "event_type": event_type,
                "model": self.settings.codex_model,
                "effort": self.settings.codex_effort,
            },
        )
        self.timeline.save_artifact(
            self.run_id,
            f"codex-{event_type}-output.txt",
            result.stdout,
        )
        if result.raw_stdout:
            self.timeline.save_artifact(
                self.run_id,
                f"codex-{event_type}-events.jsonl",
                result.raw_stdout,
            )
        self._record_vendor_usage(result, "codex", event_type, role)
        return result

    def _run_claude_prompt(
        self,
        prompt: str,
        event_type: str,
        metadata: dict | None = None,
        permission_mode: str | None = None,
        role: str = ROLE_AUTHOR,
    ) -> CliResult:
        """Run one Claude turn on behalf of `role`.

        The session resumed and updated is the role's, not "the Claude one".
        """
        effective_permission_mode = (
            permission_mode or self.settings.claude_permission_mode
        )
        session_id = self.session_for_role(role)

        self.timeline.append(
            self.run_id,
            "user",
            f"claude_{event_type}",
            prompt.strip(),
            session_id=session_id,
            metadata={
                **dict(metadata or {}),
                "model": self.settings.claude_model,
                "effort": self.settings.claude_effort,
                "permission_mode": effective_permission_mode,
                "max_turns": self.settings.claude_max_turns,
                "timeout_minutes": self.settings.claude_timeout_minutes,
            },
        )

        # `-p` is a flag, not a slot: the prompt is `claude`'s positional
        # argument, and when it is absent `claude -p` reads the prompt off
        # standard input (`--input-format text`, its default). Passing the
        # prompt as argv is what this engine used to do, and it could not
        # work: every prompt this file builds is multi-line, and a Windows
        # command line cannot carry a line break at all - `claude`
        # resolves to `claude.CMD` here, so cmd.exe would have handed the
        # CLI the first line of the prompt and nothing else. Codex has
        # been reading its prompt from stdin all along (`codex exec ... -`,
        # `input_text=prompt`); this is the same door, for the same
        # reason.
        arguments = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            effective_permission_mode,
        ]
        if self.settings.claude_max_turns > 0:
            arguments.extend(
                ["--max-turns", str(self.settings.claude_max_turns)]
            )
        if self.settings.claude_model != "default":
            arguments.extend(["--model", self.settings.claude_model])
        if self.settings.claude_effort != "auto":
            arguments.extend(["--effort", self.settings.claude_effort])
        # 官方旗標，會出現在 `/resume` 選單裡。使用者事後要找 AICC 幫他跑過
        # 什麼，這是唯一一條完全走官方介面的路。
        arguments.extend(["--name", self._session_display_name(role)])
        if session_id:
            arguments.extend(["--resume", session_id])

        raw_result = self._send_prompt_to_vendor(
            arguments,
            prompt,
            timeout_seconds=self.settings.claude_timeout_seconds,
        )
        result = self._normalize_claude_result(raw_result)
        if result.session_id:
            self._set_role_session(role, result.session_id)
            title = self._task_title("Claude Developer")
            self.session_registry.touch(
                "claude",
                result.session_id,
                title=title,
                source="aicc",
                select=True,
            )
            self._register_in_vendor_sidebar(result.session_id, title)

        self.timeline.append(
            self.run_id,
            "claude",
            "assistant_message",
            result.stdout or result.stderr,
            session_id=result.session_id or self.session_for_role(role),
            metadata={
                "success": result.success,
                "return_code": result.return_code,
                "event_type": event_type,
                "model": self.settings.claude_model,
                "effort": self.settings.claude_effort,
                "permission_mode": effective_permission_mode,
                "max_turns": self.settings.claude_max_turns,
                "timeout_minutes": self.settings.claude_timeout_minutes,
            },
        )
        self.timeline.save_artifact(
            self.run_id,
            "claude-output.txt",
            result.stdout,
        )
        self._record_blocked_instructions(result.stdout)
        if result.raw_stdout:
            self.timeline.save_artifact(
                self.run_id,
                "claude-result.json",
                result.raw_stdout,
            )
        self._record_vendor_usage(result, "claude", event_type, role)
        return result

    BLOCKED_INSTRUCTIONS_ARTIFACT = "blocked-instructions.json"

    def _record_blocked_instructions(self, output: str) -> None:
        """記下「使用者這一句，被這一條護欄擋下來了」。**累積，不覆蓋。**

        2026-08-27 拍板（❓15）的第二半。內容由
        `orchestrator_parsing.blocked_instructions()` 算出來，而它**兩邊都
        驗過**：那一句真的在任務原文裡，那一條真的是我們自己寫的護欄。
        驗不過的整組在那裡就丟掉了，所以寫進來的每一筆都是實話。

        ## 為什麼要累積

        `claude-output.txt` 是**覆蓋**的，最後一次呼叫蓋掉前面每一次。而
        被擋下來的那一句通常出現在**第一次動手**那一輪，後面的修正輪不會
        再提一次——只留最後一份的話，使用者永遠看不到它。

        ## 為什麼從 task.md 讀，不從參數拿

        這一支活在 `run_claude()` 的尾巴，那裡拿得到的是提示詞不是任務。
        而任務原文 `create_run()` 已經原封不動寫進 `task.md` 了——
        **回頭讀那一份，比再傳一份進來安全**：傳進來的那一份有可能是別的
        東西（提示詞、摘要），而驗證的前提是「這就是他寫的字」。

        全程不丟例外：這是一列附註，不值得為它中斷一次執行。
        """
        try:
            task = self.timeline.read_artifact(self.run_id, "task.md")
            if not task.strip():
                return
            found = orchestrator_parsing.blocked_instructions(task, output)
            if not found:
                return
            stored = json.loads(
                self.timeline.read_artifact(
                    self.run_id, self.BLOCKED_INSTRUCTIONS_ARTIFACT
                )
                or "[]"
            )
            if not isinstance(stored, list):
                stored = []
            merged = list(stored)
            seen = {
                (str(row.get("quote", "")), str(row.get("rule_id", "")))
                for row in stored
                if isinstance(row, dict)
            }
            for item in found:
                key = (item.quote, item.rule_id)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(
                    {
                        "quote": item.quote,
                        "rule_id": item.rule_id,
                        "rule_text": item.rule_text,
                    }
                )
            if len(merged) == len(stored):
                return
            self.timeline.save_artifact(
                self.run_id,
                self.BLOCKED_INSTRUCTIONS_ARTIFACT,
                json.dumps(merged, ensure_ascii=False, indent=2),
            )
        except Exception:
            return

    def _record_vendor_usage(
        self,
        result: CliResult,
        provider: str,
        event_type: str,
        role: str,
    ) -> None:
        """Put one CLI call's own usage report into the ledger.

        Called for both providers on every call, including the ones that report
        nothing. Claude's shape is **not** verified - this machine's CLI is
        signed out, so no successful Claude output has ever been read. Nothing
        here assumes a shape: whatever scalars the CLI puts under `usage` are
        transcribed under their own names, and a call that reports none is
        counted as a call that reported none. That is the difference between
        leaving room for Claude and guessing what Claude says.
        """
        readings = orchestrator_parsing.extract_usage_readings(
            result,
            provider=provider,
            event_type=event_type,
            # A resumed call may not repeat the id, so fall back to the session
            # this role is already on - the same expression the timeline uses.
            session_id=result.session_id or self.session_for_role(role),
        )
        self.context_ledger.record_vendor_usage(self.run_id, readings)

    @staticmethod
    def _normalize_claude_result(result: CliResult) -> CliResult:
        return orchestrator_parsing.normalize_claude_result(result)

    @staticmethod
    def _normalize_codex_result(result: CliResult) -> CliResult:
        return orchestrator_parsing.normalize_codex_result(result)

    # 使用者在 `/resume` 選單裡會看到的名字。角色的中文名跟畫面上一致，
    # 因為那個選單是給人看的，不是給程式讀的。
    _ROLE_DISPLAY = {
        ROLE_AUTHOR: "role.worker",
        ROLE_REVIEWER: "role.reviewer",
        ROLE_FINISHER: "role.approver",
        ROLE_PLANNER: "role.planner",
    }

    def _session_title_line(self, role: str, task: str) -> str:
        """給廠商的對話清單看的一行。

        跟 `--name` 用同一個前綴，這樣使用者在 Claude 的 `/resume` 選單與
        Codex 的清單裡看到的是同一種東西，不用學兩套。
        """
        head = self._session_display_name(role)
        first_line = next(
            (line.strip("# ").strip() for line in task.splitlines() if line.strip()),
            "",
        )
        return f"{head}｜{first_line[:40]}" if first_line else head

    def _session_display_name(self, role: str) -> str:
        """`--name` 的值：一眼看得出是哪個專案的哪個角色。

        刻意很短。這個名字會出現在提示框、`/resume` 選單與終端機標題，
        塞任務全文進去只會把選單撐爆——任務標題已經在 AICC 自己的清單裡了。
        """
        # 那張表存的是 key。查不到就用代號本身——不丟例外，
        # 因為這只是對話標題，炸掉的代價比顯示一個英文代號大得多。
        key = self._ROLE_DISPLAY.get(role)
        seat = t(key) if key else role
        return f"AICC·{self.project_path.name}·{seat}"[:60]

    def _register_in_vendor_sidebar(self, session_id: str, title: str) -> None:
        """把這段對話登記到 Claude 桌面版的側邊欄。

        **失敗要看得見。**那份索引的格式是逆向出來的，廠商改版會讓它無聲
        失效——照樣寫、照樣沒有錯誤、而側邊欄就是不出現，跟「這次沒跑」在
        畫面上一模一樣。所以登記不成功會在時間軸上留一行，不是默默吞掉。

        從不丟例外：登記失敗不該弄死一次真的執行。
        """
        try:
            outcome = ClaudeSidebarIndex().register(
                cli_session_id=session_id,
                cwd=self.project_path,
                title=title,
                model=self.settings.claude_model,
            )
        except Exception as exc:  # noqa: BLE001
            outcome = None
            problem = f"登記時發生預期外的錯誤：{exc}"
        else:
            problem = "" if outcome.ok else outcome.problem

        if problem:
            self.timeline.append(
                self.run_id,
                "aicc",
                "vendor_index_not_updated",
                "這段對話沒有出現在 Claude 桌面版的清單裡："
                f"{problem}"
                "（AICC 這邊的紀錄不受影響，逐字稿也還在。）",
            )

    def _task_title(self, fallback: str) -> str:
        task = self.task_path.read_text(encoding="utf-8").strip()
        first_line = next((line.strip("# ") for line in task.splitlines() if line.strip()), "")
        if not first_line:
            return fallback
        return f"{fallback}｜{first_line[:56]}"

    @staticmethod
    def parse_claude_question(output: str) -> str:
        return orchestrator_parsing.parse_claude_question(output)

    @staticmethod
    def parse_review(codex_output: str) -> dict:
        return orchestrator_parsing.parse_review(codex_output)

    @staticmethod
    def parse_review_status(codex_output: str) -> str:
        return orchestrator_parsing.parse_review_status(codex_output)

    @staticmethod
    def parse_decision_gate(output: str) -> dict:
        return orchestrator_parsing.parse_decision_gate(output)

    @staticmethod
    def parse_memory_review(output: str) -> dict:
        return orchestrator_parsing.parse_memory_review(output)
