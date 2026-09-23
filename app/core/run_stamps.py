"""執行流程蓋章的地方。

`app/services/approval_chain.py` 是章的規則，這裡是「引擎跑到哪一步就蓋哪
一個章」。分開的理由：規則那份不該知道 `TESTS_TIMED_OUT` 這種引擎詞彙，
而這份不該重新實作一次「什麼樣的章合法」。

## 章有牙齒了（2026-08-22 拍板）

章齊了才報告完成。少一個，這一輪的結局是 `NOT_DECIDABLE`——**不是失敗**：
測試過了、複查也同意了，只是這一版還缺一個能回溯的憑據。講成失敗會讓使用者
去修一個沒壞的東西；講成完成，那份紀錄事後回溯不出來，而那正是整套章存在的
理由。

在這之前它只記錄，那是刻意的：接上一個新的判定條件，等於在同一次改動裡同時
引進「章可能算錯」與「算錯就會擋掉真正該過的執行」兩種失敗，而第二種會蓋掉
第一種的症狀。**先讓它在旁邊跑一輪**——那一輪跑完了。

算不出來的時候**不放行，也不算失敗**。2026-08-24 之前這裡回 `None`，而 `None`
在呼叫端跟「不用擋」走同一條路——於是拍板檢查自己爆掉的那一次，畫面上是薄荷色
的「工作流程已完成」，跟章真的齊了那一次一個字都不差。現在回的是 `UNCOMPUTABLE`
（見底下那個哨兵），這一輪的結局是 `NOT_DECIDABLE`＋「跑完了，但拍板的判定自己
算不出來」。

記帳不准變成一種新的失敗方式——**但「不准變成失敗」不等於「准說謊」**。
`NOT_DECIDABLE` 本來就被設計成不是失敗（見上面那一段），所以兩邊都滿足。

## 誰要蓋哪些章，由陣容決定

`requires_for()` 從專案排出來的陣容算出這一次「應該」有哪些章。**不准寫死**
——寫死的清單在使用者把節點取名成「發想／規劃／UI／實作／審閱」之後會指向
一堆不存在的角色，於是拍板檢查永遠說缺章，而那些章補不出來。
**一個永遠補不了的缺，跟一個假的通過一樣壞。**

## 驗收標準的章現在生得出來了（2026-08-22）

以前這裡有一段話說它生不出來，因為「複查角色是在作者做完之後才第一次被派
上場的」。那一段已經過期：陣容裡第一個不做事的節點會在動手之前先跟使用者
把需求問清楚，問完的那份東西就是驗收標準，由它蓋章。

沒有那樣一格的陣容（例如整條線只有一個執行者）**還是生不出來**，而這裡照舊
不編一個出來——拿專案的三句宣告冒充「訂標準的那一格訂的」很誘人，但訂它的
是使用者，不是那一格，而那個差別正是【驗收標準由複查角色訂】在講的事。
"""
from __future__ import annotations

from app.i18n import t

from datetime import datetime, timezone

from app.core.lineup import (
    DUTY_APPROVE,
    DUTY_REVIEW,
    DUTY_WORK,
    ROLE_AUTHOR,
    ROLE_REVIEWER,
    criteria_seat,
)
from app.services.approval_chain import (
    ROLE_ENGINE,
    SUBJECT_ACCEPTANCE,
    SUBJECT_VERIFY,
    SUBJECT_WORK,
    ApprovalChain,
    ApprovalChainError,
    Required,
    Result,
    Stamp,
    StampKind,
    StampLog,
    Versions,
    content_digest,
)
from app.services.project_verification import (
    TESTS_BY_RUBRIC,
    TESTS_FAILED,
    TESTS_NOT_DECLARED,
    TESTS_PASSED,
    TESTS_TIMED_OUT,
)

# 引擎的測試結果對到章的結果。
#
# `TIMED_OUT` 對到 GATED 而不是 FAIL 是刻意的，而且跟引擎現有的立場一致：
# workflow_worker 對逾時的註解寫著「A deadline that fired is not a red
# light. Nothing has been shown to be broken」。**沒量到不等於壞掉**，把它
# 讀成壞掉就會派人去修一個沒有證據的 bug。
_RESULT_FOR_STATUS = {
    TESTS_PASSED: Result.PASS,
    TESTS_FAILED: Result.FAIL,
    TESTS_TIMED_OUT: Result.GATED,
    TESTS_NOT_DECLARED: Result.GATED,
    # 質化的案子：**機器什麼都沒跑，所以機器不能說「過」**。GATED 的意思
    # 正是「還不能算過，等人看」——而那一格就是審查那一格。
    #
    # 明著列出來而不是靠底下 `.get(..., Result.GATED)` 那個預設：預設會讓
    # 「我想過了，答案是 GATED」跟「我沒想過」長得一模一樣。
    TESTS_BY_RUBRIC: Result.GATED,
}


# `criteria_seat` 在 `app/core/lineup.py`。**只准有一份實作**——「訂標準的
# 是哪一格」被提示詞組裝與蓋章兩邊都問，兩份各自演化的話，提示詞會寫著一個
# 名字、章會蓋在另一個名字上，而兩邊看起來都很正常。


def requires_for(lineup) -> tuple[Required, ...]:  # noqa: ANN001
    """這一次執行「應該」蓋滿哪些章。

    順序就是它們該被蓋的順序，因為畫面照這個順序列給使用者看。

    核准那一格 2026-08-22 接上了（`workflow_worker._approval_phase`）：複查
    通過之後，它看完所有的章，說這一整條鏈站不站得住。沒有那一格的陣容完全
    不跑那一步，一次呼叫都不多。
    """
    items: list[Required] = []

    setter = criteria_seat(lineup)
    if setter:
        items.append(Required(setter, SUBJECT_ACCEPTANCE, StampKind.REVIEW))

    for slot in lineup:
        if slot.duty == DUTY_WORK:
            items.append(Required(slot.role, SUBJECT_WORK, StampKind.MACHINE))

    items.append(Required(ROLE_ENGINE, SUBJECT_VERIFY, StampKind.MACHINE))

    for slot in lineup:
        if slot.duty in (DUTY_REVIEW, DUTY_APPROVE):
            items.append(Required(slot.role, SUBJECT_WORK, StampKind.REVIEW))

    return tuple(items)


class _CannotCompute:
    """`readiness()` 這一次**算不出來**。它不是 `None`，也不是一個判定。

    存在的理由只有一個：**讓「算不出來」跟「算過了，可以拍板」在呼叫端長得
    不一樣**。以前這裡回 `None`，而呼叫端那一行是

        if verdict is None or verdict.can_decide:
            return None          # ← 放行

    兩件相反的事共用同一條放行路徑。少一個章的時候畫面會說「還缺【某某】的
    驗證章」；拍板檢查自己丟例外的時候畫面說「工作流程已完成」——**差別只在
    有沒有例外，而使用者看不到那個差別。**

    刻意**不給** `can_decide` 也不給 `verdict`：拿它當判定用會當場
    AttributeError，而不是安靜地放行。安靜地放行正是這個哨兵在防的事。
    """

    __slots__ = ()


# 算不出來的那個答案。**是單一物件，用 `is` 比**——比型別會讓任何一個長得像
# 的東西混進來，而這裡要的是「就是它」。
UNCOMPUTABLE = _CannotCompute()


# 這一串會原封不動變成 `result["decision_verdict"]`，而 `app/core/outcomes.py`
# 拿它當鑰匙查「畫面上要說哪一句」。
#
# 挑 `READY` 不是因為它算得出來——是因為那張表裡 `READY` 那一列寫的正好就是
# 這一句：**「跑完了，但拍板的判定自己算不出來」**，而 `READY` 本來走不到那裡
# （能拍板就不會是 `NOT_DECIDABLE`）。寫那一列的人已經替這個情況寫好了句子，
# 只是沒有東西送得到那裡。
#
# **正確的長相是那張表多一列 `UNCOMPUTABLE`**，而那要動 `app/core/outcomes.py`
# 與 `app/i18n.py`——這一輪那兩個檔案不歸這裡改，所以借。借的代價寫在這裡：
# `outcomes.py` 那句「READY 走不到這裡」的註解從今天起是假的。
#
# 借到什麼時候還，有一條負向驗證守著：這個常數一改成表上沒有的字，
# `tests/test_the_stamp_check_that_blew_up_does_not_say_it_finished.py` 就紅。
UNCOMPUTABLE_SCREEN_KEY = "READY"


class RunStamper:
    """一次執行的蓋章員。

    每一個蓋章的方法都吞掉自己的例外。理由跟 `StepLog.append` 一樣：**記帳不准
    變成一種新的失敗方式**——蓋不上一個章，不該把一次真的執行弄死。

    **`readiness()` 不在這條規則裡面**，而它以前在。那句「這一版連擋都不擋，
    那就更沒有理由讓它把一次真的執行弄死」寫於 2026-08-22 章有牙齒之前，
    從那天起就過期了：它現在是唯一擋得住「報告完成」的地方，而一個吞掉例外
    之後回 `None` 的守門員，跟沒有守門員長得一模一樣。所以它吞例外，但回的是
    `UNCOMPUTABLE`，不是 `None`。
    """

    def __init__(self, project_path, run_id: str) -> None:  # noqa: ANN001
        self.run_id = run_id
        self.project_path = project_path
        self.log = StampLog(project_path)
        self._last_id: str = ""

    # --- 蓋章 ---------------------------------------------------------------

    def acceptance_set(self, criteria: str, role: str, reasoning: str) -> None:
        """訂標準的那一格把「怎樣算做完」訂下來了。

        **審查章**，不是機器章：這是一個判斷，沒有任何指令跑過。`reasoning`
        寫的是它憑什麼認為問清楚了（通常是使用者最後那一句確認）。

        它蓋在**驗收標準本身**的版本上，不是交付物的版本上——見
        `approval_chain.Versions`。混成同一條版本線的話，標準剛訂完、東西
        一交出來，這個章就當場失效，畫面會說「沒有人訂標準」而事實是訂了。
        """
        self._put(
            kind=StampKind.REVIEW,
            role=role,
            subject=SUBJECT_ACCEPTANCE,
            content=criteria,
            reasoning=reasoning,
        )

    def work_done(
        self,
        deliverable: str,
        succeeded: bool,
        evidence: str,
        role: str = ROLE_AUTHOR,
    ) -> None:
        """做事的那一格交出了東西。機器章：這一次派工跑完了，結果是什麼。

        `role` 要傳，因為陣容可以有不只一格在做事，而且名字是使用者取的。
        預設值只是為了讓還沒改的呼叫端不會壞掉。
        """
        self._put(
            kind=StampKind.MACHINE,
            role=role,
            subject=SUBJECT_WORK,
            content=deliverable,
            result=Result.PASS if succeeded else Result.FAIL,
            evidence=evidence,
        )

    def verified(self, deliverable: str, status: str, reason: str) -> None:
        """專案宣告的驗證指令跑過了。

        角色掛 `ROLE_ENGINE` 而不是某一格：**沒有任何一個席位做了這件事**，
        是程式跑的。掛在席位名下會在陣容改名之後指向一格不存在的東西。

        `reason` 是 `VerificationOutcome` 已經寫好給人看的那一句（例如
        「離開碼 3221225477 不在宣告的通過清單 [0] 內」），直接當證據用——
        重寫一句只會讓紀錄裡出現兩種說法。
        """
        self._put(
            kind=StampKind.MACHINE,
            role=ROLE_ENGINE,
            subject=SUBJECT_VERIFY,
            content=deliverable,
            result=_RESULT_FOR_STATUS.get(status, Result.GATED),
            evidence=reason or status,
        )

    def reviewed(
        self, deliverable: str, reasoning: str, role: str = ROLE_REVIEWER
    ) -> None:
        """複查的那一格看過而且同意。審查章：沒有結果、沒有證據，但**要有理由**。

        它跟上面那個 verify 的機器章是兩回事，而且**故意不能互相取代**：
        一個是「指令跑出來的」，一個是「有人看過了」。

        `reasoning` 用複查自己寫的那句話，不要重寫一遍——重寫等於在紀錄裡放
        兩種說法，而事後看的人不知道該信哪一個。

        理由是空的就蓋不出章，那是刻意的。老闆 2026-08-21 的原話是「簽核章
        需要有時間戳記**對應邏輯審核**還有你說的版本」，而只記「誰、何時、
        哪一版」的章，回答不了事後最需要的那一題：**憑什麼**。
        """
        self._put(
            kind=StampKind.REVIEW,
            role=role,
            subject=SUBJECT_WORK,
            content=deliverable,
            reasoning=reasoning,
        )

    def approved(
        self, deliverable: str, role: str, reasoning: str
    ) -> None:
        """核准那一格看完所有的章，說這件事可以拍板。

        **審查章。**它跟複查那個審查章是兩件事：複查說的是「我看過這份實作」，
        這一個說的是「**我看過所有的章，這一整條鏈站得住**」。

        它蓋在交付物那條版本線上，所以內容一改它就失效——那正是要的：拍板是
        對**某一版**拍的，東西改了就要重新看一遍。
        """
        self._put(
            kind=StampKind.REVIEW,
            role=role,
            subject=SUBJECT_WORK,
            content=deliverable,
            reasoning=reasoning,
        )

    # --- 讀 -----------------------------------------------------------------

    def chain(self) -> ApprovalChain:
        try:
            return self.log.read(self.run_id)
        except Exception:
            return ApprovalChain()

    def readiness(
        self,
        deliverable: str,
        required: tuple[Required, ...],
        criteria: str = "",
    ):
        """能不能拍板。**答案有三種，不是兩種。**

        算得出來回一個 `Readiness`（可以／不可以，以及為什麼不可以）；
        **算不出來回 `UNCOMPUTABLE`**——那是第三種，不准跟前兩種混在一起。

        以前這裡的說明寫著「呼叫端目前只拿它來顯示，不拿它擋」。那句話 2026-08-22
        章有牙齒的那天就不成立了：`approval.is_undecidable` 就是拿它擋的地方，
        而它是唯一擋得住「工作流程已完成」的東西。一句過期的放行理由，讀起來
        跟一句還成立的一模一樣——這一條 2026-08-24 被掃出來的時候，正是靠那句話
        撐著。

        `criteria` 是這一刻的驗收標準原文。它跟交付物各自是一條版本線——
        標準改了，蓋在舊標準上的章跟著失效；東西改了，標準的章不受影響。

        **這裡是 `None` 的關口。**底下那條「規則層回了 None」看起來是死碼：
        今天 `ApprovalChain.readiness` 每一條路都回一個 `Readiness`。它守的是
        明天——`approval.is_undecidable` 裡 `verdict is None` 那條放行還在
        （要拿掉它得動老闆定的那條測試，待他點頭），所以只要有人在規則層新加
        一條回 `None` 的路，那個 `None` 會一路走到「工作流程已完成」，而**這一
        整組檢查一條都不會紅**。在這裡收掉，那條路就走不到放行。
        """
        try:
            verdict = self.chain().readiness(
                Versions.of(criteria, deliverable), required
            )
        except Exception as exc:
            # 例外照舊吞掉（記帳不准弄死執行），但**要留下痕跡**：吞掉又回一個
            # 跟成功同路的值，等於這件事沒發生過。回 `UNCOMPUTABLE` 讓畫面講得
            # 出來，寫進時間軸讓事後查得出是哪一個例外。
            self._note_uncomputable(exc)
            return UNCOMPUTABLE
        if verdict is None:
            # 「沒算出判定」跟「算出了例外」對使用者是同一件事，所以走同一個
            # 出口、同一種時間軸紀錄。**不要在這裡回 `None` 讓上游自己看著辦**
            # ——上游那條路是放行。
            self._note_uncomputable(
                RuntimeError(t("stamp.no_verdict_at_all"))
            )
            return UNCOMPUTABLE
        return verdict

    # --- 內部 ---------------------------------------------------------------

    def _put(
        self,
        kind: StampKind,
        role: str,
        subject: str,
        content: str,
        result: Result | None = None,
        evidence: str = "",
        reasoning: str = "",
    ) -> None:
        # 驗收標準是整條鏈的地基，不建立在任何東西之上。**特例寫在這裡而不是
        # 靠呼叫順序**：駁回之後標準會被重訂一次，那時候「上一個章」是別人的
        # 實作章，順順地接下去會讓新標準掛在舊實作底下——顛倒的。
        if subject == SUBJECT_ACCEPTANCE:
            depends_on: tuple[str, ...] = ()
        else:
            # 前一個章就是這一個的地基：實作建立在標準之上，驗證是驗實作交出
            # 來的東西，審查是審驗證過的東西。地基那個章沒了，後面全部站在
            # 空氣上——那正是「標準一改，實作與驗證的章跟著失效」的機制。
            depends_on = (self._last_id,) if self._last_id else ()

        try:
            stamp = Stamp(
                stamp_id=f"{self.run_id}-{subject}-{role}-{self._next_seq()}",
                kind=kind,
                role=role,
                subject=subject,
                content_digest=content_digest(content),
                at=datetime.now(timezone.utc).astimezone().isoformat(),
                evidence=evidence,
                reasoning=reasoning,
                result=result,
                depends_on=depends_on,
            )
        except ApprovalChainError:
            # 建不出來的章不寫進去。這裡不修補它——一個被硬湊出來的章，
            # 讀起來跟一個真的章一模一樣。
            return

        try:
            self.log.append(self.run_id, stamp)
            self._last_id = stamp.stamp_id
        except Exception:
            return

    def _next_seq(self) -> int:
        try:
            return len(self.chain().stamps)
        except Exception:
            return 0

    def _note_uncomputable(self, exc: BaseException) -> None:
        """把「拍板檢查是被哪一個例外打斷的」寫進這一輪的時間軸。

        畫面上那一句只說得出「算不出來」——**那對使用者夠了，對修的人不夠**。
        少了這一行，事後回頭查只剩一個沒有來由的 `NOT_DECIDABLE`，而下一次
        它為什麼爆掉還是查不出來。

        自己也吞例外：記時間軸失敗**不准**變成拍板檢查失敗的放大器。
        （2026-08-21 那次刪掉整個 Roaming 資料夾，形狀就是「收尾動作把原本
        的小事放大成大事」。）
        """
        try:
            from app.services.timeline_store import TimelineStore

            TimelineStore(self.project_path).append(
                self.run_id,
                "aicc",
                "stamp_readiness_uncomputable",
                f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            return


# 出廠兩人陣容跑完一輪之後，鏈子上應該有的東西。
#
# **只給還沒接上 `requires_for()` 的呼叫端與測試用**；真的在跑的專案要用
# `requires_for(orchestrator.lineup)`，因為節點的名字是使用者取的。
CURRENT_ENGINE_REQUIRES = (
    Required(ROLE_REVIEWER, SUBJECT_ACCEPTANCE, StampKind.REVIEW),
    Required(ROLE_AUTHOR, SUBJECT_WORK, StampKind.MACHINE),
    Required(ROLE_ENGINE, SUBJECT_VERIFY, StampKind.MACHINE),
    Required(ROLE_REVIEWER, SUBJECT_WORK, StampKind.REVIEW),
)


# **存 key 不存字**（模組層取出來會在 import 的當下凍住）。
_SUBJECT_WORDS = {
    SUBJECT_ACCEPTANCE: "stamp.required.criteria",
    SUBJECT_WORK: "stamp.required.build",
    SUBJECT_VERIFY: "stamp.required.verify",
}
_RESULT_WORDS = {
    Result.PASS: "stamp.result.pass",
    Result.FAIL: "stamp.result.fail",
    Result.GATED: "stamp.result.gated",
}


def describe_chain(chain: ApprovalChain) -> str:
    """把鏈子上的章寫成一段給人（和給核准那一格）讀的文字。

    **每一個章都寫出「憑什麼」**：機器章寫它跑了什麼，審查章寫它為什麼同意。
    只列「誰蓋了什麼」的話，核准那一格拿到的東西跟一張打勾表沒有兩樣——而它
    的工作正是判斷那些勾憑什麼成立。

    寫在這裡而不是在提示詞組裝那邊：這份文字用的是引擎的詞彙（`gated`、
    `TESTS_TIMED_OUT`），而提示詞那一份刻意不認識那些字。
    """
    stamps = chain.stamps
    if not stamps:
        return t("stamp.none_at_all")

    lines: list[str] = []
    for stamp in stamps:
        key = _SUBJECT_WORDS.get(stamp.subject)
        subject = t(key) if key else stamp.subject
        if stamp.kind is StampKind.MACHINE:
            result_key = _RESULT_WORDS.get(stamp.result)
            outcome = t(result_key) if result_key else str(stamp.result)
            why = stamp.evidence or t("stamp.no_evidence")
            kind = f"機器章／{outcome}"
        else:
            why = stamp.reasoning or t("parse.no_reason_given")
            kind = t("stamp.kind.review")
        lines.append(
            f"- 【{stamp.role}】{subject}　{kind}\n"
            f"  時間：{stamp.at}\n"
            f"  版本：{stamp.content_digest[:12]}\n"
            f"  憑什麼：{why}"
        )
    return "\n".join(lines)


def describe_readiness(verdict) -> str:  # noqa: ANN001
    """引擎自己的判定，寫成一句話加上還缺哪幾個。

    **這一句由引擎給，畫面與提示詞都不准自己再拼一次**：同一句話出現在時間軸、
    決策橫幅、匯出報告與這裡，各拼一次就會有四種說法，而看的人不知道該信哪個。
    """
    if verdict is UNCOMPUTABLE:
        # **整句自己站得住**，因為它會被原樣端到畫面上當那一列的說明
        # （`outcomes.describe` 的 `decision_problem` 蓋掉預設句）。
        # 借的是 outcomes.py 那一列自己的句子，兩邊講同一句話——同一件事
        # 出現兩種說法的時候，看的人不知道該信哪一個。
        return t("undecidable.READY.stage")
    if verdict is None:
        return t("stamp.cannot_tell")
    line = verdict.short
    if verdict.missing:
        gaps = "、".join(
            f"【{item.role}】的{_subject_word(item.subject)}章"
            for item in verdict.missing
        )
        line += f"\n還缺：{gaps}"
    if verdict.conflicting_role:
        line += f"\n同時訂標準又做事的是：【{verdict.conflicting_role}】"
    return line


def _subject_word(subject: str) -> str:
    """那個章講的是什麼。查不到就用引擎的代號本身——它至少講得出是哪一種。"""
    key = _SUBJECT_WORDS.get(subject)
    return t(key) if key else subject
