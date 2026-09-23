"""一次執行**怎麼收場**，以及畫面該說什麼。

## 為什麼是一個模組，不是四張表

一次執行有二十幾種收場方式。今天知道它們的地方有四個：執行控制列、首頁泳道、
時間軸、匯出的報告。**四個地方各自維護一張表，第二十五種出現的時候會有四份
互相漂開的答案**——而使用者不知道該信哪一個。

所以這裡是**唯一**知道「這個收場長什麼樣」的地方。大門只有一扇：

    describe(result) -> Outcome

## 沒有預設值，這是刻意的

`describe()` 遇到不認識的收場會**丟例外**，不會回一個「其他」。

理由是 2026-08-23 頁面設計那一輪查到的事：畫面原本有一條 `else`，把十種
收場一律畫成玫瑰色、旁邊印英文碼。**那條 else 讓「漏掉一種」看起來跟「處理過
了」一模一樣**，所以它漏了十種，漏了很久，沒有任何一條測試變紅。

有預設值的分類，等於沒有分類。

## 五組是按「使用者接下來要動哪一樣東西」分的

不是按引擎的狀態碼分。狀態碼有二十幾種，使用者實際的動作只有五種——
而同一個狀態碼（`NOT_DECIDABLE`）底下的六種判定，使用者要做的事**完全相反**：
「缺兩個章」是再跑一次就好，「有檢查是紅的」是去別的地方修。照狀態碼分，
這兩件會被畫成同一格。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.i18n import t

# ---------------------------------------------------------------------------
# 五組（加上「沒事」）。組名是**使用者要做的事**，不是狀態的分類名。
# ---------------------------------------------------------------------------

# 跑完了，沒有人在等你。
GROUP_DONE = "done"
# AI 還在線上等你回一句話。**只有這一組會脈動。**
GROUP_ASK = "ask"
# 要你決定怎麼做——動的是交辦內容、驗收標準這些**文字**。
GROUP_DECIDE = "decide"
# 少一項設定——動的是陣容、驗證方式這些**設定**。
GROUP_SETTING = "setting"
# 什麼都不用改，再跑一次就好。
GROUP_RERUN = "rerun"
# 這裡沒壞，是被別的地方的紅燈擋住。**不能併進「再跑一次」**——併進去使用者
# 會按重跑，而重跑一定再紅一次，那是叫人去按一顆一定會失敗的按鈕。
GROUP_FIX = "fix"

# 組名的 key，**不是組名本身**。存字的話它會在 import 的當下凍住。
GROUP_LABEL_KEYS = {
    GROUP_DONE: "outcome.group.done",
    GROUP_ASK: "outcome.group.ask",
    GROUP_DECIDE: "outcome.group.decide",
    GROUP_SETTING: "outcome.group.setting",
    GROUP_RERUN: "outcome.group.rerun",
    GROUP_FIX: "outcome.group.fix",
}

# 這一組**還有人在等**。畫面上的兩種金色靠會不會動分辨（`aicc.css` 自己的
# 註解），這裡是那條線的唯一依據。
WAITING_GROUPS = frozenset({GROUP_ASK})


@dataclass(frozen=True)
class Outcome:
    """一次執行收場的樣子。

    `status` 留著是給回溯用的——**畫面不准印它**。客群不是工程師，
    螢幕上出現 `NOT_RUN` 這種字對使用者沒有任何意義。
    """

    status: str
    label: str
    style: str
    group: str
    stage: str

    @property
    def waiting(self) -> bool:
        return self.group in WAITING_GROUPS

    @property
    def group_label(self) -> str:
        return t(GROUP_LABEL_KEYS[self.group])


# ---------------------------------------------------------------------------
# 表：一種收場一行。`stage` 是預設說明——結果裡有更具體的就用結果裡那句。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    """一種收場一行。

    `label_key` 與 `stage_key` 存的是 `app/i18n.py` 的 key，**不是那句話本身**。
    這張表在模組載入的當下就建好了，存字的話那些字就永遠是 import 那一刻的
    語言；而 `describe()` 每次被叫的時候才 `t()`，換語言就會跟著換。
    """

    label_key: str
    style: str
    group: str
    stage_key: str
    # 結果字典裡哪個欄位裝著更具體的說明（有的話蓋掉 `stage`）。
    detail_key: str = ""


_TABLE: dict[str, _Row] = {
    # ---- 跑完了 ----------------------------------------------------------
    "COMPLETED": _Row("outcome.COMPLETED.label", "done", GROUP_DONE, "outcome.COMPLETED.stage"),
    "PLANNING_COMPLETED": _Row(
        "outcome.PLANNING_COMPLETED.label",
        "done",
        GROUP_DONE,
        "outcome.PLANNING_COMPLETED.stage",
    ),
    # ---- 等你回一句話 ----------------------------------------------------
    "ASK_USER": _Row("outcome.group.ask", "gated", GROUP_ASK, "outcome.ASK_USER.stage"),
    "INTAKE_ASKING": _Row(
        "outcome.group.ask",
        "gated",
        GROUP_ASK,
        "outcome.INTAKE_ASKING.stage",
        detail_key="review_question",
    ),
    # ---- 要你決定怎麼做（動的是文字）-------------------------------------
    "PLAN_REJECTS_CRITERIA": _Row(
        "outcome.PLAN_REJECTS_CRITERIA.label",
        "partial",
        GROUP_DECIDE,
        "outcome.PLAN_REJECTS_CRITERIA.stage",
        detail_key="outcome_detail",
    ),
    "CRITERIA_DEADLOCK": _Row(
        "outcome.CRITERIA_DEADLOCK.label",
        "partial",
        GROUP_DECIDE,
        "outcome.CRITERIA_DEADLOCK.stage",
        detail_key="outcome_detail",
    ),
    "CONSULT_STUCK": _Row(
        "outcome.CONSULT_STUCK.label",
        "partial",
        GROUP_DECIDE,
        "outcome.CONSULT_STUCK.stage",
        detail_key="outcome_detail",
    ),
    # 這一輪一個檔案都沒有改到，所以沒有東西可以拍板（`steps/approval.py`
    # 的 `_nothing_was_produced`）。
    #
    # **歸「要你決定」不歸「再跑一次」。**分組的判準是那一組自己的定義：
    # 「什麼都不用改，再跑一次就好」。這一種不是——同一份交辦內容再跑一次，
    # 最可能的結果是再一次什麼都沒做出來，而 `GROUP_RERUN` 在首頁上就是
    # 一顆重跑按鈕。**那是叫人去按一顆會給出同樣答案的按鈕**，跟隔壁
    # `GROUP_FIX` 那段註解講的是同一件事。要動的是交辦的內容，所以歸這一組。
    #
    # 也**不歸 `error`**：程式沒有壞、測試也過了。畫成紅的會讓他去修一個
    # 沒壞的東西，而且會被算進首頁那句「今日錯誤 N 件」（`ERROR_STATUSES`
    # 數的是 `style == "error"`）。
    "NOTHING_TO_APPROVE": _Row(
        "outcome.NOTHING_TO_APPROVE.label",
        "partial",
        GROUP_DECIDE,
        "outcome.NOTHING_TO_APPROVE.stage",
        detail_key="outcome_detail",
    ),
    # ---- 少一項設定 ------------------------------------------------------
    "CRITERIA_NO_OWNER": _Row(
        "outcome.CRITERIA_NO_OWNER.label",
        "partial",
        GROUP_SETTING,
        "outcome.CRITERIA_NO_OWNER.stage",
        detail_key="outcome_detail",
    ),
    "NO_WORKING_SEAT": _Row(
        "outcome.NO_WORKING_SEAT.label",
        "partial",
        GROUP_SETTING,
        "outcome.NO_WORKING_SEAT.stage",
        detail_key="verification_problem",
    ),
    "NO_REVIEWING_SEAT": _Row(
        "outcome.NO_REVIEWING_SEAT.label",
        "partial",
        GROUP_SETTING,
        "outcome.NO_REVIEWING_SEAT.stage",
        detail_key="verification_problem",
    ),
    # 這一輪**一格都沒有跑**：序列要派工的那一種職責，這份陣容裡一格都沒有。
    #
    # ## 跟上面那個 `NO_REVIEWING_SEAT` 不是同一件事
    #
    # 那一個是「東西做出來了，但沒有人審」——少的是一個章。這一個是
    # 「連第一步都派不出去」，一次派工都沒發生、一毛額度都沒花。使用者
    # 接下來要做的事一樣（去陣容頁），但**他看到的那一句話不一樣**：
    # 前者要講「少一個章」，後者要講「這一輪沒有開始」。
    #
    # ## 為什麼它 2026-08-26 之前不存在
    #
    # 因為在那之前這種情況**根本不收場**：`seat_with_duty(..., fallback=
    # ROLE_REVIEWER)` 在一格複查都沒有的時候退回一個陣容裡沒有的名字，
    # 下游 `_run_role_prompt` 於是丟
    #
    # > NotImplementedError: 這個專案的陣容裡沒有「reviewer」這一格⋯⋯
    #
    # **那句話把他送去改一個沒有壞的東西**——他排的五格一點問題都沒有，
    # 真正的原因是那五格裡沒有任何一格的職責是「複查」，而計畫序列每一步
    # 都派給複查。整輪收在 `failed` 加一坨 traceback，不是一個結局。
    #
    # 歸 `GROUP_SETTING` 不歸 `GROUP_FIX`：程式沒有壞，少的是一項設定。
    # 也不歸 `GROUP_RERUN`——同一份陣容再跑一次，會在同一秒停在同一個地方。
    "NO_SEAT_FOR_DUTY": _Row(
        "outcome.NO_SEAT_FOR_DUTY.label",
        "partial",
        GROUP_SETTING,
        "outcome.NO_SEAT_FOR_DUTY.stage",
        detail_key="verification_problem",
    ),
    "VERIFY_NOT_CONFIGURED": _Row(
        "outcome.VERIFY_NOT_CONFIGURED.label",
        "partial",
        GROUP_SETTING,
        "outcome.VERIFY_NOT_CONFIGURED.stage",
        detail_key="verification_problem",
    ),
    "MEMORY_TRUNCATED": _Row(
        "outcome.MEMORY_TRUNCATED.label",
        "partial",
        GROUP_SETTING,
        "outcome.MEMORY_TRUNCATED.stage",
        detail_key="memory_problem",
    ),
    # 跑完了，但**維護共同記憶那兩步派不出去**：草稿給「複查」那一格、
    # 審它給「執行」那一格，而這份陣容裡少了其中一種。
    #
    # ## 跟上面那個 `NO_SEAT_FOR_DUTY` 是兩件事
    #
    # 那一種是「這一輪沒有開始」，一次派工都沒發生、一毛額度都沒花。
    # 這一種是**整輪都跑完了**——程式寫了、測試過了、拍板也蓋了，只有最後
    # 那一步沒有人做。產出不受影響，那句話一定要講出來，不然他會整輪重跑。
    #
    # ## 為什麼它 2026-08-26 之前不存在
    #
    # 因為在那之前這種情況**根本不收場**。沒有一格複查的時候
    # `steps/reviewing.look()` 回 `NO_REVIEWING_SEAT`，而 2026-08-24 起那不再
    # 停下整輪，於是 `codex_result=None` 一路傳到 `run_codex_memory_update`：
    #
    #   AttributeError: 'NoneType' object has no attribute 'stdout'
    #
    # 補起來也只是往前挪一行——下一行 `fallback=ROLE_REVIEWER` 退回一個他的
    # 陣容裡不存在的名字，接著就是這一整輪要消滅的那一句
    # 「這個專案的陣容裡沒有「reviewer」這一格」。
    #
    # 歸 `GROUP_SETTING` 不歸 `GROUP_RERUN`：同一份陣容再跑一次會在同一個
    # 地方停下來，只是那一次他要先花完一整輪的額度才看到同一句話。
    "MEMORY_NO_SEAT_FOR_DUTY": _Row(
        "outcome.MEMORY_NO_SEAT_FOR_DUTY.label",
        "partial",
        GROUP_SETTING,
        "outcome.MEMORY_NO_SEAT_FOR_DUTY.stage",
        detail_key="memory_problem",
    ),
    # 維護共同記憶的那一次呼叫本身失敗了（CLI 掛掉、逾時、沒啟動）。
    #
    # **這一種 2026-08-24 之前沒有家**，於是呼叫端只認 ASK_USER，
    # `status="ERROR"` 掉進 else 變成 COMPLETED／PASS——一次沒有人維護
    # 記憶的執行，畫面上是薄荷色的「工作流程已完成」。
    # `timeline` 與 `step_log` 裡留著 `success: false`，而沒有任何一處讀它。
    #
    # 歸 RERUN 不歸 FIX：程式沒有壞，是那一次呼叫沒成——再跑一次就好。
    "MEMORY_ERROR": _Row(
        "outcome.MEMORY_ERROR.label",
        "partial",
        GROUP_RERUN,
        "outcome.MEMORY_ERROR.stage",
        detail_key="memory_problem",
    ),
    # ---- 再跑一次就好 ----------------------------------------------------
    "INTAKE_INVALID": _Row(
        "outcome.INTAKE_INVALID.label",
        "partial",
        GROUP_RERUN,
        "outcome.INTAKE_INVALID.stage",
        detail_key="outcome_detail",
    ),
    "INTAKE_ERROR": _Row(
        "outcome.INTAKE_ERROR.label",
        "error",
        GROUP_RERUN,
        "outcome.INTAKE_ERROR.stage",
        detail_key="claude_error",
    ),
    "CONSULT_INVALID": _Row(
        "outcome.CONSULT_INVALID.label",
        "partial",
        GROUP_RERUN,
        "outcome.CONSULT_INVALID.stage",
        detail_key="outcome_detail",
    ),
    "APPROVAL_INVALID": _Row(
        "outcome.APPROVAL_INVALID.label",
        "partial",
        GROUP_RERUN,
        "outcome.APPROVAL_INVALID.stage",
        detail_key="outcome_detail",
    ),
    "APPROVAL_REJECTED": _Row(
        "outcome.APPROVAL_REJECTED.label",
        "partial",
        GROUP_RERUN,
        "outcome.APPROVAL_REJECTED.stage",
        detail_key="outcome_detail",
    ),
    "APPROVAL_ERROR": _Row(
        "outcome.APPROVAL_ERROR.label",
        "error",
        GROUP_RERUN,
        "outcome.APPROVAL_ERROR.stage",
        detail_key="claude_error",
    ),
    "PLANNER_ERROR": _Row(
        "outcome.PLANNER_ERROR.label",
        "error",
        GROUP_RERUN,
        "outcome.PLANNER_ERROR.stage",
        detail_key="claude_error",
    ),
    "CLAUDE_ERROR": _Row(
        "outcome.CLAUDE_ERROR.label",
        "error",
        GROUP_RERUN,
        "outcome.CLAUDE_ERROR.stage",
        # CLI 自己那句話。**這一格空著的代價 2026-08-23 量到了**：老闆撞到
        # 五小時用量上限，CLI 明明說了「額度用完了，幾點會回來」，而畫面上
        # 寫的是「Claude 暫時無法完成任務」——他看到的跟「壞掉了」一模一樣。
        detail_key="claude_error",
    ),
    "CODEX_ERROR": _Row(
        "outcome.CODEX_ERROR.label",
        "error",
        GROUP_RERUN,
        "outcome.CODEX_ERROR.stage",
        detail_key="codex_log",
    ),
    "CODEX_INVALID_RESPONSE": _Row(
        "outcome.CODEX_INVALID_RESPONSE.label", "error", GROUP_RERUN, "outcome.CODEX_INVALID_RESPONSE.stage"
    ),
    # ---- 有一項要先修 ----------------------------------------------------
    "APPROVAL_BLOCKED": _Row(
        "outcome.APPROVAL_BLOCKED.label",
        "partial",
        GROUP_FIX,
        "outcome.APPROVAL_BLOCKED.stage",
        detail_key="outcome_detail",
    ),
    "FIX_LIMIT_REACHED": _Row(
        "outcome.FIX_LIMIT_REACHED.label", "partial", GROUP_FIX, "outcome.FIX_LIMIT_REACHED.stage"
    ),
    "VERIFY_TIMED_OUT": _Row(
        "outcome.VERIFY_TIMED_OUT.label",
        "partial",
        GROUP_FIX,
        "outcome.VERIFY_TIMED_OUT.stage",
        detail_key="verification_problem",
    ),
}

# ---------------------------------------------------------------------------
# `NOT_DECIDABLE` 一種橫跨四組。
#
# 它不是一種收場，是六種——而「缺兩個章」跟「有檢查是紅的」，使用者要做的事
# 完全相反。所以它按**判定**分，不按狀態碼分。
# ---------------------------------------------------------------------------

UNDECIDABLE_STATUS = "NOT_DECIDABLE"

_UNDECIDABLE: dict[str, _Row] = {
    "MISSING": _Row(
        "undecidable.MISSING.label",
        "partial",
        GROUP_RERUN,
        "undecidable.MISSING.stage",
        detail_key="decision_problem",
    ),
    "SPLIT_VERSION": _Row(
        "undecidable.SPLIT_VERSION.label",
        "partial",
        GROUP_DECIDE,
        "undecidable.SPLIT_VERSION.stage",
        detail_key="decision_problem",
    ),
    "SELF_REVIEW": _Row(
        "undecidable.SELF_REVIEW.label",
        "partial",
        GROUP_SETTING,
        "undecidable.SELF_REVIEW.stage",
        detail_key="decision_problem",
    ),
    "GATED": _Row(
        "undecidable.GATED.label",
        "partial",
        GROUP_SETTING,
        "undecidable.GATED.stage",
        detail_key="decision_problem",
    ),
    "FAILED": _Row(
        "undecidable.FAILED.label",
        "partial",
        GROUP_FIX,
        "undecidable.FAILED.stage",
        detail_key="decision_problem",
    ),
    # **READY 這一列走得到，2026-08-24 起。**原本這裡寫的是「READY 走不到
    # 這裡——能拍板就不是 NOT_DECIDABLE」，那句話從那天起是假的：
    # `run_stamps.UNCOMPUTABLE`（拍板檢查自己算不出來）借這一列落地，
    # `run_stamps.UNCOMPUTABLE_SCREEN_KEY` 指的就是這個字。
    #
    # 留著那句假的最貴：下一個來清死碼的人會照著它把這一列刪掉，而這一列是
    # 唯一讓「算不出來」在畫面上講得出話的東西——刪掉之後那一輪的
    # `outcome_group` 是空字串，首頁上是一格沒有顏色也沒有標籤的東西。
    #
    # 借約有一條負向驗證守著（C3）：
    # tests/test_the_stamp_check_that_blew_up_does_not_say_it_finished.py
    # ::test_the_ending_has_a_row_in_the_outcome_table
    #
    # 正確的長相是這張表自己多一列 `UNCOMPUTABLE`＋`app/i18n.py` 多一句。
    "READY": _Row(
        "undecidable.MISSING.label",
        "partial",
        GROUP_RERUN,
        "undecidable.READY.stage",
        detail_key="decision_problem",
    ),
}


class UnknownOutcome(KeyError):
    """一種沒有人替它想過畫面該說什麼的收場。

    **刻意讓它炸開**，而不是回一個「其他」。回「其他」的話，漏掉一種跟處理過
    一種在畫面上長得一模一樣——這正是 2026-08-23 查到十種收場被畫成紅燈、
    一種被畫成綠燈的原因。
    """


def known_statuses() -> frozenset[str]:
    """有人替它想過畫面要說什麼的收場。守衛測試比的就是這一份。"""
    return frozenset(_TABLE) | {UNDECIDABLE_STATUS}


# 狀態列只有一行。CLI 的錯誤輸出可能是幾百行的堆疊。
STAGE_MAX_CHARS = 240


def _last_words(text: str) -> str:
    """一段輸出裡「最後那句有意義的話」。

    **取尾巴不取開頭**：命令列工具把過程印在前面、把結論印在最後，而使用者
    要看的是結論。取開頭會拿到一行版本號。

    這一步存在的理由 2026-08-23 量到了：老闆撞到五小時用量上限，CLI 明明
    說了「額度用完了、幾點會回來」，而畫面上寫的是「Claude 暫時無法完成
    任務」——**AICC 把那句話換成了一句自己編的、而且比較沒用的話**。
    他看到的跟「壞掉了」一模一樣。

    （所以這裡刻意**不去比對任何字串**。「撞到上限」長什麼樣我沒有樣本，
    而照著猜寫一條 `if "usage limit" in ...` 出來的東西，比不上直接把它
    自己講的話原樣端出去——猜錯的時候，那條 if 會很有自信地說錯話。）
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    # **最後一行，就這樣。**往前多接幾行會把「Loading⋯」「node v20」那種
    # 開場白一起端上去，而那是雜訊擠掉結論。看不夠的人去看執行紀錄——
    # 那裡有全文。
    for line in reversed(lines):
        if len(line) >= 4:
            return line[-STAGE_MAX_CHARS:]
    return lines[-1][-STAGE_MAX_CHARS:]


def describe(result: dict) -> Outcome:
    """這一次收場長什麼樣。**唯一**知道答案的地方。"""
    status = str(result.get("workflow_status", "") or "")
    if status == UNDECIDABLE_STATUS:
        verdict = str(result.get("decision_verdict", "") or "")
        row = _UNDECIDABLE.get(verdict)
        if row is None:
            raise UnknownOutcome(
                f"{UNDECIDABLE_STATUS} 帶回來的判定是 {verdict!r}，"
                "而 app/core/outcomes.py 沒有替它寫過畫面要說什麼。"
            )
    else:
        row = _TABLE.get(status)
        if row is None:
            raise UnknownOutcome(
                f"{status!r} 這種收場沒有人替它寫過畫面要說什麼。"
                "在 app/core/outcomes.py 補一行——**不要加預設值**。"
            )

    stage = t(row.stage_key)
    if row.detail_key:
        detail = _last_words(str(result.get(row.detail_key, "") or ""))
        if detail:
            # 引擎給的具體說明**蓋掉**那張表的預設句。那一句是 CLI 或使用者
            # 講的話，不是介面文字，所以不進目錄。
            stage = detail
    return Outcome(
        status=status,
        label=t(row.label_key),
        style=row.style,
        group=row.group,
        stage=stage,
    )
