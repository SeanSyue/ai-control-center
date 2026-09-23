r"""他排過的那一格不見了，畫面不准說「你沒排」。

## FG20

一格位子的某一欄這一版讀不懂（例如廠牌多了一家而版本號沒跟著跳），
它會變成 `UnreadableSlot`——**留在檔案裡，但派不動**。

而複查那一關沒有人可派的時候，時間軸上只多一行：

> 這條線上沒有任何一格的職責是「複查」，所以這一輪沒有人複查過產出。

**那句話在有讀不出來的位子時是假的。**他排了，只是被丟掉了。
照那句講，他會以為是自己的陣容本來就沒有那一格——
**而不是「有一格被我丟掉了」**。

而那一輪的收場是**完成／可以拍板**，畫面上的節點數少一個，
**沒有任何一句話說少了什麼**。

## 為什麼講得出名字

走到 `UnreadableSlot` 的前提就是「**名字讀得出來**，只有某一欄這一版不認得」
（連名字都沒有的那種當垃圾丟）。所以那幾格叫什麼是知道的——
**講不出名字的警告等於沒有警告**。

## 它擋不住什麼

擋不住「那一格根本沒有進到 `unreadable`」——那是讀檔那一側的事，
`tests/test_a_record_you_cannot_read_is_not_a_clean_record.py` 在守。
"""
from __future__ import annotations

from app.core.lineup import AgentSlot, Lineup, UnreadableSlot
from app.core.workflow_worker import why_nobody_reviewed
from app.i18n import t


def _lineup_with_one_lost() -> Lineup:
    """一格讀得出來、一格讀不出來——而讀不出來的那一格**名字是知道的**。"""
    return Lineup(
        (AgentSlot("node1", "claude", "c1", "", "", "work"),),
        (UnreadableSlot(index=1, role="node3", field="provider", raw="{}"),),
    )


def test_the_engine_still_knows_what_it_dropped() -> None:
    """前提檢查：名字掉了的話，底下那一條講不出東西來。"""
    assert _lineup_with_one_lost().unreadable_names == ("node3",)


def test_it_does_not_say_he_never_seated_a_reviewer() -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是它 2026-08-26 之前的樣子：不管有沒有讀不出來的位子，
    都講同一句「這條線上沒有任何一格的職責是複查」。

    **問的是「選哪一句」，不是「那兩句長什麼樣」。**第一版問後者，
    而破壞驗證 L1 當場判它沒有牙齒——把選擇那一行改成永遠選第二句，
    兩句話還是好好的，測試照樣綠。
    """
    said = why_nobody_reviewed(_lineup_with_one_lost())
    assert "不是你沒排" in said, said
    assert said != t("lineup.no_reviewer_note"), (
        "有一格被丟掉了，而它講的是「你沒排複查」"
    )


def test_it_names_the_seats_that_went_missing() -> None:
    """講得出**是哪幾格**。

    「有幾格讀不出來」寫進紀錄等於沒寫——他要知道去陣容頁看哪一格。
    """
    said = t("lineup.no_reviewer_but_some_unreadable", count=2, names="node3、node4")
    assert "node3" in said and "node4" in said, said
    assert "2" in said, said


def test_it_says_the_settings_are_still_there() -> None:
    """**不准讓他以為那一格被刪了。**

    設定還在檔案裡。少了這一句，他會去陣容頁重排一格——
    而那會蓋掉原本那一格的設定。
    """
    said = t("lineup.no_reviewer_but_some_unreadable", count=1, names="node3")
    assert "沒有被刪掉" in said or "還在" in said, said


def test_the_plain_sentence_is_still_there_for_when_he_really_did_not() -> None:
    """**另一半：真的沒排的時候，不准硬說有東西被丟掉。**

    只做上面那幾條的話，把兩種情況都換成新那一句是綠的——
    而那會讓一個真的沒排複查的人去找一格不存在的位子。
    """
    plain = why_nobody_reviewed(Lineup.of([AgentSlot("node1", "claude")]))
    assert "沒有任何一格的職責是「複查」" in plain, plain
    assert "讀不出來" not in plain, plain
