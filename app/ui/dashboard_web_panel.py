"""Dashboard panel — renders dashboard_v5.html with real project state.

Why this panel is a web view when the Session panel stays native Qt: v5's visual
language is built almost entirely out of CSS features QSS does not have -
repeating-linear-gradient (the plaid), clip-path (the ribbon), @keyframes (the
pulse) and multi-layer box-shadow (the double glow). Three attempts to
reproduce it with QSS + paintEvent all landed at "close but visibly wrong", each
time somewhere different. Running the real file in Chromium is not an
approximation of v5 - it is v5.
"""
from __future__ import annotations

from app.i18n import t

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton

from app.services import ui_preferences
from app.services.context_ledger import ContextLedger
from app.services.handover import handover_steps, outstanding
from app.services.project_dashboard import ProjectDashboardItem, ProjectDashboardService
from app.ui.web_panel import WebViewPanel
from app.services import relative_time

# v5 has three lanes; the service reports five statuses. `state` picks the lane,
# `tone` carries the real meaning - an errored project sits in the idle lane but
# renders rose, not grey. (tone is optional; omitting it falls back to state.)
# **拿 key 不拿字。**這張表是用「那個專案現在的狀態字串」去查的，而那個字串
# 是 `project_dashboard` 用 `t(...)` 取出來的——存中文的話，介面翻成英文的
# 那一刻**每一個專案都會掉進 idle 那條泳道**，而畫面上完全正常
#（那正是【`WRITES_TO` 拿中文當查詢的鍵】那個病的第二個入口）。
#
# 兩邊都從同一個 key 取，所以換語言的時候兩邊一起換。
_LANE_BY_KEY: tuple[tuple[str, tuple[str, str]], ...] = (
    ("outcome.group.ask", ("gate", "gate")),
    ("run.status.running", ("run", "run")),
    ("dash.state.idle", ("idle", "idle")),
    ("dash.state.done", ("idle", "done")),
    ("dash.state.error", ("idle", "error")),
    # 「要你處理」：跑完了，**沒有人在線上等你回話，但有一件事等你動手**。
    #
    # 2026-08-24 之前這一項不在表上，於是 16 種這樣的收場全部掉進底下那個
    # 預設值——畫成灰色的「閒置」，跟「從來沒跑過」排在同一條泳道裡。
    # 卡片上的字寫著「要你處理」，而它躺在標著「閒置」的那一段底下。
    #
    # **泳道走 `gate`（等你決策），色調另外給一個**：泳道回答「急不急」，
    # 而它跟需要決策的那些一樣急；色調回答「是哪一種」，而它**不該用
    # 金色那一種**——金色會跳（`.star.gate .core` 有 animation），
    # 而跳動這件事 `outcomes.WAITING_GROUPS` 只留給「AI 還在線上等」。
    ("dash.needs_you", ("gate", "needs_you")),
    # 「中斷未完成」：上一次執行**宣稱自己還在跑，而沒有東西替它背書**。
    #
    # 泳道走 `gate` 而不是 `run`：`run` 那條泳道的卡片會跳（`.star.run .core`
    # 有 animation），而跳動的意思是「**現在正在動**」。這一種沒有在動——
    # 它已經停了，而且要人動手才會再動。放在 `run` 就是這次要修的那個病本身：
    # **真的在跑的跟上次跑到一半死掉的長得一模一樣。**
    #
    # 色調沿用 `needs_you` 而不是新開一個：新色調要頁面那一側有一條
    # `.tag.<色調>`，而 `app/ui/web/*` 是建置產物、這一輪不動。沿用的代價是
    # 兩種狀態同色，而**它們的標籤字不一樣**（「要你處理」／「中斷未完成」），
    # 卡片背面還多一列講下一步——差別看得見。
    ("dash.state.interrupted", ("gate", "needs_you")),
)

# 那一次中斷停在哪 → 畫面上那句話。
#
# **寫成常數字串的表，不是 `t(f"dash.interrupted.{reason}")`。**
# 組出來的 key 躲得過 `test_every_key_used_anywhere_exists`——那條檢查只看
# 得懂 `t("字面常數")`。躲過去的下場是打錯一個字，執行到那一行才炸，而那
# 一行只在「上一次中斷了」的時候才走得到。
_INTERRUPTED_TEXT_KEYS: dict[str, str] = {
    ProjectDashboardService.INTERRUPTED_NEVER_STARTED:
        "dash.interrupted.never_started",
    ProjectDashboardService.INTERRUPTED_STOPPED_MIDWAY:
        "dash.interrupted.stopped_midway",
}


class UnmappedLane(KeyError):
    """有一種狀態沒有人替它決定畫在哪。**不給預設值。**"""


def lane_for(status_key: str) -> tuple[str, str]:
    """這個狀態畫在哪一條泳道、用什麼色調。

    收的是 **key**（`dash.state.idle`），不是畫面上那個字。2026-08-24 之前
    收的是字，於是這個函式在英文版上線的那一天會全部落到預設值——
    而預設值不會報錯，它只是把每一個專案都畫成灰色的待命。

    ## 那個預設值 2026-08-24 拿掉了

    原本查不到就回 `("idle", "idle")`，而 docstring 替它辯護：
    「一個沒見過的狀態畫成灰色的『待命』，比畫成綠色的『完成』誠實」。

    **那句話是錯的，而且它正好是被否決過的那個形狀**
    （`DECISIONS.md`：「否決：把漏掉的收場補進畫面那條 elif 鏈——補完還是
    同一個形狀，第十二種出現時再漏一次」）。掉進預設值的不是「沒見過的
    狀態」，是**16 種真的會發生、而且需要他動手**的收場。

    現在丟例外。畫面那一側 `_card()` 接住它（首頁不能因為一種新收場整頁掛掉），
    但**接住的地方會把它記下來**，而 `test_every_status_has_a_lane`
    在測試階段就會紅——新增一種收場而沒有替它決定畫法，跑測試就知道。
    """
    for key, lane in _LANE_BY_KEY:
        if status_key == key:
            return lane
    raise UnmappedLane(status_key)
WEEK_SECONDS = 7 * 24 * 3600.0


def _compact_count(characters: int) -> str:
    """Character counts, shortened for a narrow rail box.

    Characters rather than tokens: a token count would need a real tokeniser,
    and dividing by a constant produces a number that looks exact and is not.
    """
    if characters >= 10_000:
        return f"{characters / 10_000:.1f} 萬字"
    if characters >= 1_000:
        return f"{characters / 1_000:.1f}k 字"
    return f"{characters} 字"


def _age_seconds(iso_timestamp: str) -> float:
    """Seconds since `iso_timestamp`. Unknown timestamps sort to 'very old'."""
    if not iso_timestamp:
        return WEEK_SECONDS
    try:
        moment = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return WEEK_SECONDS
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.now().astimezone().tzinfo)
    delta = datetime.now(timezone.utc) - moment.astimezone(timezone.utc)
    return max(0.0, delta.total_seconds())


def _relative(seconds: float) -> str:
    return relative_time.describe_age(seconds)


def _short_path(path: str, keep: int = 2) -> str:
    parts = [p for p in path.replace("/", "\\").split("\\") if p]
    return "…\\" + "\\".join(parts[-keep:]) if len(parts) > keep else path


# 佇列那五個控制項送過來的動作。**列成一份常數而不是散在 if 裡**，
# 因為畫面那一側要照著它做，而一份對得上的清單比五個散落的字串好對。
#
# N8（2026-08-24）：引擎這五個操作一直都在，接線也接好在等，**而畫面上
# 是一份唯讀名單**。頁面設計那一輪查到的六件之一。
QUEUE_ACTIONS = ("queue_add", "queue_remove", "queue_pause", "queue_limit")

# 卡片右下角那顆按鈕上的字。**列成一張表而不是 `.get(state, "接續")`**：
# 原本的預設值讓「沒見過的狀態」跟「接續」長得一模一樣，而那是
# 【有預設值的分類等於沒有分類】。這裡保留預設，但預設是**明寫的一個
# key**，不是一個藏在 `.get()` 第二個參數裡的字面值——差別在於翻譯的人
# 掃 `i18n.py` 就看得到它。
_ACTION_LABEL_KEYS = {"gate": "dash.action.decide", "run": "dash.action.view"}

# 兩個廠牌的名字並排。**不是介面文字**——「Claude × Codex」在任何語言
# 都是這五個字，翻譯它等於把產品名字翻掉。
PAIRED_LABEL = "Claude × Codex"


class DashboardWebPanel(WebViewPanel):
    """Drop-in replacement for DashboardPanel's public surface."""

    PAGE = "dashboard_v5.html"

    project_selected = Signal(str)
    queue_capacity_available = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.service = ProjectDashboardService()
        self._projects: list = []
        self._items: list[ProjectDashboardItem] = []
        self._summary = None

        # "記得存使用者偏好" - which groups are folded survives a restart.
        self._settings = ui_preferences.store()
        raw = self._settings.value("dashboard/collapsed", "idle")
        folded = {k for k in str(raw).split(",") if k}
        self._collapsed = {k: k in folded for k in ("gate", "run", "idle")}
        # 畫不出來的那幾種收場。空的才正常——`test_every_status_has_a_lane`
        # 守著它，而這一格是給執行期留痕用的（測試漏掉的話至少查得到）。
        self._unmapped: set[str] = set()

        # Compatibility shim: MainWindow wires DashboardPanel.refresh_button, so
        # swapping the panel stays a one-line change in main_window.py.
        self.refresh_button = QPushButton(self)
        self.refresh_button.hide()

    # ---- data -----------------------------------------------------------
    def refresh(self, projects: list) -> None:
        self._projects = projects
        self._summary, self._items = self.service.build(projects)
        self.push_state()

    @staticmethod
    def _handover_detail(project_path: str) -> dict:
        """接手待辦，只在還有沒做完的時候回一個 key。

        列的是**還沒做的那幾件**，不是「4 件裡完成 2 件」。進度分數會在待辦
        清空之前就先讓人覺得這件事已經在處理了，而這裡每一項都要人動手才會
        消失——沒有人動手，它就該一直在。
        """
        try:
            pending = outstanding(handover_steps(Path(project_path)))
        except Exception:
            return {}
        if not pending:
            return {}
        return {t("dash.handoff_todo"): "、".join(step.title for step in pending)}

    def collect_state(self) -> dict:
        projects = []
        gate_today = 0
        for item in self._items:
            try:
                state, tone = lane_for(item.status)
            except UnmappedLane:
                # **首頁不能因為一種新收場整頁掛掉**——它是使用者看見的第一頁。
                # 所以這裡接住，畫成最保守的那一種（灰、不跳、不說做完了），
                # **但接住的地方留一筆**，而 `test_every_status_has_a_lane`
                # 在測試階段就會紅。
                #
                # 這跟原本那個「查不到就回 idle」的差別是：那時候**沒有任何
                # 東西會講**，16 種要他動手的收場靜靜躺在「閒置」底下三個月
                # 都不會有人發現。
                state, tone = ("idle", "idle")
                self._unmapped.add(item.status)
            age = _age_seconds(item.last_updated)
            relative = _relative(age)
            paired, gates = self._project_facts(item)
            gate_today += gates
            projects.append(
                {
                    "id": item.path,
                    "name": item.name,
                    "path": _short_path(item.path),
                    "state": state,
                    "tone": tone,
                    "state_label": item.status_label,
                    "age_s": age,
                    "paired": paired,
                    "claude": item.last_task or t("run.status.not_started"),
                    "codex": self._codex_text(item, paired),
                    # No progress metric exists in the data model yet; the bar is
                    # omitted rather than faked (v5 skips it when this is null).
                    "progress": None,
                    "elapsed": relative,
                    "elapsed_label": t("dash.last_activity"),
                    "sub": relative + ("" if paired else t("dash.unpaired_suffix")),
                    "action": "review" if state == "gate" else "open",
                    "action_label": t(_ACTION_LABEL_KEYS.get(state, "dash.action.resume")),
                    "detail": {
                        t("dash.detail.state"): item.status_label,
                        # Only when there is one. The page renders whatever
                        # keys arrive, so an always-present 「失敗在哪一格：—」
                        # would put a dash on every healthy card.
                        **(
                            {t("dash.failed_at"): item.failed_step}
                            if item.failed_step
                            else {}
                        ),
                        **(
                            {t("dash.waiting_for_you"): item.waiting_question}
                            if item.waiting_question
                            else {}
                        ),
                        # 上一次執行中斷了的時候才有。**它要回答的是「所以
                        # 我現在該做什麼」**——只寫「中斷未完成」等於把人送去
                        # 翻資料夾，而該翻不該翻這件事機器已經知道了。
                        **(
                            {
                                t("dash.interrupted_at"): t(
                                    _INTERRUPTED_TEXT_KEYS[item.interrupted_reason]
                                )
                            }
                            if item.interrupted_reason in _INTERRUPTED_TEXT_KEYS
                            else {}
                        ),
                        # 護欄擋下了他寫的哪一句（❓15）。**印的是他寫的
                        # 原話**，不是一句「有指示被忽略」——後者等於叫他
                        # 自己回頭猜是哪一句，而那一句機器手上就有。
                        **(
                            {
                                t("dash.blocked_by_rule"): "；".join(
                                    t(
                                        "dash.blocked_pair",
                                        quote=quote,
                                        rule=rule,
                                    )
                                    for quote, rule in item.blocked_instructions
                                )
                            }
                            if item.blocked_instructions
                            else {}
                        ),
                        # 搬過來還沒補完的東西（ROADMAP P5）。同樣是「有才畫」：
                        # 絕大多數專案不是搬過來的，一個常駐的「接手待辦：—」
                        # 會讓真的有待辦的那一次消失在背景裡。
                        **self._handover_detail(item.path),
                        t("dash.last_activity"): relative,
                        t("dash.detail.pairing"): (
                            PAIRED_LABEL if paired else t("dash.unpaired")
                        ),
                        t("dash.done_today"): str(item.completed_today),
                    },
                }
            )

        counts = {"run": 0, "gate": 0, "idle": 0}
        for entry in projects:
            counts[entry["state"]] += 1

        queue_state = self.service.queue_store.load()
        name_by_path = {item.path: item.name for item in self._items}
        queue = [name_by_path.get(p, _short_path(p)) for p in queue_state.queued_paths]
        # 跟 `queue` **逐格對齊**的路徑。畫面上顯示的是名字，但「把這一列
        # 移除」要送得回一個認得出來的東西——而名字會撞（兩個專案資料夾
        # 同名是很平常的事）。
        #
        # 為什麼是兩個平行陣列而不是一串物件：頁面現在的那一行是
        # `q.map((t,i)=> ... ${t} ...)`，換成物件會當場印出 `[object Object]`。
        # **一個看得見的退步，換一個比較好看的形狀，不划算。**
        # 他們重寫那個 box 的時候會併成一串物件——見交接檔。
        queue_paths = list(queue_state.queued_paths)

        limit = max(1, self._summary.concurrency_limit if self._summary else 1)
        # `note` may be null and the page will compute the usual sentence; it is
        # only overridden for the paused case, which the page cannot know about.
        note = t("queue.paused_note") if queue_state.paused else None

        # Why each project is not starting (ROADMAP P4). Keyed by the same id
        # the project cards use, so the page attaches it to the card rather
        # than drawing a separate list of names to cross-reference.
        #
        # From the scheduler's own pass, not a second set of checks - see
        # `ProjectDashboardService.start_readiness`. A project that *can*
        # start has no entry at all, which is how the page tells 「可以跑」
        # from 「跑不了但我沒說原因」.
        blocked = {
            block.path: {"reason": block.reason, "detail": block.detail}
            for block in self.service.start_readiness(self._projects).blocked
        }
        # The subset worth putting on a card. `not_queued` is left out on
        # purpose: it is true of most projects most of the time, so a row
        # saying it would appear on nearly every card and mean nothing. The
        # actionable case is 「你排了它，它卻沒動」 - which is exactly the
        # queued-but-blocked set.
        #
        # The full map used to be pushed as well, "for the page to use however
        # it wants". It never read it, and the offer was a trap: the sentence
        # above says the page must *not* use it however it wants, because
        # rendering `not_queued` puts a meaningless row on nearly every card.
        # An unread field that invites the mistake its own comment warns about
        # is not future-proofing. Rebuilding it is one line off
        # `start_readiness`, which is where the answer lives anyway.
        card_blocks = {
            path: entry["detail"]
            for path, entry in blocked.items()
            if entry["reason"] != "not_queued"
        }
        for entry in projects:
            reason = card_blocks.get(entry["id"])
            if reason:
                entry["detail"][t("dash.why_not_moving")] = reason

        return {
            "mode": self._mode,
            "collapsed": self._collapsed,
            "projects": projects,
            "concurrency": {"used": counts["run"], "limit": limit, "note": note},
            "queue": queue,
            "queue_paths": queue_paths,
            "queue_paused": queue_state.paused,
            "today": [
                [t("dash.today.completed"), str(self._summary.completed_today if self._summary else 0)],
                ["Decision Gate", str(gate_today)],
                [t("dash.today.retried"), str(self._summary.failed_today if self._summary else 0)],
            ]
            + self._context_rows(),
        }

    def _context_rows(self) -> list[list[str]]:
        """Today's context accounting, summed over every project.

        This is the 「節省 context」box the design asked for, and it took a
        recorder to earn it: the original spec's percentage had no source, so
        the box was left out rather than filled with a plausible number. What is
        shown now comes from ContextLedger, which stores what each run actually
        carried and how much a later round skipped re-sending.

        Nothing is shown on a day with no runs. A row reading 0 would suggest
        the day's context was measured and came to nothing, when in fact there
        was nothing to measure.
        """
        sent = skipped = runs = 0
        for item in self._items:
            try:
                summary = ContextLedger(Path(item.path)).summary(days=1)
            except Exception:
                # Same policy as _project_facts: an unreadable project folder is
                # a gap in the total, not a broken dashboard.
                continue
            runs += summary["runs"]
            sent += summary["sent_characters"]
            skipped += summary["skipped_characters"]

        if not runs:
            return []

        rows = [[t("dash.context_carried"), _compact_count(sent)]]
        if skipped:
            # Only when a run actually reused a session. On a single-round day
            # this row is absent rather than zero - "省下 0" reads like a
            # failure, when it means the situation never arose.
            rows.append([t("dash.resend_saved"), _compact_count(skipped)])
        return rows

    def _project_facts(self, item: ProjectDashboardItem) -> tuple[bool, int]:
        """(paired, decision gates today) from one pass over the project's runs.

        `ProjectDashboardItem.waiting_count` is a *current* count, not a daily
        tally, so it cannot be relabelled as "今日 Decision Gate" - the date
        filter happens here instead of changing the shared service.
        """
        try:
            from app.services.timeline_store import TimelineStore

            runs = TimelineStore(Path(item.path)).list_runs()
        except Exception:
            # A project folder can be missing or unreadable; that is an
            # "unknown", not a crash of the whole dashboard.
            return False, 0

        latest = runs[0] if runs else None
        paired = bool(latest and latest.claude_session_id and latest.codex_session_id)

        today = datetime.now().date()
        gates = 0
        for run in runs:
            if run.status not in self.service.WAITING_STATUSES:
                continue
            stamp = run.updated_at or run.created_at
            try:
                if datetime.fromisoformat(stamp).date() == today:
                    gates += 1
            except (TypeError, ValueError):
                continue
        return paired, gates

    @staticmethod
    def _codex_text(item: ProjectDashboardItem, paired: bool) -> str:
        if not paired:
            return t("dash.unpaired")
        if item.is_queued:
            return t("dash.queued")
        if item.status == "run.status.running":
            return t("run.status.running")
        # 中斷的那一種**不准落回「待命」**——那正是這次要修的形狀：
        # 「上次跑到一半死掉」跟「什麼事都沒有」長得一樣。
        if item.status == "dash.state.interrupted":
            return t("dash.state.interrupted")
        return t("dash.state.idle")

    # ---- actions from the page -----------------------------------------
    def handle_action(self, kind: str, arg: str) -> None:
        if kind.startswith("collapse:"):
            group = kind.split(":", 1)[1]
            if group in self._collapsed:
                self._collapsed[group] = arg not in ("false", "0", "")
                folded = ",".join(k for k, v in self._collapsed.items() if v)
                self._settings.setValue("dashboard/collapsed", folded)
            return
        if kind in ("focus", "open", "review"):
            self.project_selected.emit(arg)
            return
        if kind in QUEUE_ACTIONS:
            self._queue_action(kind, arg)

    def _queue_action(self, kind: str, arg: str) -> None:
        """佇列那五個控制項。

        **每一種都要 `queue_capacity_available`**，包含「加進佇列」——
        佇列空著而且有位子的時候，加進去那一刻就該開始跑，不是等下一次
        有別的事情發生。

        漏掉那個訊號的症狀很具體：他按了「恢復」，畫面上佇列還在那裡，
        而**什麼都沒有開始跑**，直到他下一次做別的事。
        """
        store = self.service.queue_store
        if kind == "queue_add":
            if not arg:
                return
            store.enqueue(Path(arg))
        elif kind == "queue_remove":
            if not arg:
                return
            store.remove(Path(arg))
        elif kind == "queue_pause":
            store.set_paused(arg not in ("", "0", "false"))
        elif kind == "queue_limit":
            try:
                limit = int(arg)
            except (TypeError, ValueError):
                # 送過來的不是數字**就不要動它，也不准炸**。
                #
                # 這裡本來的說明寫著「存一個 0 進去的話那個清單永遠沒有
                # 位子」——**那句話是錯的**：`set_concurrency_limit` 自己
                # 夾在 1~5。（負向驗證那一條是綠的才發現。）
                #
                # 真正的風險是這是一個**動作處理器**：從畫面按下去然後在
                # Python 這一側丟例外，使用者看到什麼沒有人保證。
                return
            store.set_concurrency_limit(limit)
        else:
            return

        self.refresh(self._projects)
        self.queue_capacity_available.emit()
