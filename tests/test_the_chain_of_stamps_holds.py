"""簽核鏈的規則，一條一條問它有沒有效。

`app/services/approval_chain.py` 存在的理由是 2026-08-21 那批定案裡最貴的
幾條——它們每一條都是「**看起來很確定的錯誤**」的一種形狀：

* 一個沒有證據的「我同意」，讀起來跟一個跑過測試的「通過」一模一樣。
* 一個蓋在舊版內容上的章，看起來跟蓋在新版上的一模一樣。
* 「沒有宣告怎麼驗」被當成「驗了但是紅的」，會派人去修一個沒壞的東西。
* 「自己出考卷自己考」在畫面上看起來就是兩個章。

所以這裡每一條測試守的都不是「函式回傳對的值」，而是「這個分不出來的
差別，程式分得出來」。
"""
from __future__ import annotations

import pytest

from app.services.approval_chain import (
    SUBJECT_ACCEPTANCE,
    SUBJECT_VERIFY,
    SUBJECT_WORK,
    ApprovalChain,
    ApprovalChainError,
    Readiness,
    Required,
    ROLE_USER,
    Result,
    Stamp,
    StampKind,
    Verdict,
    Versions,
    content_digest,
)

V1 = content_digest("第一版的內容")
V2 = content_digest("改過之後的內容")

# 兩條版本線都停在同一版。這些測試里的章本來就共用一個版本碼，
# 拆成兩條之後它們問的事情沒變。兩條各自走的場景在檔案末尾單獨寫。
AT_V1 = Versions(criteria=V1, deliverable=V1)
AT_V2 = Versions(criteria=V2, deliverable=V2)

AT = "2026-08-21T10:00:00+08:00"


def machine(
    stamp_id: str,
    role: str,
    subject: str,
    digest: str = V1,
    result: Result = Result.PASS,
    depends_on: tuple[str, ...] = (),
) -> Stamp:
    return Stamp(
        stamp_id=stamp_id,
        kind=StampKind.MACHINE,
        role=role,
        subject=subject,
        content_digest=digest,
        at=AT,
        evidence="pytest -q -> 1341 passed",
        result=result,
        depends_on=depends_on,
    )


def review(
    stamp_id: str,
    role: str,
    subject: str,
    digest: str = V1,
    depends_on: tuple[str, ...] = (),
    reasoning: str = "看過了，兩個邊界條件都有處理。",
) -> Stamp:
    return Stamp(
        stamp_id=stamp_id,
        kind=StampKind.REVIEW,
        role=role,
        subject=subject,
        content_digest=digest,
        at=AT,
        reasoning=reasoning,
        depends_on=depends_on,
    )


TWO_PERSON = (
    Required("reviewer", SUBJECT_ACCEPTANCE),
    Required("author", SUBJECT_WORK),
    Required("reviewer", SUBJECT_VERIFY),
)


# --- 章本身建得出來嗎 -------------------------------------------------------


def test_a_machine_stamp_without_evidence_cannot_be_made() -> None:
    """沒有證據的機器章，就是一個穿著機器外衣的審查章。

    這一條是整個檔案裡最重要的：守衛只認機器章，所以只要「我同意」能夠
    自稱機器章，守衛就等於不存在——而且畫面上完全看不出差別。
    """
    with pytest.raises(ApprovalChainError, match="證據"):
        Stamp(
            stamp_id="s1",
            kind=StampKind.MACHINE,
            role="reviewer",
            subject=SUBJECT_VERIFY,
            content_digest=V1,
            at=AT,
            result=Result.PASS,
            evidence="",
        )


def test_a_review_stamp_cannot_carry_a_result() -> None:
    """「我看了、我同意」不是一個量出來的東西。

    讓審查章帶 result，等於讓一個人的印象跟一次真的執行擠在同一個欄位裡，
    而下游只會讀那個欄位。
    """
    with pytest.raises(ApprovalChainError, match="不准帶結果"):
        Stamp(
            stamp_id="s1",
            kind=StampKind.REVIEW,
            role="finisher",
            subject=SUBJECT_WORK,
            content_digest=V1,
            at=AT,
            result=Result.PASS,
        )


def test_a_stamp_must_name_the_version_it_was_put_on() -> None:
    """沒綁版本的章永遠不會失效，而永遠不會失效的章等於沒有章。"""
    with pytest.raises(ApprovalChainError, match="版"):
        Stamp(
            stamp_id="s1",
            kind=StampKind.REVIEW,
            role="reviewer",
            subject=SUBJECT_WORK,
            content_digest="",
            at=AT,
        )


def test_an_unknown_subject_is_refused_rather_than_stored() -> None:
    """subject 拼錯，「訂標準的跟做事的是不是同一個」那條檢查就靜靜失效。

    存下來再說會讓錯字活到拍板那一刻，而那時候它看起來只是「少一個章」。
    """
    with pytest.raises(ApprovalChainError, match="蓋章對象"):
        review("s1", "author", "acceptence")  # 少一個 a


def test_a_stamp_cannot_depend_on_something_that_is_not_there() -> None:
    chain = ApprovalChain()
    with pytest.raises(ApprovalChainError, match="還不存在"):
        chain.add(machine("s2", "author", SUBJECT_WORK, depends_on=("s1",)))


# --- 缺章要講「缺幾個」，不是「第幾關」 ------------------------------------


def test_a_missing_stamp_is_reported_as_a_count_not_as_a_stage() -> None:
    """定案原文：少一個章不是卡在那一關，是整件事還不能拍板。

    回報成關卡序號，就等於把一個被否決的形狀（一串紅綠燈）又做回來。
    """
    chain = ApprovalChain().add(machine("s1", "reviewer", SUBJECT_ACCEPTANCE))

    verdict = chain.readiness(AT_V1, TWO_PERSON)

    assert verdict.verdict is Verdict.MISSING
    assert len(verdict.missing) == 2
    assert verdict.short == "缺 2 個章"
    assert not verdict.can_decide


def test_a_full_chain_on_one_version_can_be_decided() -> None:
    chain = (
        ApprovalChain()
        .add(machine("s1", "reviewer", SUBJECT_ACCEPTANCE))
        .add(machine("s2", "author", SUBJECT_WORK, depends_on=("s1",)))
        .add(machine("s3", "reviewer", SUBJECT_VERIFY, depends_on=("s2",)))
    )

    verdict = chain.readiness(AT_V1, TWO_PERSON)

    assert verdict.can_decide
    assert verdict.short == "可以拍板"


# --- 自己出考卷自己考 -------------------------------------------------------


def test_the_one_who_did_the_work_cannot_also_have_set_the_bar() -> None:
    """它會訂一個自己剛好做得到的。

    擋的是角色重疊，不是「一定要有第二個 AI」——下一條測試就是反面。
    """
    chain = (
        ApprovalChain()
        .add(machine("s1", "author", SUBJECT_ACCEPTANCE))
        .add(machine("s2", "author", SUBJECT_WORK, depends_on=("s1",)))
    )

    verdict = chain.readiness(AT_V1, TWO_PERSON)

    assert verdict.verdict is Verdict.SELF_REVIEW
    assert verdict.conflicting_role == "author"


def test_a_one_person_table_is_legal_when_the_user_sets_the_bar() -> None:
    """一人表格沒有複查角色，由使用者本人訂驗收標準。

    這不是繞過規則，是剛好符合規則：使用者不是做事的那一個。畫面上要明講
    「這一組沒有人幫你檢查，所以怎樣算做完要你自己給」——那句話在
    `UI決定紀錄-2026-08-21.md` 第 4 題。
    """
    one_person = (
        Required(ROLE_USER, SUBJECT_ACCEPTANCE, StampKind.REVIEW),
        Required("author", SUBJECT_WORK),
        Required("author", SUBJECT_VERIFY),
    )
    chain = (
        ApprovalChain()
        .add(review("s1", ROLE_USER, SUBJECT_ACCEPTANCE))
        .add(machine("s2", "author", SUBJECT_WORK, depends_on=("s1",)))
        .add(machine("s3", "author", SUBJECT_VERIFY, depends_on=("s2",)))
    )

    assert chain.readiness(AT_V1, one_person).can_decide


# --- 沒得驗不是失敗 ---------------------------------------------------------


def test_nothing_to_verify_is_its_own_verdict_not_a_failure() -> None:
    """混在一起就會派人去修一個沒壞的東西。

    量到的，不是想像的：這個專案的測試印出「1334 passed」，離開碼卻因為
    關閉時崩潰而不是 0。照離開碼判，引擎就會去修一個沒壞的東西。
    """
    chain = (
        ApprovalChain()
        .add(machine("s1", "reviewer", SUBJECT_ACCEPTANCE))
        .add(machine("s2", "author", SUBJECT_WORK, depends_on=("s1",)))
        .add(
            machine(
                "s3",
                "reviewer",
                SUBJECT_VERIFY,
                result=Result.GATED,
                depends_on=("s2",),
            )
        )
    )

    verdict = chain.readiness(AT_V1, TWO_PERSON)

    assert verdict.verdict is Verdict.GATED
    assert verdict.verdict is not Verdict.FAILED
    assert "怎麼確認" in verdict.short


def test_a_red_check_outranks_a_missing_one() -> None:
    """先修壞掉的，再談缺不缺——順序決定使用者先去做哪一件事。"""
    chain = (
        ApprovalChain()
        .add(machine("s1", "reviewer", SUBJECT_ACCEPTANCE))
        .add(
            machine(
                "s2", "author", SUBJECT_WORK, result=Result.FAIL, depends_on=("s1",)
            )
        )
    )

    verdict = chain.readiness(AT_V1, TWO_PERSON)

    assert verdict.verdict is Verdict.FAILED
    assert verdict.blocking[0].stamp_id == "s2"


# --- 失效範圍由依賴決定 -----------------------------------------------------


def test_changing_the_content_drops_only_the_stamps_that_stood_on_it() -> None:
    """沒失效的不准跟著劃掉。這是硬條件。

    算不出依賴時唯一「誠實」的退路是全部一起劃掉——而那是說謊，它會逼
    使用者重做不需要重做的關。
    """
    chain = (
        ApprovalChain()
        .add(machine("s1", "reviewer", SUBJECT_ACCEPTANCE))
        .add(machine("s2", "author", SUBJECT_WORK, depends_on=("s1",)))
        # 驗收標準改了：這個章蓋在新版上，前面兩個沒有。
        .add(machine("s3", "reviewer", SUBJECT_ACCEPTANCE, digest=V2))
    )

    still_good = {s.stamp_id for s in chain.live(AT_V2)}
    fallen = {s.stamp_id for s in chain.invalidated_by(AT_V2)}

    assert still_good == {"s3"}
    assert fallen == {"s1", "s2"}


def test_a_stamp_falls_when_the_stamp_it_stood_on_falls() -> None:
    """依賴是會傳遞的：地基沒了，蓋在上面的也沒了。

    只比對版本碼不夠——一個章可能剛好蓋在新版上，卻建立在一個已經失效的
    章之上，那它就是一個站在空氣上的章。
    """
    chain = (
        ApprovalChain()
        .add(machine("s1", "reviewer", SUBJECT_ACCEPTANCE))
        # s2 蓋在新版上，但它說自己建立在 s1（舊版）之上。
        .add(machine("s2", "author", SUBJECT_WORK, digest=V2, depends_on=("s1",)))
    )

    assert chain.live(AT_V2) == ()
    assert {s.stamp_id for s in chain.invalidated_by(AT_V2)} == {"s1", "s2"}


def test_stamps_spread_over_two_versions_are_not_reported_as_missing() -> None:
    """章其實蓋過了，只是不在同一版上。

    報成「缺章」會讓使用者再蓋一次，然後得到一模一樣的結果——這正是那種
    「看起來很確定的錯誤」。
    """
    chain = (
        ApprovalChain()
        .add(machine("s1", "reviewer", SUBJECT_ACCEPTANCE))
        .add(machine("s2", "author", SUBJECT_WORK, depends_on=("s1",)))
        .add(machine("s3", "reviewer", SUBJECT_VERIFY, digest=V2))
    )

    verdict = chain.readiness(AT_V2, TWO_PERSON)

    assert verdict.verdict is Verdict.SPLIT_VERSION
    assert len(verdict.versions) == 2
    assert {s.stamp_id for s in verdict.blocking} == {"s1", "s2"}


# --- 版本碼要從內容算，不能是編的 -------------------------------------------


def test_the_version_code_comes_from_the_content_itself() -> None:
    """如果版本碼是誰自己編的，整個失效機制就是假的。

    哨兵回音：推一段不可能被寫死的內容進去，看它算出來的東西真的跟著變。
    """
    sentinel = "哨兵-9f3a2c-不可能被寫死"

    assert content_digest(sentinel) != content_digest(sentinel + "x")
    assert content_digest(sentinel) == content_digest(sentinel)
    assert len(content_digest(sentinel)) == 64


# --- 沒有輪數上限 -----------------------------------------------------------


def test_there_is_no_round_count_anywhere_in_the_verdict() -> None:
    """定案：不准設輪數上限，那會逼出「時間到就假裝過了」。

    這一條守的是**不存在**，所以它問的是資料形狀：Readiness 上不准長出
    任何一個看起來像分母的欄位。一旦有了「第 2 / 3 輪」，畫面就會把它畫成
    進度條，而進度條走到底的意思是「該放行了」。
    """
    fields = set(Readiness.__dataclass_fields__)

    forbidden = {
        name
        for name in fields
        if any(
            mark in name
            for mark in ("round", "attempt", "limit", "max", "total", "of_")
        )
    }

    assert not forbidden, (
        "Readiness 長出了看起來像輪數上限的欄位："
        + "、".join(sorted(forbidden))
    )


def test_the_reviewer_may_set_the_bar_and_also_sign_off_on_the_work() -> None:
    """那正是複查角色的工作，不是違規。

    「實作的章」只算機器章。同一個 subject 上的審查章意思是「我看過這份
    實作」——那是檢查，不是動手。

    這一條是 2026-08-21 接引擎時抓到的：把兩者混在一起，複查角色一旦既訂
    標準又簽核，整條鏈就會被判成「自己出考卷自己考」。正確的流程被擋下來，
    而使用者完全看不出為什麼——那比漏抓一次違規更貴。
    """
    chain = (
        ApprovalChain()
        .add(review("s1", "reviewer", SUBJECT_ACCEPTANCE))
        .add(machine("s2", "author", SUBJECT_WORK, depends_on=("s1",)))
        .add(machine("s3", "reviewer", SUBJECT_VERIFY, depends_on=("s2",)))
        # 複查看過作者交出來的東西，蓋一個審查章。這不是「做事」。
        .add(review("s4", "reviewer", SUBJECT_WORK, depends_on=("s3",)))
    )

    required = (
        Required("reviewer", SUBJECT_ACCEPTANCE, StampKind.REVIEW),
        Required("author", SUBJECT_WORK),
        Required("reviewer", SUBJECT_VERIFY),
    )

    verdict = chain.readiness(AT_V1, required)

    assert verdict.verdict is not Verdict.SELF_REVIEW, verdict.conflicting_role
    assert verdict.can_decide


# --- 審查章要回答得了「憑什麼」 --------------------------------------------


def test_a_review_stamp_without_a_reason_cannot_be_made() -> None:
    """只記「誰、何時、哪一版」的章，事後回溯回答不了最重要的那一題。

    老闆 2026-08-21 的原話：「簽核章需要有時間戳記**對應邏輯審核**還有你說的
    版本」。第一版把「審查章不准帶任何內容」寫死了，於是那個「邏輯審核」
    根本沒有地方放——**規則本身把需求擋在門外**。
    """
    with pytest.raises(ApprovalChainError, match="憑什麼"):
        Stamp(
            stamp_id="s1",
            kind=StampKind.REVIEW,
            role="reviewer",
            subject=SUBJECT_WORK,
            content_digest=V1,
            at=AT,
            reasoning="   ",
        )


def test_a_machine_stamp_cannot_carry_a_reason() -> None:
    """機器章記的是量出來的東西，不是誰的判斷。

    兩格分開，是為了讓下游一眼分得出「這是跑出來的」還是「這是有人認為的」。
    讓機器章也能寫判斷，那條界線就沒了。
    """
    with pytest.raises(ApprovalChainError, match="不准帶理由"):
        Stamp(
            stamp_id="s1",
            kind=StampKind.MACHINE,
            role="reviewer",
            subject=SUBJECT_VERIFY,
            content_digest=V1,
            at=AT,
            evidence="pytest -q -> 1383 passed",
            result=Result.PASS,
            reasoning="我覺得這樣可以",
        )


def test_the_reason_survives_being_written_and_read_back() -> None:
    """理由要能存下去再讀回來。

    存檔漏掉這一格的話，章在記憶體裡有理由、在硬碟上沒有——而事後回溯讀的
    是硬碟。
    """
    from app.services.approval_chain import _as_row, _from_row

    original = review("s1", "reviewer", SUBJECT_WORK, reasoning="邊界條件都檢查過了")
    round_tripped = _from_row(_as_row(original))

    assert round_tripped is not None
    assert round_tripped.reasoning == "邊界條件都檢查過了"


# --- 驗收標準與交付物是兩條版本線 -------------------------------------------
#
# 2026-08-22 定案【驗收標準要能被駁回】之後，驗收標準本身變成一份會改版的
# 東西。兩者綁同一個版本碼的話會出現一個看不出來的錯誤：訂標準的那一格先
# 訂好、蓋了章，執行的那一格一交出東西，版本碼就變了，**那個章當場失效**，
# 畫面上寫「沒有人訂標準」而事實是訂了。

CRITERIA_A = content_digest("驗收標準第一版")
CRITERIA_B = content_digest("驗收標準改過之後")


def test_the_bar_survives_the_deliverable_arriving() -> None:
    """訂標準在前、交東西在後，這是正常流程，不是版本分岔。

    綁同一條版本線的話這一條會紅，而且紅得毫無道理：使用者會看到「複查
    沒訂標準」，然後去叫它再訂一次，得到同樣的結果。
    """
    chain = (
        ApprovalChain()
        .add(review("bar", "reviewer", SUBJECT_ACCEPTANCE, digest=CRITERIA_A))
        .add(
            machine(
                "work", "author", SUBJECT_WORK, digest=V1, depends_on=("bar",)
            )
        )
    )

    alive = {s.stamp_id for s in chain.live(Versions(CRITERIA_A, V1))}

    assert alive == {"bar", "work"}


def test_changing_the_bar_drops_the_work_that_stood_on_it() -> None:
    """標準一改，蓋在舊標準上的實作跟著失效。

    這就是【驗收標準要能被駁回】要的行為，而且是自動的——沒有人需要記得
    去作廢誰。走的是既有的 `depends_on`，不是另外一條規則。
    """
    chain = (
        ApprovalChain()
        .add(review("bar", "reviewer", SUBJECT_ACCEPTANCE, digest=CRITERIA_A))
        .add(
            machine(
                "work", "author", SUBJECT_WORK, digest=V1, depends_on=("bar",)
            )
        )
    )

    fallen = {s.stamp_id for s in chain.invalidated_by(Versions(CRITERIA_B, V1))}

    assert fallen == {"bar", "work"}, "標準改了，實作的章沒有跟著掉下來"


def test_changing_the_deliverable_leaves_the_bar_alone() -> None:
    """東西改了，標準不受影響——反過來不成立，那正是要的不對稱。

    對稱的話，作者每交一次件就要有人重新訂一次標準，而標準沒有變。
    """
    chain = ApprovalChain().add(
        review("bar", "reviewer", SUBJECT_ACCEPTANCE, digest=CRITERIA_A)
    )

    alive = {s.stamp_id for s in chain.live(Versions(CRITERIA_A, V2))}

    assert alive == {"bar"}


def test_the_bar_is_not_counted_as_a_second_version_of_the_deliverable() -> None:
    """版本分岔只看交付物那條線。

    把驗收標準的版本碼算進去的話，任何一次正常執行都會出現兩個版本碼，
    於是「缺章」永遠被報成「版本分岔」——而使用者照著那句話去做（先把
    內容收斂到同一版）永遠不會有效果，因為根本沒有分岔。
    """
    chain = (
        ApprovalChain()
        .add(review("bar", "reviewer", SUBJECT_ACCEPTANCE, digest=CRITERIA_A))
        .add(
            machine(
                "work", "author", SUBJECT_WORK, digest=V1, depends_on=("bar",)
            )
        )
    )

    required = (
        Required("reviewer", SUBJECT_ACCEPTANCE, StampKind.REVIEW),
        Required("author", SUBJECT_WORK, StampKind.MACHINE),
        Required("reviewer", SUBJECT_VERIFY, StampKind.MACHINE),
    )

    verdict = chain.readiness(Versions(CRITERIA_A, V1), required)

    assert verdict.verdict is Verdict.MISSING
    assert [item.subject for item in verdict.missing] == [SUBJECT_VERIFY]


def test_a_stale_work_stamp_is_missing_not_a_version_split() -> None:
    """交付物改了、標準沒改——那是「實作要重做」，不是「大家各簽各的版本」。

    這一條分的是兩句**對使用者意義完全相反**的話：

    * 缺章 → 去把那一關重跑一次就好。
    * 版本分岔 → 補章沒有用，要先把內容收斂到同一版。

    把驗收標準的版本碼算進「現場有幾個版本」的話，任何一次正常執行都會有
    兩個版本碼（標準一個、交付物一個），於是**每一次缺章都會被報成版本
    分岔**，而使用者照著那句話去做永遠不會有效果——他手上根本沒有兩個版本
    的交付物。
    """
    chain = (
        ApprovalChain()
        .add(review("bar", "reviewer", SUBJECT_ACCEPTANCE, digest=CRITERIA_A))
        .add(
            machine(
                "work", "author", SUBJECT_WORK, digest=V1, depends_on=("bar",)
            )
        )
    )
    required = (
        Required("reviewer", SUBJECT_ACCEPTANCE, StampKind.REVIEW),
        Required("author", SUBJECT_WORK, StampKind.MACHINE),
        Required("reviewer", SUBJECT_VERIFY, StampKind.MACHINE),
    )

    # 標準沒動，交付物換成第二版。
    verdict = chain.readiness(Versions(CRITERIA_A, V2), required)

    assert verdict.verdict is Verdict.MISSING, (
        f"報成了 {verdict.verdict.value}——只有交付物換了一版，"
        "現場沒有兩個版本的交付物可以收斂"
    )


def test_re_running_the_same_check_lets_the_newer_answer_win() -> None:
    """同一個人、同一件事、同一種章，**後蓋的蓋掉先蓋的**。

    沒有這一條的話，一次「跑紅了 → 修好了 → 再跑綠了」的執行，鏈子上會同時
    留著那個紅的與那個綠的。而「有紅的」排在「缺章」前面，所以拍板檢查會說
    **「有檢查是紅的」**——那句話是對的，只是它講的是已經被修掉的那一次，
    而使用者會照著它回去修一個已經好了的東西。

    只在**同一版內容**上成立：內容換了版本碼就不同，早就被擋在外面。所以
    這不是「用新的蓋掉舊的證據」，是「對同一份東西重跑了一次，以最後那次
    為準」——兩次都留在檔案裡，回溯看得到。
    """
    chain = (
        ApprovalChain()
        .add(
            machine(
                "first", "engine", SUBJECT_VERIFY, result=Result.FAIL
            )
        )
        .add(
            machine(
                "second", "engine", SUBJECT_VERIFY, result=Result.PASS
            )
        )
    )

    alive = chain.live(AT_V1)

    assert [s.stamp_id for s in alive] == ["second"], (
        "重跑之後那個紅的還算數——拍板檢查會說「有檢查是紅的」，"
        "而它講的是已經被修掉的那一次"
    )
    # 兩個都還在檔案裡。蓋掉的是「算不算數」，不是紀錄。
    assert [s.stamp_id for s in chain.stamps] == ["first", "second"]


def test_a_newer_answer_from_somebody_else_does_not_shadow_yours() -> None:
    """蓋掉只在**同一個人、同一件事、同一種章**上成立。

    放寬成「同一件事」的話，複查的審查章會蓋掉核准的審查章——兩個都是
    `work` 上的審查章，而它們是兩件完全不同的事（我看過這份實作／我看過
    所有的章）。少一個，拍板就少一個憑據，而畫面上看起來一切正常。
    """
    chain = (
        ApprovalChain()
        .add(review("by-review", "複查", SUBJECT_WORK, reasoning="實作看過了"))
        .add(review("by-approve", "核准", SUBJECT_WORK, reasoning="章都齊了"))
    )

    alive = {s.stamp_id for s in chain.live(AT_V1)}

    assert alive == {"by-review", "by-approve"}
