from __future__ import annotations

from app.i18n import t

import json
import traceback
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from app.adapters.cli_runner import CliResult

from app.core.lineup import (
    CLASSIC_ROLES,
    DUTY_APPROVE,
    DUTY_PLAN,
    DUTY_REVIEW,
    DUTY_WORK,
    ROLE_AUTHOR,
    ROLE_PLANNER,
    ROLE_REVIEWER,
    AgentSlot,
    Lineup,
    criteria_seat,
)
from app.core import outcomes
from app.core.orchestrator import NoSeatWithDuty, Orchestrator
from app.core.orchestrator_parsing import (
    parse_approval,
    parse_consult_request,
    parse_criteria_rejection,
    parse_intake,
)
from app.core.run_stamps import RunStamper
from app.core.lineup import shown_name
from app.core.steps import (
    Answer,
    Site,
    Stop,
    approval,
    consulting,
    building,
    intake,
    planning,
    reviewing,
    verification,
)
from app.services.ai_settings import AIExecutionSettings
from app.services.approval_chain import Result, StampKind, Versions
from app.services.project_knowledge import MemoryTruncatedError
from app.services.project_verification import (
    TESTS_NOT_DECLARED,
    TESTS_NOT_RUN,
    TESTS_TIMED_OUT,
    VerificationNotDeclared,
)
from app.services.session_registry import SessionRegistry


def why_nobody_reviewed(lineup) -> str:  # noqa: ANN001
    """沒有人複查的時候，該講哪一句。

    **「你沒排複查」跟「你排了但我讀不出來」是兩件事。**

    FG20：陣容裡有讀不出來的位子時，那一格**是他排的**——只是這一版讀不懂
    它某一欄，所以它沒有被派工。照「你沒排」那句講，他會以為是自己的陣容
    本來就沒有那一格，**而不是「有一格被丟掉了」**。而那一輪的收場是
    完成／可以拍板，畫面上的節點數少一個，**沒有任何一句話說少了什麼**。

    那一格的名字讀得出來（走到 `UnreadableSlot` 的前提就是「名字讀得出來，
    只有某一欄這一版不認得」），所以講得出是哪幾格——**講不出名字的警告
    等於沒有警告**。

    **抽成一支具名函式而不是寫在呼叫端**：寫在呼叫端的話，測試只測得到
    那兩句話長什麼樣，測不到「選哪一句」——而選錯的那一天，兩句話都還是
    好好的。破壞驗證 L1 當場判它沒有牙齒。
    """
    lost = getattr(lineup, "unreadable_names", ())
    if lost:
        return t(
            "lineup.no_reviewer_but_some_unreadable",
            count=len(lost),
            names="、".join(lost),
        )
    return t("lineup.no_reviewer_note")


# 缺哪一種職責就講哪一句。**一種職責一句，不是一句填空的通稿。**
#
# 通稿只講得出「去陣容頁改職責」，而那是四句話裡最沒用的那一半：缺複查
# 的人還可以把序列換成開發序列，缺執行的人還可以把討論回合設成 0——
# 兩條出路都是**不用改陣容**的，而通稿講不出它們。
#
# 這張表要蓋滿 `lineup.VALID_DUTIES`。守著這件事的是
# `test_a_planning_run_with_nobody_to_review_says_which_duty_is_missing.py`
# ::test_every_duty_has_a_sentence——少一種的時候會在那裡紅，
# **不是在使用者按下開始的那一刻丟 KeyError**。
NO_SEAT_TEXT_KEYS = {
    DUTY_PLAN: "lineup.no_seat_with_duty.plan",
    DUTY_WORK: "lineup.no_seat_with_duty.work",
    DUTY_REVIEW: "lineup.no_seat_with_duty.review",
    DUTY_APPROVE: "lineup.no_seat_with_duty.approve",
}


def why_this_sequence_cannot_run(
    duty: str, seats: tuple[str, ...], workflow_mode: str
) -> str:
    """這條序列派不出工的時候，該講哪一句。

    **抽成一支具名函式而不是寫在呼叫端**，跟隔壁 `why_nobody_reviewed`
    同一個理由：寫在呼叫端的話，測試只測得到「那句話長什麼樣」，
    測不到「選哪一句」——而選錯的那一天，四句話都還是好好的。

    序列的名字從 `sequence.*` 帶進去，**不在句子裡寫死**。那個詞是使用者
    在兩個畫面之間對照用的（設定面板選哪一條、陣容頁看那一條下每一格
    做什麼），而它 2026-08-26 之前在這個產品裡有兩個名字。
    """
    planning = workflow_mode == "planning"
    return t(
        NO_SEAT_TEXT_KEYS[duty],
        sequence=t("sequence.planning" if planning else "sequence.development"),
        other=t("sequence.development" if planning else "sequence.planning"),
        count=len(seats),
        seats="、".join(seats),
    )


# 維護共同記憶那兩步派不出去的時候，該講哪一句。
#
# **跟 `NO_SEAT_TEXT_KEYS` 分開的那一張表**，因為使用者看到的不是同一件事：
# 那一種是「這一輪沒有開始」（一次派工都沒發生），這一種是「跑完了，
# 但共同記憶沒有人維護」——程式寫了、測試過了、章也蓋了，**產出不受影響**，
# 而那句話一定要講，不然他會以為要重跑。
#
# 兩種職責兩句：缺複查的時候沒有人擬草稿，缺執行的時候草稿擬得出來但沒有人
# 審它——而**這個產品不會把一份沒有人看過的東西寫進八份共同記憶**
# （`SECURITY_MODEL.md` 就在那八份裡）。
MEMORY_NO_SEAT_TEXT_KEYS = {
    DUTY_REVIEW: "memory.no_seat_with_duty.review",
    DUTY_WORK: "memory.no_seat_with_duty.work",
}


def why_memory_was_not_maintained(duty: str, seats: tuple[str, ...]) -> str:
    """共同記憶沒有維護到的時候，該講哪一句。

    抽成具名函式的理由同隔壁 `why_this_sequence_cannot_run`：寫在呼叫端的
    話，測試只測得到「那句話長什麼樣」，測不到「選哪一句」。
    """
    return t(
        MEMORY_NO_SEAT_TEXT_KEYS[duty],
        count=len(seats),
        seats="、".join(seats),
    )


class WorkflowWorker(QObject):
    stage_changed = Signal(str)
    # Which line-up seat is running, by role name. Separate from stage_changed
    # because the stage is prose written per step ("正在執行測試" belongs to no
    # seat) while this is the engine's own dispatch.
    active_role_changed = Signal(str)
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        project_path: Path,
        user_question: str = "",
        user_answer: str = "",
        starting_fix_attempts: int = 0,
        claude_session_id: str = "",
        codex_session_id: str = "",
        run_id: str = "",
        settings: AIExecutionSettings | None = None,
    ) -> None:
        super().__init__()
        self.project_path = project_path
        self.user_question = user_question
        self.user_answer = user_answer
        self.starting_fix_attempts = starting_fix_attempts
        self.claude_session_id = claude_session_id
        self.codex_session_id = codex_session_id
        self.run_id = run_id
        self.settings = (settings or AIExecutionSettings()).normalized()
        self.orchestrator: Orchestrator | None = None

    @Slot()
    def run(self) -> None:
        try:
            orchestrator = Orchestrator(
                self.project_path,
                claude_session_id=self.claude_session_id,
                codex_session_id=self.codex_session_id,
                run_id=self.run_id,
                settings=self.settings,
                lineup=self._lineup_for_run(),
            )
            self.orchestrator = orchestrator
            # Emitted from this (worker) thread, same as stage_changed - the
            # receiving side connects it with BoundSlot, see MainWindow.
            orchestrator.on_role_start = self.active_role_changed.emit

            if (
                self.settings.workflow_mode == "planning"
                and not self.user_answer
            ):
                self._run_planning(orchestrator)
            else:
                self._run_development(orchestrator)

        except NoSeatWithDuty as missing:
            # 不是 `failed`：程式沒有壞，是這份陣容裡沒有這條序列要派工的
            # 那一種職責。收成一個結局而不是一坨 traceback，跟隔壁
            # `MemoryTruncatedError` 同一個形狀——差別只在停的理由，
            # 而理由是這個結局唯一有用的東西。
            #
            # **收在這裡而不是收在 `_run_planning` 裡**：丟這個例外的是
            # `orchestrator.required_seat_with_duty`，而序列上每一步都可能
            # 走到它。收在每一個呼叫點的話，第二個呼叫點是下一個漏掉的地方。
            self._complete(self._no_seat_for_duty_result(missing))
        except MemoryTruncatedError as cut:
            # 不是 `failed`：這不是「程式壞了」，是「共同記憶少了一截，所以
            # 這一輪不該跑」。兩者在畫面上差很多——`failed` 畫成紅燈加一坨
            # traceback，沒有人看得出要做什麼；這裡收成一個 `partial` 結局，
            # 跟「尚未設定驗證」同一族：**跑完了、不會有東西回來、要人處理**，
            # 而且那句話講的是真正的原因。
            #
            # 收在這裡而不是收在每個呼叫點，是因為三條路（work plan、
            # decision gate、memory update）都會丟同一個例外，而它們對這件事
            # 的答案本來就該一樣。
            self._complete(self._memory_truncated_result(str(cut)))
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            self.finished.emit()

    def _lineup_for_run(self) -> Lineup | None:
        """The project's composed line-up, with this run's session ids applied.

        **This wire did not exist before ROADMAP P10 Stage B**, and its absence
        was a live bug rather than an unbuilt feature: the composer wrote a
        line-up into `sessions.json`, the engine built `DEFAULT_LINEUP` from
        the two session-id arguments and never read the file. Setting both
        seats to Claude on the 陣容 page stored `reviewer: claude` and still
        ran Codex. Verified by composing one and printing what the engine
        resolved, not by reading the code.

        Returns None for a project that has never composed one, so the
        orchestrator's own fallback stays the single definition of "the
        default line-up".

        Session ids only reach `author` and `reviewer`. Those two arguments are
        provider-keyed - a channel that predates per-role sessions and cannot
        say *which* Claude - so applying them to a seat they predate is how a
        third agent would silently resume somebody else's conversation. Newer
        seats keep whatever the registry stored for them.

        **2026-08-10: that protection had a hole, and it let back in the exact
        bug P10 was built to remove.** The provider-keyed argument was applied
        to *every* classic seat, so a line-up with two Claude seats gave both
        of them the same conversation:

            陣容頁配的      author=AUTHOR-CHAT   reviewer=REVIEWER-CHAT
            實際跑的        author=AUTHOR-CHAT   reviewer=AUTHOR-CHAT

        `REVIEWER-CHAT` was discarded without a word, and 「獨立審查者」 became
        the author reading their own work. Two Claude seats is a supported and
        signposted configuration - the 陣容 page warns about shared context but
        allows it, and the acceptance checklist walks the user straight into it.

        The legacy channel carries **one** session per provider, so it may
        speak for **one** seat per provider: the same first-of-provider slot
        that `selected_session_id()` derives it from. Any further seat of that
        provider keeps its own stored session.

        `force_new_sessions` has to be named here rather than ridden in on an
        empty argument. `main_window` already blanks the provider-keyed values
        for it, and under the old code that blanking reached both classic
        seats; now that the second seat keeps its own session, the setting has
        to say so itself or it would quietly stop applying to that seat.

        **Deliberately only the classic seats**, which is exactly as far as the
        old behaviour went. A planner seat keeps its stored session under
        `force_new_sessions` today, and that is arguably wrong - but it is a
        separate question about what that setting means, not part of this fix.
        Recorded in ROADMAP P10 rather than changed in passing: it alters what
        a paid run sends.
        """
        stored = SessionRegistry(self.project_path).load_lineup()
        if not len(stored):
            return None
        by_provider = {
            "claude": self.claude_session_id,
            "codex": self.codex_session_id,
        }
        # Which seat each provider-keyed argument is allowed to speak for.
        spoken_for = {
            provider: stored.first_for_provider(provider)
            for provider in by_provider
        }
        fresh = self.settings.force_new_sessions and not (
            self.user_answer or self.run_id
        )

        def session_for(slot: AgentSlot) -> str:
            if slot.role not in CLASSIC_ROLES:
                return slot.session_id
            if fresh:
                return ""
            owner = spoken_for.get(slot.provider)
            if owner is not None and owner.role == slot.role:
                return by_provider.get(slot.provider, "")
            return slot.session_id

        return Lineup.of(
            # **`replace()`，不要重新建一個。**
            #
            # 這裡以前傳四個位置參數，於是 `identity` 與（2026-08-22 加的）
            # `duty` 兩格在每一次執行的當下都被丟掉：使用者排的「實作」那一格
            # 職責會退回預設，引擎因此找不到任何會產出東西的位子，而畫面上
            # 他的陣容看起來一切正常。
            #
            # 改成具名參數只擋掉一半——**名字要一個一個抄，而漏抄的那個欄位
            # 一樣是靜靜消失，一樣不會有任何錯誤**。2026-08-25 加
            # `writes_files`（那一格要不要動檔案）的時候證實了這件事：這一行
            # 每次執行都會跑到，漏抄它的話，使用者親手關掉寫檔的那一格會在
            # **開始執行的當下**變回可以寫，而陣容頁上那個開關看起來還是關的。
            #
            # `replace()` 沒有這個洞：它從既有的那一格複製，只換指名要換的。
            replace(slot, session_id=session_for(slot))
            for slot in stored
        )

    def _run_planning(self, orchestrator: Orchestrator) -> None:
        task = orchestrator.task_path.read_text(encoding="utf-8")
        rounds = self.settings.planning_rounds

        self.stage_changed.emit(t("step.codex_drafting_plan"))
        codex_result = orchestrator.run_codex_plan(
            task=task,
            round_number=0,
            final_round=(rounds == 0),
        )
        if not codex_result.success:
            self._complete(
                self._planning_result(
                    workflow_status="CODEX_ERROR",
                    codex_result=codex_result,
                    claude_result=None,
                )
            )
            return

        current_plan = codex_result.stdout
        last_claude = None

        for round_number in range(1, rounds + 1):
            self.stage_changed.emit(
                f"Claude 正在檢查第 {round_number} 輪計畫"
            )
            last_claude = orchestrator.run_claude_plan_review(
                task=task,
                codex_plan=current_plan,
                round_number=round_number,
            )
            if not last_claude.success:
                self._complete(
                    self._planning_result(
                        workflow_status="CLAUDE_ERROR",
                        codex_result=codex_result,
                        claude_result=last_claude,
                    )
                )
                return

            self.stage_changed.emit(
                f"Codex 正在統整第 {round_number} 輪計畫"
            )
            codex_result = orchestrator.run_codex_plan(
                task=task,
                previous_plan=current_plan,
                claude_feedback=last_claude.stdout,
                round_number=round_number,
                final_round=(round_number == rounds),
            )
            if not codex_result.success:
                self._complete(
                    self._planning_result(
                        workflow_status="CODEX_ERROR",
                        codex_result=codex_result,
                        claude_result=last_claude,
                    )
                )
                return
            current_plan = codex_result.stdout

        orchestrator.timeline.save_artifact(
            orchestrator.run_id,
            "final-plan.md",
            current_plan,
        )
        if t("handoff.first_import") in task or t("handoff.summary_title") in task:
            # current_plan is codex_result.stdout from the planning loop.
            written_memory = orchestrator.knowledge.write_memory_from_text(
                current_plan, author="codex"
            )
            orchestrator.timeline.append(
                orchestrator.run_id,
                "aicc",
                "project_brief_saved",
                t("handoff.saved_to_memory")
                + ", ".join(written_memory),
            )
        self._complete(
            self._planning_result(
                workflow_status="PLANNING_COMPLETED",
                codex_result=codex_result,
                claude_result=last_claude,
            )
        )

    def _no_worker_result(self) -> dict:
        """陣容裡沒有任何一格會產出東西。

        **停下來，不要挑一格代打。**挑的那一格會拿到寫入權限，而使用者從來
        沒有把那一格排成「執行」——他會得到一個他沒有授權的動作，而畫面上
        看起來一切正常。
        """
        return self._build_result(
            workflow_status="NO_WORKING_SEAT",
            review_status="NOT_RUN",
            fix_attempts=0,
            claude_result=None,
            test_result=None,
            codex_result=None,
        ) | {
            "verification_problem": (
                t("lineup.no_worker")
            )
        }

    def _no_seat_for_duty_result(self, missing: NoSeatWithDuty) -> dict:
        """這條序列要派工的那一種職責，這份陣容裡一格都沒有。

        **這一輪一次派工都沒有發生**，所以每一個結果欄位都是空的／False：
        沒有人交出東西、沒有跑測試、沒有人審。挑一格代打會讓其中幾欄有值，
        而那正是這個結局要防的事——`test_it_does_not_pick_one_to_stand_in`
        量的就是這幾欄。

        跟 `_no_reviewer_result` 分開，因為使用者看到的不是同一件事：
        那一個是「東西做出來了但沒有人審」（少一個章），這一個是
        「連第一步都派不出去」（這一輪沒有開始）。
        """
        return self._build_result(
            workflow_status="NO_SEAT_FOR_DUTY",
            review_status="NOT_RUN",
            fix_attempts=0,
            claude_result=None,
            test_result=None,
            codex_result=None,
        ) | {
            "verification_problem": why_this_sequence_cannot_run(
                missing.duty,
                missing.seats,
                self.settings.workflow_mode,
            )
        }

    def _no_reviewer_result(self, claude_result, test_result) -> dict:
        """東西做出來了，但沒有人審。

        不當成成功：【簽核鏈】那條定案說最終決策要一路上每個 node 都有章，
        而沒有複查的那一格，章就少一個。回報成 COMPLETED 會讓使用者以為
        有人看過了。
        """
        return self._build_result(
            workflow_status="NO_REVIEWING_SEAT",
            review_status="NOT_RUN",
            fix_attempts=0,
            claude_result=claude_result,
            test_result=test_result,
            codex_result=None,
        ) | {
            "verification_problem": (
                t("lineup.no_reviewer_after_work")
            )
        }

    def _fix_target(
        self, orchestrator: Orchestrator, review_data: dict
    ) -> tuple[str, str]:
        """駁回要退給誰。回傳 `(節點, 問題)`，只有一個有意義。

        定案【駁回直接送給該負責的人】的不變式：**駁回一定要指名一個 node
        並附理由，不准只說「退回上一關」。**誰該負責由職責決定，不由位置
        決定——畫面有問題就退給做畫面的那一格，不是退給它前面那一格。

        **只有一格會產出東西的時候不用指名。**那不是「猜一個」——候選只有
        一個，沒有可猜的空間。這條規則要防的是引擎在多個候選之間自己挑，
        而不是要求複查說一句廢話。

        指名了但那個名字不在陣容裡，當成無效回覆，**不退而求其次挑一格**：
        它想退給的那一格跟我們挑的那一格，很可能不是同一個人。
        """
        workers = self._roles_with(orchestrator, DUTY_WORK)
        named = str(review_data.get("target", "")).strip().lower()

        if not named:
            if len(workers) == 1:
                return workers[0], ""
            return "", (
                "複查回報要修正，但沒有指名要退給哪一格。這個專案有"
                f"{len(workers)} 格會產出東西（{'、'.join(workers)}），"
                "引擎不會替它挑一個——那就變回「退回上一關」了。"
            )

        for slot in orchestrator.lineup:
            if slot.role == named:
                if slot.duty != DUTY_WORK:
                    return "", (
                        f"複查要把問題退給「{slot.role}」，但那一格的職責是"
                        f"「{slot.duty}」，它不會產出東西，修不了。"
                    )
                return slot.role, ""

        return "", (
            f"複查要把問題退給「{named}」，但這個專案的陣容裡沒有這一格。"
            f"目前排的是：{'、'.join(orchestrator.lineup.roles)}。"
        )

    @staticmethod
    def _roles_with(orchestrator: Orchestrator, duty: str) -> list[str]:
        """陣容裡做這一種事的位子，依執行順序。

        以前這裡是寫死的 ROLE_AUTHOR / ROLE_REVIEWER，於是使用者把位子取名
        「實作」「複查」之後，引擎會說「這個專案的陣容裡沒有 author 這一格」
        ——他排得出來的東西，引擎跑不動。
        """
        return [slot.role for slot in orchestrator.lineup if slot.duty == duty]

    def _intake_phase(
        self, orchestrator: Orchestrator, stamper: RunStamper
    ) -> tuple[str, dict | None]:
        """需求確認那一扇門。門後的東西在 `app/core/steps/intake.py`。

        這裡只做三件事：**把現場交出去、把答案的去留接回來、把「為什麼停」
        翻成一份回報**。判斷一個字都不留在這裡——留了就是門開得不夠大。
        """
        site = self._site(
            orchestrator,
            stamper,
            Answer(question=self.user_question, text=self.user_answer),
        )
        criteria, stop = intake.confirm(site)
        # 答案被吃掉的話，這裡也要跟著空——底下「依照使用者答案繼續」那條路
        # 讀的是 `self.user_answer`。
        self.user_question = site.answer.question
        self.user_answer = site.answer.text
        return criteria, self._as_result(stop)

    def _site(
        self,
        orchestrator: Orchestrator,
        stamper: RunStamper,
        answer: Answer | None = None,
    ) -> Site:
        """一次執行的現場，交給門後的模組。

        寫成一個地方而不是每一扇門各組一次：**現場多一樣東西的時候，
        這裡改一行，不是一扇一扇改**——而那正是這批門要消滅的東西。
        """
        return Site(
            orchestrator=orchestrator,
            stamper=stamper,
            on_stage=self.stage_changed.emit,
            answer=answer if answer is not None else Answer(),
        )

    def _as_result(self, stop: Stop | None) -> dict | None:
        """把一句「為什麼停」翻成一份回報。

        **門後的模組不知道回報長什麼樣**，那是刻意的：那份字典有十九個欄位，
        每一扇門都要認識它的話，加一個欄位就要一扇一扇改。
        """
        if stop is None:
            return None
        if stop.cli_result is not None:
            return self._build_result(
                workflow_status=stop.status,
                review_status=stop.review_status,
                fix_attempts=self.starting_fix_attempts,
                claude_result=stop.cli_result,
                test_result=None,
                codex_result=None,
            )
        return self._early_stop_result(
            workflow_status=stop.status,
            review_status=stop.review_status,
            note=stop.note,
            question=stop.question,
        )

    def _build_phase(
        self, orchestrator: Orchestrator, stamper: RunStamper
    ) -> tuple[CliResult | None, dict | None]:
        """確認需求 → 擬做法 → 執行。**這是一個迴圈，不是一條直線。**

        老闆 2026-08-22 問的：「如果當成絕對的驗收標準的話**可能會因為執行上
        的問題而任務失敗**　有其他方法嗎?」

        定案是：執行的那一格發現做不到，就**駁回給訂標準的那一格**，附理由。
        不是讓它自己改標準（它會改成自己剛好做得到的），也不是讓整件事失敗
        ——現實跟預期不一樣的時候，錯的通常不是現實。

        所以退回之後這裡會回到最前面，讓訂標準的那一格帶著這條意見重訂一份，
        然後重新派工。

        **沒有次數上限。**收斂的判準是定案寫的那一條：同一條異議連續兩輪沒有
        進展就是卡住。設次數上限的話，到最後一次不管解決沒有，執行端都會為了
        不被判失敗而硬做——而它交出來的東西沒有人要。

        回傳 `(執行結果, 要停下來的結果, 真的做了事的那幾格)`。前兩個只有一個
        有意義；第三個是給蓋章用的——**章要蓋在真的做事的那一格名下**，寫死
        author 的話，使用者把那一格取名「實作」之後，拍板檢查會永遠說缺一個
        而且補不出來。
        """
        seen: list[str] = []
        while True:
            criteria, intake_stop = self._intake_phase(orchestrator, stamper)
            if intake_stop is not None:
                return None, intake_stop, []

            if self.user_answer:
                self.stage_changed.emit(t("step.claude_resuming_after_answer"))
                # 只有真人在對話框裡打了字才會走到這裡——`user_answer` 沒有
                # 別的來源，而需求確認那一步已經把屬於它的答案吃掉並清空了。
                #
                # 這裡刻意不重跑規劃、也不重送做法：它續的是同一個 Claude
                # 對話，那些東西它已經有了。重跑等於再付一次錢，去換一份給
                # 做到一半的工作用的新做法。
                # 續跑那條路沒有重走工作迴圈，所以這裡自己挑出是誰：
                # 陣容裡最後一格做事的。它就是當初被問住的那一格。
                #
                # **挑出來的那一格要一路傳到底。**以前只拿它來蓋章，而派工
                # 那一行寫死 `ROLE_AUTHOR`——於是使用者改過名的陣容，會在
                # 「他剛打完答案按下送出」那一刻收到
                # 「這個專案的陣容裡沒有 author 這一格」。**章蓋對了、工作
                # 派錯了，而兩件事來自同一個問題。**
                workers = self._roles_with(orchestrator, DUTY_WORK)
                if not workers:
                    # **他剛打完答案按下送出，而沒有一格接得住它。**
                    #
                    # 以前這裡傳 `role=""` 下去，讓引擎自己找——找不到就退回
                    # 出廠的 `author`，於是他在整條線上最不預期出錯的那一刻
                    # 收到「這個專案的陣容裡沒有 author 這一格」，一坨
                    # traceback，而他剛剛才打完字。
                    #
                    # 收成「沒有人做事」而不是「這一輪沒有開始」：續跑這一次
                    # **一次派工都還沒發生**，所以停下來是對的；而他要改的
                    # 那一格是「執行」，`NO_WORKING_SEAT` 那一句講的正是它。
                    # 「這一輪沒有開始」是**整條序列**派不出工那一種的話，
                    # 它會叫他去換序列——換過去照樣沒有人接他的答案。
                    return None, self._no_worker_result(), []
                answered = orchestrator.run_claude_with_user_answer(
                    question=self.user_question,
                    answer=self.user_answer,
                    answered_by="user",
                    role=workers[-1],
                )
                return answered, None, [(workers[-1], answered)]

            work_plan, planner_failure = self._plan_phase(
                orchestrator, criteria
            )
            if planner_failure is not None:
                return None, planner_failure, []

            # 執行那一扇門。門後的東西在 `app/core/steps/building.py`。
            built = building.build(self._site(orchestrator, stamper), work_plan)
            if built.ending == "NO_WORKING_SEAT":
                return None, self._no_worker_result(), []
            if built.stop is not None:
                return None, self._as_result(built.stop), built.ran
            claude_result = built.result
            objection = built.objection
            rejected_by = built.rejected_by
            ran = built.ran

            if not objection or not criteria.strip():
                # 沒有驗收標準的時候，一句「駁回驗收標準」退不回任何地方——
                # 沒有那一格。當成一般的產出往下走，複查那一關會看到它。
                return claude_result, None, ran

            stuck = self._route_criteria_rejection(
                orchestrator, rejected_by, objection, seen
            )
            if stuck is not None:
                return None, stuck, ran
            # 迴圈再走一次：標準已經作廢，訂標準的那一格會帶著這條意見重訂。

    def _dispatch_with_discussion(
        self,
        orchestrator: Orchestrator,
        role: str,
        send,  # noqa: ANN001 - callable(discussion) -> CliResult
    ) -> tuple[CliResult, dict | None]:
        """派一格。它要找別人討論的話，替它問，帶著答案再派一次。

        邏輯在 `app/core/steps/consulting.py`——這裡只是把「為什麼停」翻成
        一份回報。回傳 `(結果, 要停下來的結果)`，只有一個有意義。
        """
        site = self._site(
            orchestrator, RunStamper(self.project_path, orchestrator.run_id)
        )
        result, stop = consulting.ask(site, role, send)
        return result, self._as_result(stop)

    def _approval_phase(
        self,
        orchestrator: Orchestrator,
        stamper: RunStamper,
        deliverable: str,
    ) -> dict | None:
        """核准那一扇門。門後的東西在 `app/core/steps/approval.py`。

        回傳 `None` 代表可以往下走；回傳一個結果代表停下來。
        """
        _nothing, stop = approval.decide(
            self._site(orchestrator, stamper), deliverable
        )
        return self._as_result(stop)

    def _decision_blocked(
        self,
        orchestrator: Orchestrator,
        stamper: RunStamper,
        deliverable: str,
        *,
        fix_attempts: int,
        claude_result,  # noqa: ANN001
        test_result,  # noqa: ANN001
        codex_result,  # noqa: ANN001
    ) -> dict | None:
        """章不齊就**不准報告完成**。判斷在 `approval.is_undecidable`。

        判斷搬走了，這裡留下來的是**把它組成一份回報**——那是主程式的事，
        而且刻意留在這裡：三個執行結果要照原樣帶進去（這一輪真的跑完了，
        紀錄不該因為「還不能拍板」就把它們清空），而門後的模組不知道那份
        回報有十九個欄位。
        """
        undecidable = approval.is_undecidable(
            self._site(orchestrator, stamper), deliverable
        )
        if undecidable is None:
            return None

        result = self._build_result(
            workflow_status="NOT_DECIDABLE",
            review_status="PASS",
            fix_attempts=fix_attempts,
            claude_result=claude_result,
            test_result=test_result,
            codex_result=codex_result,
        )
        result["decision_problem"] = undecidable.problem
        result["decision_verdict"] = undecidable.verdict
        result["test_output"] = undecidable.told_to_the_user
        return result

    def _route_criteria_rejection(
        self,
        orchestrator: Orchestrator,
        rejected_by: str,
        objection: str,
        seen: list[str],
    ) -> dict | None:
        """把「這份標準做不到」退給訂標準的那一格。

        回傳 `None` 代表退回去了、可以再走一輪；回傳一個結果代表停下來。

        【駁回直接送給該負責的人】：這裡指名的是 `criteria_seat`，不是「上一
        關」。退回上一關的話，在五節點流程裡會退給規劃那一格，而標準不是它
        訂的——它只能再退一次，或者自己改一份不是它職權的東西。
        """
        setter = criteria_seat(orchestrator.lineup)
        if not setter:
            return self._nobody_set_the_bar_result(rejected_by, objection)

        # 【不收斂的定義】看**單一異議**不看總數：同一條連續兩輪沒有進展，
        # 就是卡住。比對前先把空白壓平——同一句話換個斷行不算新的意見。
        key = " ".join(objection.split())[:400]
        if seen and seen[-1] == key:
            return self._criteria_deadlock_result(
                rejected_by, setter, objection
            )
        seen.append(key)

        orchestrator.record_criteria_objection(rejected_by, objection)
        orchestrator.clear_acceptance_criteria()
        # 做法也一起作廢。【重簽範圍由依賴決定】：做法是照著舊標準擬的，
        # 留著的話執行的那一格會同時拿到新標準與照舊標準擬的做法，兩份互相
        # 矛盾，而它只能自己選一份照做——選哪一份都不會有人知道。
        orchestrator.clear_work_plans()
        self.stage_changed.emit(
            f"{shown_name(rejected_by)}退回了驗收標準，"
            f"{shown_name(setter)}要重訂"
        )
        return None

    def _nobody_set_the_bar_result(self, seat: str, objection: str) -> dict:
        """有人要退回驗收標準，但這條線上沒有人訂標準。

        不硬挑一格收下它：挑到的那一格會拿到「訂標準」的權限，而使用者從來
        沒有把它排成那個角色。
        """
        shown = shown_name(seat)
        return self._early_stop_result(
            workflow_status="CRITERIA_NO_OWNER",
            review_status="NOT_RUN",
            note=(
                f"【{shown}】要退回驗收標準，但這條工作線上沒有任何一格負責"
                f"訂標準（只有執行的節點）。它說的是：{objection}"
            ),
            question="",
        )

    def _criteria_deadlock_result(
        self, seat: str, setter: str, objection: str
    ) -> dict:
        """同一條意見退第二次還沒有進展——談不攏，交給使用者。

        【談不攏交給人】：不自動放行，也不自動終止。自動放行的話，做出來的
        東西照著一份沒有人同意的標準；自動終止的話，使用者只會看到「失敗」，
        而真正發生的是兩邊對「怎樣算做完」有不同意見。
        """
        shown = shown_name(seat)
        owner = shown_name(setter)
        return self._early_stop_result(
            workflow_status="CRITERIA_DEADLOCK",
            review_status="NOT_RUN",
            note=(
                f"【{shown}】與【{owner}】對「怎樣算做完」談不攏：同一條意見"
                f"退回兩次，標準沒有改動。這一輪停在這裡，等你決定。\n\n"
                f"【{shown}】說：{objection}"
            ),
            question="",
        )

    def _plan_phase(
        self, orchestrator: Orchestrator, criteria: str = ""
    ) -> tuple[str, dict | None]:
        """擬做法那一扇門。門後的東西在 `app/core/steps/planning.py`。

        回傳 `(做法, 失敗結果)`，只有一個有意義。
        """
        site = self._site(
            orchestrator, RunStamper(self.project_path, orchestrator.run_id)
        )
        work_plan, stop = planning.draft(site, criteria)
        return work_plan, self._as_result(stop)

    def _run_development(self, orchestrator: Orchestrator) -> None:
        fix_attempts = self.starting_fix_attempts

        # 章。**這一版只記錄，不擋任何東西**——它不參與底下任何一個
        # 判定，執行的成敗完全跟它無關。理由見 app/core/run_stamps.py：
        # 同一次改動裡同時引進「章可能算錯」與「算錯就擋掉該過的執行」，
        # 第二種會蓋掉第一種的症狀。
        stamper = RunStamper(self.project_path, orchestrator.run_id)

        # 派任何工之前先問「這個專案說過要怎麼驗嗎」。門後的理由在
        # `app/core/steps/verification.py::declared`。
        nothing_to_verify_with = verification.declared(
            self._site(orchestrator, stamper)
        )
        if nothing_to_verify_with:
            self._complete(
                self._verification_not_declared_result(nothing_to_verify_with)
            )
            return

        claude_result, build_stop, workers_ran = self._build_phase(
            orchestrator, stamper
        )
        if build_stop is not None:
            self._complete(build_stop)
            return

        claude_result, pending_result = self._resolve_claude_questions(
            orchestrator,
            claude_result,
            fix_attempts=fix_attempts,
            test_result=None,
            codex_result=None,
        )
        if pending_result is not None:
            self._complete(pending_result)
            return

        # 成敗兩種都蓋。機器章記的是「跑了什麼、結果是什麼」，
        # 只在成功時蓋的話，紀錄裡就會缺掉每一次失敗的那一段。
        # 每一格做事的都蓋一個，**蓋在它自己的名字底下**。
        #
        # 以前這裡只蓋一個，而且寫死 author。使用者把那一格取名「實作」之後，
        # 章掛在一個不存在的角色底下——而拍板檢查要的是那一格的章，於是它永遠
        # 缺一個，**而且補不出來**。排了兩格做事的也一樣：只有一個章。
        #
        # 全部蓋在**最後那一版**交付物上：早一格的成果已經被晚一格接著改了，
        # 蓋在它當時的那一版上的話，那個章在這一輪結束前就自己失效了。
        # `workers_ran` 是空的就**一個章都不蓋**。
        #
        # 這裡本來退回 `[(ROLE_AUTHOR, claude_result)]`——而上面那一整段註解
        # 講的正是「章掛在一個不存在的角色底下，拍板檢查永遠缺一個而且補不
        # 出來」。**同一個檔案裡，修好的那一句下面三行就是同一個病的退路。**
        #
        # 蓋一個假的章比缺一個章更貴：缺章的時候拍板會說「缺誰的章」，
        # 而假章會讓那一關**通過**——一輪沒有人做事的執行，會被記成做完了。
        # （同型的退路 2026-08-24 在 `steps/reviewing.py:236` 已經拿掉一次。）
        final_diff = orchestrator.get_git_diff()
        for seat, outcome in workers_ran:
            stamper.work_done(
                deliverable=final_diff,
                succeeded=outcome.success,
                evidence=f"claude 離開碼 {outcome.return_code}",
                role=seat,
            )

        if not claude_result.success:
            self._complete(
                self._build_result(
                    workflow_status="CLAUDE_ERROR",
                    review_status="NOT_RUN",
                    fix_attempts=fix_attempts,
                    claude_result=claude_result,
                    test_result=None,
                    codex_result=None,
                )
            )
            return

        while True:
            # 驗證那一扇門。門後的東西在 `app/core/steps/verification.py`。
            # 這裡只把「跑出什麼」翻成一份回報——**判斷一個字都不留在這裡**。
            checked = verification.check(self._site(orchestrator, stamper))
            test_result = checked.result
            if checked.ending == "VERIFY_NOT_CONFIGURED":
                self._complete(
                    self._verification_not_declared_result(checked.problem)
                )
                return
            if checked.ending == "VERIFY_TIMED_OUT":
                self._complete(
                    self._build_result(
                        workflow_status="VERIFY_TIMED_OUT",
                        review_status="NOT_RUN",
                        fix_attempts=fix_attempts,
                        claude_result=claude_result,
                        test_result=test_result,
                        codex_result=None,
                    )
                    | {"verification_problem": checked.problem}
                )
                return

            if not test_result.passed:
                if fix_attempts >= orchestrator.max_fix_attempts:
                    self._complete(
                        self._build_result(
                            workflow_status="FIX_LIMIT_REACHED",
                            review_status="NOT_RUN",
                            fix_attempts=fix_attempts,
                            claude_result=claude_result,
                            test_result=test_result,
                            codex_result=None,
                        )
                    )
                    return

                fix_attempts += 1
                self.stage_changed.emit(
                    f"Claude 正在執行第 {fix_attempts} 次修正"
                )
                # **取值前判空。**正常路徑進不來（`building` 會先回
                # `NO_WORKING_SEAT`），但續跑那條路整個跳過了 `building`
                # ——一個 IndexError 在這裡會變成一整坨 traceback，
                # 而真正的事實是「這條線上沒有一格會寫檔案」，那句話產品
                # 早就會講了。
                fixers = self._roles_with(orchestrator, DUTY_WORK)
                if not fixers:
                    self._complete(self._no_worker_result())
                    return
                fixer = fixers[-1]
                claude_result = orchestrator.run_claude_fix(
                    {
                        "summary": t("verify.tests_failed"),
                        "issues": [
                            test_result.stdout or test_result.stderr
                        ],
                        "instructions": [
                            t("verify.fix_instruction")
                        ],
                    },
                    fix_attempts,
                    # 修正派給最後一個「執行」職責的位子。
                    role=fixer,
                )
                claude_result, pending_result = self._resolve_claude_questions(
                    orchestrator,
                    claude_result,
                    fix_attempts=fix_attempts,
                    test_result=test_result,
                    codex_result=None,
                )
                if pending_result is not None:
                    self._complete(pending_result)
                    return
                if not claude_result.success:
                    self._complete(
                        self._build_result(
                            workflow_status="CLAUDE_ERROR",
                            review_status="NOT_RUN",
                            fix_attempts=fix_attempts,
                            claude_result=claude_result,
                            test_result=test_result,
                            codex_result=None,
                        )
                    )
                    return

                # 修完也要蓋一個實作的機器章。不蓋的話，最先那個章蓋在**修改
                # 之前**那一版的交付物上——修完就不算數了，而拍板檢查會說
                # 「缺一個實作的章」。那句話讀起來像流程壞了，實際上是修好了
                # 而沒有人記下來。
                stamper.work_done(
                    deliverable=orchestrator.get_git_diff(),
                    succeeded=claude_result.success,
                    evidence=(
                        f"第 {fix_attempts} 次修正，claude 離開碼 "
                        f"{claude_result.return_code}"
                    ),
                    role=fixer,
                )
                continue

            git_diff = orchestrator.get_git_diff()
            changed_files = orchestrator.get_changed_files()

            # 複查那一扇門。門後的東西在 `app/core/steps/reviewing.py`。
            reviewed = reviewing.look(
                self._site(orchestrator, stamper),
                claude_result=claude_result,
                test_result=test_result,
                git_diff=git_diff,
                changed_files=changed_files,
            )
            codex_result = reviewed.codex_result
            review_data = reviewed.data
            review_status = reviewed.status
            notes = reviewed.notes
            if reviewed.ending == "NO_REVIEWING_SEAT":
                # **不停下來，往下走。**
                #
                # 2026-08-24 老闆把他的五節點排出來的時候撞到這裡：
                # 發想／規劃／UI設計（plan）→ 執行（work）→ 審閱（approve），
                # **沒有一格是複查**——而引擎在這裡把整件事停掉。
                #
                # 查下去發現是 AICC 自己前後矛盾：`requires_for(lineup)` 算出
                # 這一次該有哪些章的時候，**根本沒有要求複查的章**（它是從陣容
                # 算的）。簽核鏈說不需要，執行迴圈說一定要。
                #
                # 停下來那一版的理由是「沒有複查就少一個章」——那句話在
                # `requires_for` 還是寫死的年代是對的，現在不是了。
                #
                # 所以改成：跳過複查那一段，往下走。**章不齊的話底下那條
                # 「還不能拍板」自己會講**，而那正是定案要的形狀：
                # 「少一個章不是卡在那一關，是整件事還不能拍板」。
                #
                # 但**不准安靜地跳過**：時間軸留一筆，不然使用者不小心把複查
                # 那一格刪掉之後，他會得到一個「沒有人看過但看起來很正常」的
                # 結果。
                orchestrator.timeline.append(
                    orchestrator.run_id,
                    "aicc",
                    "no_reviewing_seat",
                    why_nobody_reviewed(orchestrator.lineup),
                )
                review_status = "PASS"
            if reviewed.ending == "CODEX_ERROR":
                self._complete(
                    self._build_result(
                        workflow_status="CODEX_ERROR",
                        review_status="UNKNOWN",
                        fix_attempts=fix_attempts,
                        claude_result=claude_result,
                        test_result=test_result,
                        codex_result=codex_result,
                    )
                )
                return

            if review_status == "PASS":
                # （審查章在門後就蓋好了——每一格同意的各一個，蓋在它自己的
                # 名字底下。見 `reviewing._stamp`。）

                # 核准：看完所有的章，說這一整條鏈站不站得住。
                approval_stop = self._approval_phase(
                    orchestrator, stamper, git_diff
                )
                if approval_stop is not None:
                    self._complete(approval_stop)
                    return

                # 章從這裡開始**有牙齒**。
                #
                # 老闆 2026-08-22 拍板。在這之前它只記錄——鏈子建得起來、算得出
                # 「能不能拍板」，而執行的成敗完全不受它影響。那一版是刻意的
                # （先讓它在旁邊跑一輪，看它算出來的東西對不對），那一輪跑完了。
                #
                # **這不是「失敗」。**定案原文：「少一個章不是卡在那一關，是
                # 整件事還不能拍板」。所以這裡回的是一個獨立的結局，而不是把
                # 一次跑完、測試也過了的執行講成壞掉的。
                undecided = self._decision_blocked(
                    orchestrator,
                    stamper,
                    git_diff,
                    fix_attempts=fix_attempts,
                    claude_result=claude_result,
                    test_result=test_result,
                    codex_result=codex_result,
                )
                if undecided is not None:
                    # **意見也要跟著出去。**這條路以前直接回，所以一次
                    # 「章不齊＋有人提了意見」會把意見整批掉光——而那正是
                    # 使用者最需要看到的組合：東西做完了、有人有話說、
                    # 而且還不能拍板。
                    undecided["advisory_notes"] = notes
                    self._complete(undecided)
                    return

                self.stage_changed.emit(t("step.maintaining_memory"))
                memory_update = orchestrator.maintain_memory_after_pass(
                    claude_result=claude_result,
                    codex_review=codex_result,
                    git_diff=git_diff,
                    changed_files=changed_files,
                )
                # **三種，不是兩種。**2026-08-24 之前這裡是一個三元式：
                # ASK_USER 走一條、**其他全部走 COMPLETED**——而
                # `maintain_memory_after_pass` 回得出 `status="ERROR"`
                # （CLI 掛掉／逾時／沒啟動）。於是一次沒有人維護記憶的執行
                # 在畫面上是薄荷色的「工作流程已完成」。
                memory_status = memory_update.get("status")
                if memory_status == "ASK_USER":
                    ending = ("ASK_USER", "ASK_USER")
                elif memory_status == "ERROR":
                    ending = ("MEMORY_ERROR", "PASS")
                elif memory_status == "NO_SEAT":
                    # **不是 `MEMORY_ERROR`。**那一句話的結尾是「再跑一次
                    # 就好」，而同一份陣容再跑一次會在同一個地方停下來——
                    # 只是這一次他又花了一整輪的額度才看到同一句話。
                    ending = ("MEMORY_NO_SEAT_FOR_DUTY", "PASS")
                else:
                    ending = ("COMPLETED", "PASS")
                result = self._build_result(
                    workflow_status=ending[0],
                    review_status=ending[1],
                    fix_attempts=fix_attempts,
                    claude_result=claude_result,
                    test_result=test_result,
                    codex_result=codex_result,
                )
                result["memory_update"] = memory_update
                # 供參考級的意見。流程沒有為它們停下來，但它們要看得到。
                result["advisory_notes"] = notes
                if memory_status == "ASK_USER":
                    result["review_question"] = str(
                        memory_update.get("question", "")
                    )
                elif memory_status == "ERROR":
                    # 收場那一列要講得出**哪一次呼叫沒成**，不是只講
                    # 「記憶沒維護到」——後者他修不了。
                    result["memory_problem"] = str(
                        memory_update.get("summary", "")
                    ) or t("outcome.MEMORY_ERROR.stage")
                elif memory_status == "NO_SEAT":
                    # 缺的是哪一種職責、他排的那幾格叫什麼名字，都由引擎
                    # 帶回來——收場表那一列的罐頭句子講不出這兩件事，
                    # 而他要對照的正是「我排的這幾格裡哪一格該改」。
                    result["memory_problem"] = why_memory_was_not_maintained(
                        str(memory_update.get("duty", "")),
                        tuple(memory_update.get("seats", ())),
                    )
                self._complete(result)
                return

            if review_status == "ASK_USER":
                result = self._build_result(
                    workflow_status="ASK_USER",
                    review_status="ASK_USER",
                    fix_attempts=fix_attempts,
                    claude_result=claude_result,
                    test_result=test_result,
                    codex_result=codex_result,
                )
                result["review_question"] = review_data.get("question", "")
                self._complete(result)
                return

            if review_status != "FIX":
                self._complete(
                    self._build_result(
                        workflow_status="CODEX_INVALID_RESPONSE",
                        review_status=review_status,
                        fix_attempts=fix_attempts,
                        claude_result=claude_result,
                        test_result=test_result,
                        codex_result=codex_result,
                    )
                )
                return

            if fix_attempts >= orchestrator.max_fix_attempts:
                self._complete(
                    self._build_result(
                        workflow_status="FIX_LIMIT_REACHED",
                        review_status="FIX",
                        fix_attempts=fix_attempts,
                        claude_result=claude_result,
                        test_result=test_result,
                        codex_result=codex_result,
                    )
                )
                return

            fix_target, target_problem = self._fix_target(
                orchestrator, review_data
            )
            if target_problem:
                self._complete(
                    self._build_result(
                        workflow_status="CODEX_INVALID_RESPONSE",
                        review_status=review_status,
                        fix_attempts=fix_attempts,
                        claude_result=claude_result,
                        test_result=test_result,
                        codex_result=codex_result,
                    )
                    | {"verification_problem": target_problem}
                )
                return

            fix_attempts += 1
            shown = shown_name(fix_target)
            self.stage_changed.emit(
                f"{shown}正在執行第 {fix_attempts} 次修正"
            )
            claude_result = orchestrator.run_claude_fix(
                review_data,
                fix_attempts,
                role=fix_target,
            )
            claude_result, pending_result = self._resolve_claude_questions(
                orchestrator,
                claude_result,
                fix_attempts=fix_attempts,
                test_result=test_result,
                codex_result=codex_result,
            )
            if pending_result is not None:
                self._complete(pending_result)
                return
            if not claude_result.success:
                self._complete(
                    self._build_result(
                        workflow_status="CLAUDE_ERROR",
                        review_status="FIX",
                        fix_attempts=fix_attempts,
                        claude_result=claude_result,
                        test_result=test_result,
                        codex_result=codex_result,
                    )
                )
                return

            # 修完要蓋章，否則最先那個實作的章會停在修改之前那一版——修好了
            # 而沒有人記下來，拍板檢查會說「缺一個實作的章」。
            stamper.work_done(
                deliverable=orchestrator.get_git_diff(),
                succeeded=claude_result.success,
                evidence=(
                    f"第 {fix_attempts} 次修正，claude 離開碼 "
                    f"{claude_result.return_code}"
                ),
                role=fix_target,
            )

    def _resolve_claude_questions(
        self,
        orchestrator: Orchestrator,
        claude_result,
        fix_attempts: int,
        test_result,
        codex_result,
    ):
        for _attempt in range(3):
            claude_question = orchestrator.parse_claude_question(
                claude_result.stdout
            )
            if not claude_question:
                return claude_result, None

            self.stage_changed.emit(t("step.codex_judging_question"))
            decision = orchestrator.resolve_claude_question_with_codex(
                claude_question
            )
            gate_result = decision.get("codex_result") or codex_result

            if decision.get("action") == "CONTINUE":
                self.stage_changed.emit(t("step.claude_resuming_after_gate"))
                claude_result = decision["claude_result"]
                continue

            result = self._build_result(
                workflow_status="ASK_USER",
                review_status="ASK_USER",
                fix_attempts=fix_attempts,
                claude_result=claude_result,
                test_result=test_result,
                codex_result=gate_result,
            )
            # The gate may rephrase Claude's question, and when it does that
            # wording is the better one. But when its output could not be
            # parsed, `question` is the raw CLI dump - and preferring that over
            # the question Claude actually asked put machine output in front of
            # the user under 「需要你的判斷：」. Found by driving the real
            # workflow to a gate with an unparseable gate response.
            gate_question = decision.get("question") if decision.get("parsed") else ""
            result["review_question"] = str(
                gate_question or claude_question or decision.get("question") or ""
            )
            result["decision_gate"] = {
                "action": decision.get("action", "ASK_USER"),
                "reason": decision.get("reason", ""),
            }
            return claude_result, result

        result = self._build_result(
            workflow_status="ASK_USER",
            review_status="ASK_USER",
            fix_attempts=fix_attempts,
            claude_result=claude_result,
            test_result=test_result,
            codex_result=codex_result,
        )
        result["review_question"] = (
            t("gate.too_many_auto")
        )
        return claude_result, result

    def _memory_truncated_result(self, problem: str) -> dict:
        """共同記憶被切了，所以什麼都沒跑——這是一個結局，不是一個當機。

        形狀刻意抄 `_verification_not_declared_result`：兩件事是同一種
        「還沒開始就該停」。差別只在停的理由，而理由是這個結局唯一有用的
        東西，所以它被寫進 `memory_problem`（給畫面）與 `test_output`
        （給紀錄），而不是只留在一行 log 裡。

        `review_status` 是 `NOT_RUN` 而不是 `FAIL`：沒有人審過任何東西。
        """
        return {
            "project_path": str(self.project_path),
            "workflow_status": "MEMORY_TRUNCATED",
            "claude_success": False,
            "tests_success": False,
            "tests_status": TESTS_NOT_RUN,
            "codex_success": False,
            "review_status": "NOT_RUN",
            "fix_attempts": self.starting_fix_attempts,
            "claude_output": "",
            "claude_error": "",
            "test_output": problem,
            "memory_problem": problem,
            "codex_output": "",
            "codex_log": "",
            "review_question": "",
            "memory_update": {},
            "ai_settings": self.settings.to_dict(),
            "run_id": (
                self.orchestrator.run_id
                if self.orchestrator is not None
                else self.run_id
            ),
            "claude_session_id": (
                self.orchestrator.claude_session_id
                if self.orchestrator is not None
                else self.claude_session_id
            ),
            "codex_session_id": (
                self.orchestrator.codex_session_id
                if self.orchestrator is not None
                else self.codex_session_id
            ),
        }

    def _intake_question_result(self, question: str, seat: str) -> dict:
        """第一格有話要問使用者。

        `review_status` 用 `ASK_USER`，因為畫面上「有一題在等你」的整套動線
        （橫幅、回答按鈕、LINE 通知）已經綁在那個值上。**不另立一個新的等待
        狀態**——第二種等待就是第二套要記得同步的畫面，而漏掉的那一次，使用
        者會看到一個沒有回答按鈕的問題。
        """
        shown = shown_name(seat)
        return self._early_stop_result(
            workflow_status="INTAKE_ASKING",
            review_status="ASK_USER",
            note=f"【{shown}】在動手之前要先跟你確認一件事。",
            question=question,
            asked_by=seat,
        )

    def _intake_invalid_result(self, problem: str, seat: str) -> dict:
        """第一格沒有照格式回答，所以引擎分不出它是在問還是在交件。

        停下來，**不猜**。猜錯的兩種結局都很貴：把命令列輸出丟到使用者臉上，
        或者拿一個問句當驗收標準讓後面每一格照著做。
        """
        shown = shown_name(seat)
        return self._early_stop_result(
            workflow_status="INTAKE_INVALID",
            review_status="NOT_RUN",
            note=f"【{shown}】{problem}",
            question="",
        )

    def _early_stop_result(
        self,
        *,
        workflow_status: str,
        review_status: str,
        note: str,
        question: str,
        asked_by: str = "",
    ) -> dict:
        """「還沒開始就該停」那一族結局共用的形狀。

        寫成一份而不是各抄一次：這個字典有十九個鍵，抄第三次一定會漏掉一個，
        而漏掉的那一個在畫面上讀起來就是「這一欄本來就沒有值」。
        """
        return {
            "project_path": str(self.project_path),
            "workflow_status": workflow_status,
            "claude_success": False,
            "tests_success": False,
            "tests_status": TESTS_NOT_RUN,
            "codex_success": False,
            "review_status": review_status,
            "fix_attempts": self.starting_fix_attempts,
            "claude_output": "",
            "claude_error": "",
            "test_output": note,
            "codex_output": "",
            "codex_log": "",
            "review_question": question,
            # **是哪一格在問。**老闆 2026-08-27：畫面上那顆鈕要寫
            # 「回覆 XXX」，而 XXX 就是這一欄。
            #
            # 在這之前，問**使用者**的那一題從來沒有帶過名字——
            # node 對 node 的討論有 `asked_by`（`orchestrator.py:1074`），
            # 輪到問他的時候那個名字就掉了。於是畫面只能寫一句「回答問題」，
            # 而他要自己去猜是誰在等。
            #
            # **答不出來就留空，不准猜。**空的時候畫面寫「回覆」，
            # 不是編一個名字出來——一個編出來的名字，跟一個對的名字，
            # 在畫面上長得一模一樣。
            "review_asked_by": asked_by,
            # 為什麼停下來，寫給人看的那一句。**跟 test_output 分開**：
            # test_output 那一欄在別的結局裡裝的是真的測試輸出，畫面拿它當
            # 說明的話，兩種完全不同的東西會出現在同一個位置。
            "outcome_detail": note,
            "memory_update": {},
            "ai_settings": self.settings.to_dict(),
            "run_id": (
                self.orchestrator.run_id
                if self.orchestrator is not None
                else self.run_id
            ),
            "claude_session_id": (
                self.orchestrator.claude_session_id
                if self.orchestrator is not None
                else self.claude_session_id
            ),
            "codex_session_id": (
                self.orchestrator.codex_session_id
                if self.orchestrator is not None
                else self.codex_session_id
            ),
        }

    def _verification_not_declared_result(self, problem: str) -> dict:
        """The 「沒得驗」 ending - deliberately not shaped like a failure.

        `tests_status` is `NOT_DECLARED`, a third value beside `PASSED` and
        `FAILED`: everything downstream (the status pill, the review, the
        run ledger) has to be able to tell 「還沒有人說要怎麼驗」 from
        「驗過了，紅的」, and a boolean cannot carry that.
        """
        return {
            "project_path": str(self.project_path),
            "workflow_status": "VERIFY_NOT_CONFIGURED",
            "claude_success": False,
            "tests_success": False,
            "tests_status": TESTS_NOT_DECLARED,
            "codex_success": False,
            "review_status": "NOT_RUN",
            "fix_attempts": self.starting_fix_attempts,
            "claude_output": "",
            "claude_error": "",
            "test_output": problem,
            "verification_problem": problem,
            "codex_output": "",
            "codex_log": "",
            "review_question": "",
            "memory_update": {},
            "ai_settings": self.settings.to_dict(),
            "run_id": (
                self.orchestrator.run_id
                if self.orchestrator is not None
                else self.run_id
            ),
            "claude_session_id": (
                self.orchestrator.claude_session_id
                if self.orchestrator is not None
                else self.claude_session_id
            ),
            "codex_session_id": (
                self.orchestrator.codex_session_id
                if self.orchestrator is not None
                else self.codex_session_id
            ),
        }

    def _planning_result(
        self,
        workflow_status: str,
        codex_result,
        claude_result,
    ) -> dict:
        return {
            "project_path": str(self.project_path),
            "workflow_status": workflow_status,
            "claude_success": (
                bool(claude_result.success)
                if claude_result is not None
                else True
            ),
            "tests_success": False,
            "tests_status": TESTS_NOT_RUN,
            "codex_success": bool(codex_result.success),
            "review_status": "PLAN",
            "fix_attempts": 0,
            "claude_output": (
                claude_result.stdout
                if claude_result is not None
                else ""
            ),
            "claude_error": (
                claude_result.stderr
                if claude_result is not None
                else ""
            ),
            "test_output": "",
            "codex_output": codex_result.stdout,
            "codex_log": codex_result.stderr,
            "review_question": "",
            "ai_settings": self.settings.to_dict(),
            "run_id": self.orchestrator.run_id if self.orchestrator else "",
            "claude_session_id": (
                self.orchestrator.claude_session_id
                if self.orchestrator else self.claude_session_id
            ),
            "codex_session_id": (
                self.orchestrator.codex_session_id
                if self.orchestrator else self.codex_session_id
            ),
        }

    def _build_result(
        self,
        workflow_status: str,
        review_status: str,
        fix_attempts: int,
        claude_result,
        test_result,
        codex_result,
    ) -> dict:
        return {
            "project_path": str(self.project_path),
            "workflow_status": workflow_status,
            # **`claude_result` 可能是 None，跟 `test_result` 一樣。**
            #
            # 兩個結局根本沒有跑到那一步：`NO_WORKING_SEAT`（這條線上一格
            # 都不會寫檔案）與 `NO_REVIEWER`。旁邊那一行 2026-08-2x 就加了
            # None 防護，這一行沒有——於是 `_no_worker_result()` 自己會丟
            # `AttributeError`，**而它的整支 docstring 都在解釋那個結局該
            # 怎麼收場**。
            #
            # 使用者看到的差別：本來該是「沒有人做事——這條線上沒有一格會
            # 寫檔案」（`app/i18n.py`），實際是一整坨紅色的 traceback。
            # 那句話早就寫好了，**只是到不了**。
            #
            # 2026-08-25 之前 `tests/` 裡沒有任何一條碰過 `NO_WORKING_SEAT`。
            "claude_success": (
                claude_result.success if claude_result is not None else False
            ),
            "tests_success": (
                test_result.passed if test_result is not None else False
            ),
            # The verdict comes from the project's declared rule, computed
            # once in `Orchestrator.run_tests`. Re-deriving it here from a
            # boolean is how 「逾時」 used to arrive downstream wearing
            # 「測試紅了」's clothes.
            "tests_status": (
                test_result.status
                if test_result is not None
                else TESTS_NOT_RUN
            ),
            "codex_success": (
                codex_result.success if codex_result is not None else False
            ),
            "review_status": review_status,
            "fix_attempts": fix_attempts,
            # 同上。`codex_result` 這一側**每一處都防了 None**
            # （:1462／:1474／:1477），`claude_result` 一處都沒有——
            # 而兩個參數的型別宣告是一樣的。
            # **不對稱本身就是那個 bug 的樣子。**
            "claude_output": (
                claude_result.stdout if claude_result is not None else ""
            ),
            "claude_error": (
                claude_result.stderr if claude_result is not None else ""
            ),
            "test_output": (
                (test_result.stdout or test_result.stderr)
                if test_result is not None
                else ""
            ),
            "codex_output": (
                codex_result.stdout if codex_result is not None else ""
            ),
            "codex_log": (
                codex_result.stderr if codex_result is not None else ""
            ),
            "memory_update": {},
            "ai_settings": self.settings.to_dict(),
            "run_id": (
                self.orchestrator.run_id
                if self.orchestrator is not None
                else self.run_id
            ),
            "claude_session_id": (
                self.orchestrator.claude_session_id
                if self.orchestrator is not None
                else self.claude_session_id
            ),
            "codex_session_id": (
                self.orchestrator.codex_session_id
                if self.orchestrator is not None
                else self.codex_session_id
            ),
        }

    def _complete(self, result: dict) -> None:
        # 這一次收場屬於哪一組。**在這裡蓋，因為這裡是唯一的出口**——蓋在
        # 畫面那一側的話，落地的紀錄（時間軸、workflow-result.json）沒有它，
        # 而事後回頭看一次執行的人，看到的正是那份紀錄。
        #
        # 認不出來就不寫。守衛測試會為了「認不出來」變紅，而一次真的執行
        # 不該因為分類表少一行就死掉——那條原則從 RunStamper 第一天就在。
        try:
            result["outcome_group"] = outcomes.describe(result).group
        except outcomes.UnknownOutcome:
            result["outcome_group"] = ""

        if self.orchestrator is not None:
            result.setdefault("run_id", self.orchestrator.run_id)
            result.setdefault(
                "claude_session_id",
                self.orchestrator.claude_session_id,
            )
            result.setdefault(
                "codex_session_id",
                self.orchestrator.codex_session_id,
            )
            result.setdefault("ai_settings", self.settings.to_dict())
            self.orchestrator.timeline.finalize_run(
                self.orchestrator.run_id,
                result,
            )

        result_path = self.project_path / ".aicc" / "workflow-result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.completed.emit(result)
