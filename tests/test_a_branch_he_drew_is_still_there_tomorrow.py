r"""老闆 2026-08-22 要的那個框：**分支存得下來**，而舊檔打開還是同一排。

他的原話（`ROADMAP.md`）：

> 有分支或沒分支無所謂，應該要是在一個方框內，有需要才往下增加分支，
> 然後才把方框垂直距離增加放下第二條線。

也就是：**左右＝階層、同一階層的分支往下堆、框的高度＝最多那一層有幾個、
連線畫在框裡。現在那一排卡片，就是「每一層剛好一個」的特例。**

2026-08-27 之前引擎存不下那件事：`AgentSlot` 只有
`role / provider / session_id / credential / identity / duty / writes_files`，
`Lineup.slots` 是一個平的順序 tuple——**所以畫面畫什麼都是編的**。

## 這一支守四件事，而且四件都被弄紅過

| | 守什麼 | 弄紅它的樣子 |
|---|---|---|
| 一 | **舊檔照樣開得起來，而且長相一樣** | 空的 `parent` 讀成「我是第一層」→ 舊專案打開變成九條各自為政的支線 |
| 二 | **出廠陣容的形狀沒有變** | 出廠那幾格被拉出一條分支 → 一台新機器打開就不是那一排 |
| 三 | **不合法的形狀被擋下來，而且講得出是哪一格** | 擋了但不指名 → 他有九格可以猜 |
| 四 | **這份資料真的推得到畫面那一側** | 算出來沒有人讀——這個專案反覆踩的那一個 |

## 為什麼是「上一格是誰」而不是「第幾層」

`app/core/lineup.py` 的 `PARENT_PREVIOUS` 寫了理由，這裡只記結論：他要的是
**框裡的連線**，而一個 int 存得下「這兩格同層」，存不下「這一條線接誰」。

## 這一支守不到什麼（誠實講）

**守不到「線畫出來長什麼樣」。**這一輪畫面一個像素都沒有動——`layer` 與
`upstream` 推出去了，頁面還沒有接（登記在
`tests/test_no_field_is_pushed_into_the_void.py` 的待接清單上，接上的那天
那張清單會自己要求被刪）。這裡量得到的是**引擎講出來的那個答案對不對**，
不是它被畫成什麼。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.lineup import (  # noqa: E402
    DUTY_APPROVE,
    DUTY_REVIEW,
    DUTY_WORK,
    LINK_FORWARD,
    LINK_MISSING,
    AgentSlot,
    Lineup,
    LineupError,
)
from app.services.session_registry import SessionRegistry  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402

# 他自己取的名字。**不准換成 `author`／`reviewer`**：出廠名字會被
# `_DUTY_BY_LEGACY_ROLE` 推回原值，好幾種掉欄位的病在出廠名字上是看不見的。
# 同一個理由寫在 `test_a_seat_keeps_its_duty_through_every_button.py`。
FIRST = "發想"
SECOND = "粗剪"
THIRD = "上字幕"


def three_seats() -> Lineup:
    """三格，每一格的每一欄都不一樣，**線是空的**（＝今天那一排）。"""
    return Lineup.of(
        [
            AgentSlot(FIRST, "codex", session_id="chat-plan", duty=DUTY_REVIEW),
            AgentSlot(SECOND, "claude", session_id="chat-work", duty=DUTY_WORK),
            AgentSlot(THIRD, "codex", session_id="chat-check", duty=DUTY_APPROVE),
        ]
    )


def branched() -> Lineup:
    """第三格改接第一格。**兩條支線並排在第二層**，框長高一列。

        [發想] ─┬─ [粗剪]
                └─ [上字幕]
    """
    return three_seats().with_parent(THIRD, FIRST)


# ---- 一、舊檔照樣開得起來 -------------------------------------------------


def _old_sessions_json(project: Path) -> list[dict]:
    """2026-08-27 以前的 `.aicc/sessions.json` 裡那個 `lineup` 長什麼樣。

    **一個字都沒有 `parent`**——這才是重點：這一輪加的欄位在那些檔案裡
    根本不存在，而那些檔案是使用者真的排過的東西。
    """
    return [
        {
            "role": FIRST,
            "provider": "codex",
            "session_id": "chat-plan",
            "credential": "",
            "identity": "",
            "duty": DUTY_REVIEW,
            "writes_files": "",
        },
        {
            "role": SECOND,
            "provider": "claude",
            "session_id": "chat-work",
            "credential": "",
            "identity": "你負責把它做出來。",
            "duty": DUTY_WORK,
            "writes_files": "off",
        },
        {
            "role": THIRD,
            "provider": "codex",
            "session_id": "chat-check",
            "credential": "",
            "identity": "",
            "duty": DUTY_APPROVE,
            "writes_files": "",
        },
    ]


def test_a_project_saved_before_branches_opens_as_the_same_row(tmp_path) -> None:
    """**這一條是這份檔案存在的第一個理由。**

    一份沒有這個欄位的 `sessions.json` 讀回來，要是**今天畫面上那一排**：
    每一層剛好一格、框高一列、一條接不上的線都沒有。

    弄紅它的樣子：空的 `parent` 讀成「我是第一層」。那時候每一格都在第 0 層、
    框變成三列高——**而每一格的名字、職責、對話都還在**，所以看起來完全正常。
    """
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    registry = SessionRegistry(project)
    payload = registry.load()
    payload["lineup"] = _old_sessions_json(project)
    registry.save(payload)

    lineup = SessionRegistry(project).load_lineup()

    assert lineup.roles == (FIRST, SECOND, THIRD)
    shape = lineup.shape
    assert shape.problems == (), f"舊檔讀出一條接不上的線：{shape.problems}"
    assert shape.layers == (0, 1, 2), "舊檔不再是「每一層剛好一格」"
    assert shape.rows == 1, f"框從一列變成 {shape.rows} 列——舊專案的長相變了"
    assert shape.upstream == ("", FIRST, SECOND), "舊檔那條直鏈接錯了"


def test_a_project_saved_before_branches_keeps_every_field_it_had(tmp_path) -> None:
    """前提檢查：上一條量的是形狀，這一條量的是**那份檔案本身沒有掉東西**。

    少了它，上一條在「讀進來的其實是出廠陣容」的情況下也會通過——
    出廠陣容也是每一層剛好一格。
    """
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    registry = SessionRegistry(project)
    payload = registry.load()
    payload["lineup"] = _old_sessions_json(project)
    registry.save(payload)

    lineup = SessionRegistry(project).load_lineup()
    seated = lineup.for_role(SECOND)

    assert seated is not None
    assert seated.provider == "claude"
    assert seated.session_id == "chat-work"
    assert seated.duty == DUTY_WORK
    assert seated.writes_files == "off", "他親手關掉的寫檔開關被升級打開了"
    assert seated.identity == "你負責把它做出來。"


def test_opening_and_saving_an_old_project_does_not_rewrite_its_shape(tmp_path) -> None:
    """讀進來再存回去（`save()` 每次都走這一趟），那一排不准變成別的東西。

    這是 `Lineup.to_list()` 那條止血線的新一半：新欄位寫回去的值必須是
    「還沒有人被問過這一題」，不是這一版自己推出來的一條線。
    """
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    registry = SessionRegistry(project)
    payload = registry.load()
    payload["lineup"] = _old_sessions_json(project)
    registry.save(payload)

    again = SessionRegistry(project)
    again.save_lineup(again.load_lineup())

    stored = json.loads((project / ".aicc" / "sessions.json").read_text("utf-8"))
    assert [row["parent"] for row in stored["lineup"]] == ["", "", ""], (
        "讀一次再存一次，AICC 替他寫了一條他沒有畫過的線："
        f"{[row.get('parent') for row in stored['lineup']]}"
    )
    assert SessionRegistry(project).load_lineup().shape.rows == 1


# ---- 二、出廠陣容的形狀沒有變 ---------------------------------------------


def test_every_shipped_line_up_is_still_one_seat_per_layer() -> None:
    """**一台新機器打開，一個使用者看得到的差別都不准有。**

    出廠的那幾份（經典兩人、加上規劃者、以及每一張出廠模式）全部都要是
    「每一層剛好一格」——框高一列，也就是現在那一排。

    弄紅它的樣子：任何一份出廠陣容被拉出一條分支。那時候第一次打開 AICC
    看到的就不是那一排，而這一輪的界線寫得很清楚：**這一輪是讓資料存得下
    分支，不是改預設長相。**
    """
    from app.services.lineup_presets import built_in_presets

    shipped = [Lineup.classic(), Lineup.classic().with_planner()]
    presets = built_in_presets()
    # 前提檢查：出廠模式一張都沒有的話，底下的迴圈什麼都沒檢查到。
    assert presets, "一張出廠模式都沒有——這條檢查沒有材料"
    shipped += [preset.lineup for preset in presets]

    for lineup in shipped:
        shape = lineup.shape
        assert shape.problems == (), f"{lineup.roles} 出廠就帶著一條接不上的線"
        assert shape.rows == 1, f"{lineup.roles} 出廠的框是 {shape.rows} 列，不是一列"
        assert shape.layers == tuple(range(len(lineup.roles))), (
            f"{lineup.roles} 出廠不再是「每一層剛好一格」：{shape.layers}"
        )
        assert all(slot.parent == "" for slot in lineup), (
            f"{lineup.roles} 出廠就有人替他拉過線"
        )


# ---- 三、分支真的存得下來 -------------------------------------------------


def test_a_branch_stacks_under_the_same_layer_and_makes_the_frame_taller() -> None:
    """把第三格接到第一格後面 → 它跟第二格**同一層**，框長高到兩列。

    這就是老闆那句話的資料版：**橫向管「有幾層」、垂直管「那一層有幾條」。**
    """
    shape = branched().shape

    assert shape.problems == ()
    assert shape.layers == (0, 1, 1), f"第三格沒有跟第二格同層：{shape.layers}"
    assert shape.rows == 2, f"框沒有長高，還是 {shape.rows} 列"
    assert shape.upstream == ("", FIRST, FIRST), "那兩條線沒有都接到第一格"


def test_a_branch_survives_being_written_to_disk_and_read_back(tmp_path) -> None:
    """他畫的分支，**下一次打開還在**。

    弄紅它的樣子：`to_dict()` 不存這一欄。那時候框在他眼前是對的，關掉再
    打開就攤成一排——而攤成一排的那一排看起來完全正常。
    """
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    SessionRegistry(project).save_lineup(branched())

    back = SessionRegistry(project).load_lineup()

    assert back.shape == branched().shape, "存到硬碟再讀回來，形狀變了"
    assert back.shape.rows == 2


def test_a_saved_mode_remembers_how_the_seats_were_wired() -> None:
    """存成模式再讀回來，**線也要在**。

    【角色表格】那條定案：一張表格 = 一組角色**加上它們怎麼連**。少了這一
    欄，一張分支表格套用回去會攤成一排，而表格的名字、格數、每一格的職責
    看起來一模一樣。
    """
    from app.services.lineup_presets import LineupPreset

    original = LineupPreset(name="剪片用", description="", lineup=branched())
    restored = LineupPreset.from_dict(original.to_dict())

    assert restored is not None
    assert restored.lineup.shape.upstream == ("", FIRST, FIRST)
    assert restored.lineup.shape.rows == 2


# ---- 四、不合法的形狀被擋下來，而且講得出是哪一格 -------------------------


def test_pointing_at_a_slot_that_is_not_there_is_refused_by_name() -> None:
    """接到一個不存在的名字 → 擋下來，而且**兩個名字都要出現在那句話裡**。

    只講「形狀不合法」等於沒講：他有九格。
    """
    with pytest.raises(LineupError) as caught:
        three_seats().with_parent(THIRD, "沒有這一格")

    said = str(caught.value)
    assert THIRD in said, f"沒有講是哪一格：{said}"
    assert "沒有這一格" in said, f"沒有講他想接的是誰：{said}"


def test_pointing_at_a_slot_that_runs_later_is_refused_by_name() -> None:
    """接到一個排在自己後面的格子 → 擋下來。

    這一條是這個資料結構的地基，不是額外的保險：`slots` 的順序就是執行
    順序，一格收不到還沒跑的那一格產出的東西。而它同時讓**環變成不可能
    存在**——所以這個檔案裡沒有環偵測，也不該有。
    """
    with pytest.raises(LineupError) as caught:
        three_seats().with_parent(FIRST, THIRD)

    said = str(caught.value)
    assert FIRST in said and THIRD in said, f"沒有指名是哪兩格：{said}"


def test_a_cycle_cannot_be_built_one_step_at_a_time() -> None:
    """兩步走出一個環也不行。**這一條量的是那條規則的強度，不是某一次呼叫。**

    a 接不到排在後面的 b（上一條），所以要繞的話得先讓 b 接 a、再讓 a 接 b
    ——第二步一樣被同一條規則擋掉。環在這個資料結構裡**不可能存在**。
    """
    one_way = three_seats().with_parent(SECOND, FIRST)

    with pytest.raises(LineupError):
        one_way.with_parent(FIRST, SECOND)


def test_a_slot_cannot_be_wired_to_itself() -> None:
    """自己接自己 → 這一格讀不出來，而**名字還在、原文一個字都沒動**。

    在 `AgentSlot` 那一層擋，換到的是一份手改壞的檔案讀回來時走既有的
    `UnreadableSlot` 那條路：畫得出名字、派不到工、寫不了任何東西。
    """
    from app.core.lineup import FIELD_PARENT

    with pytest.raises(LineupError) as caught:
        AgentSlot(FIRST, "codex", parent=FIRST)
    assert caught.value.field == FIELD_PARENT
    assert FIRST in str(caught.value)

    lineup = Lineup.from_list(
        [
            {"role": FIRST, "provider": "codex", "parent": FIRST},
            {"role": SECOND, "provider": "claude"},
        ]
    )
    assert lineup.unreadable_names == (FIRST,), (
        "自己接自己的那一格被安靜丟掉了——那是無聲的資料損失"
    )
    assert json.loads(lineup.unreadable[0].raw)["parent"] == FIRST, "原文被改寫了"


def test_wiring_a_seat_to_itself_is_refused_in_its_own_words() -> None:
    """**擋對了不算數，話要講對。**

    這一條 2026-08-27 覆核的時候補上，因為上面那一條有個洞：它量的是
    `AgentSlot` 那一層，而**畫面走不到那一層**。畫面唯一的入口是
    `with_parent`，而在那裡「接自己」會先撞上「接一個排在自己後面的格子」
    ——於是使用者把一格接到它自己身上，收到的是

        「「發想」排在「發想」後面⋯⋯先把「發想」往前移，再接一次。」

    一句叫他把發想移到發想前面的話。擋下來了，而**下一步是錯的**。

    所以這裡量的是**哪一句話**，不是「有沒有擋」：三條路要各講各的，講錯
    的那一句跟講對的那一句在紀錄上長得一模一樣。
    """
    from app.i18n import t

    with pytest.raises(LineupError) as caught:
        three_seats().with_parent(FIRST, FIRST)
    said = str(caught.value)

    with pytest.raises(LineupError) as other:
        three_seats().with_parent(FIRST, THIRD)
    runs_later = str(other.value)

    assert said == t("lineup.parent_is_itself", role=FIRST), (
        f"接自己收到的不是它自己那一句：{said}"
    )
    assert said != runs_later, (
        "接自己跟接一個排在後面的格子講同一句話——而那一句叫他"
        "「把它往前移」，移的對象就是它自己"
    )
    assert FIRST in said, f"沒有講是哪一格：{said}"


def test_a_hand_edited_file_that_cannot_be_drawn_names_the_slot(tmp_path) -> None:
    """一份手改壞的檔案：那一格**還坐在那裡**，而形狀說得出接不上的是誰。

    讀檔這一層不丟例外，理由跟當年 `vendor_cannot_write` 那一支寫的一樣
    （它 2026-09-01 拿掉了，但那個理由本身沒有過期）：在那裡丟例外的
    結果是整份陣容變成「讀不出來」，他會看到一個空的編組頁，而**說不出
    哪一格有問題**。
    """
    lineup = Lineup.from_list(
        [
            {"role": FIRST, "provider": "codex"},
            {"role": SECOND, "provider": "claude", "parent": "被他刪掉的那一格"},
            {"role": THIRD, "provider": "codex", "parent": FIRST},
        ]
    )

    assert lineup.roles == (FIRST, SECOND, THIRD), "接不上的那一格被丟掉了"
    shape = lineup.shape
    assert [(bad.role, bad.reason) for bad in shape.problems] == [
        (SECOND, LINK_MISSING)
    ], f"沒有指名接不上的是哪一格：{shape.problems}"
    assert shape.problems[0].parent == "被他刪掉的那一格", "沒有講他想接的是誰"


def test_a_shape_that_cannot_be_resolved_is_not_half_guessed() -> None:
    """解不開的時候**整個退回執行順序那一排**，不拿接得上的那幾條拼半成品。

    弄紅它的樣子：把接得上的那幾條照樣算層次、接不上的那一格擺在第 0 層。
    那張圖**看起來就是他排的**，而其中有幾條線是程式自己補的——他會照著
    一張沒有人畫過的圖做事。
    """
    lineup = Lineup.from_list(
        [
            {"role": FIRST, "provider": "codex"},
            {"role": SECOND, "provider": "claude", "parent": THIRD},
            {"role": THIRD, "provider": "codex", "parent": FIRST},
        ]
    )
    shape = lineup.shape

    assert [(bad.role, bad.reason) for bad in shape.problems] == [
        (SECOND, LINK_FORWARD)
    ]
    assert shape.layers == (0, 1, 2), f"拼了一個半成品出來：{shape.layers}"
    assert shape.rows == 1
    assert shape.upstream == ("", FIRST, SECOND), (
        "退回去的那一排不是執行順序，是別的東西"
    )


# ---- 一格被移走／改名的時候，它下面那一層怎麼辦 ---------------------------


def test_removing_a_slot_in_the_middle_joins_the_line_back_up() -> None:
    """拿掉中間那一格，線接起來——**今天那一排本來就是這樣**。

    a → b → c，拿掉 b 之後畫面上是 a → c。分支只是把同一件事寫出來。
    """
    chain = three_seats().with_parent(SECOND, FIRST).with_parent(THIRD, SECOND)

    after = chain.without_node(SECOND)

    assert after.roles == (FIRST, THIRD)
    seated = after.for_role(THIRD)
    assert seated is not None
    assert seated.parent == FIRST, f"線沒有接起來，它還指著被刪掉的那一格：{seated.parent}"
    assert after.shape.problems == ()


def test_removing_the_first_slot_under_a_branch_names_the_orphans() -> None:
    """拿掉第一層那一格、而底下明著接了兩條支線 → 擋下來，**指名那幾格**。

    那時候沒有「上一格」可以接。把兩條支線串起來是替他發明一條他沒有畫過
    的線，而畫出來看起來就像他畫的。
    """
    forked = three_seats().with_parent(SECOND, FIRST).with_parent(THIRD, FIRST)

    with pytest.raises(LineupError) as caught:
        forked.without_node(FIRST)

    said = str(caught.value)
    assert SECOND in said and THIRD in said, f"沒有指名是哪幾格接不上了：{said}"


def test_renaming_a_slot_carries_the_lines_hanging_off_it() -> None:
    """改一格的名字，**接在它下面的那幾條線跟著改**。

    線是用名字拉的。改完名字不改線的話，那幾條線指向一個已經不存在的名字
    ——框整個退回那一排，而他只是改了一個字。
    """
    after = branched().renamed(FIRST, "腳本")

    assert after.shape.problems == (), "改個名字就把線改斷了"
    assert after.shape.upstream == ("", "腳本", "腳本")
    assert after.shape.rows == 2


def test_moving_a_seat_that_would_break_a_line_is_refused() -> None:
    """搬動會把線弄壞的時候 → 擋下來，不自動改線。

    自動改的話，他排一次順序，AICC 把他畫的分支接到另一格身上——而改完的
    那張圖看起來就像他畫的。
    """
    chain = three_seats().with_parent(THIRD, SECOND)

    with pytest.raises(LineupError) as caught:
        chain.moved(THIRD, 0)

    said = str(caught.value)
    assert THIRD in said and SECOND in said, f"沒有指名是哪一條線：{said}"


def test_moving_a_seat_on_a_row_that_nobody_wired_still_works() -> None:
    """前提檢查：**今天那一排搬得動**。

    少了它，上一條可以靠「`moved` 一律丟例外」通過，而那會把上下移動整個
    弄壞——出廠陣容一格都移不動，而沒有任何一條會紅。
    """
    after = three_seats().moved(THIRD, 0)

    assert after.roles == (THIRD, FIRST, SECOND)
    assert after.shape.rows == 1


# ---- 四、這份資料真的推得到畫面那一側 -------------------------------------


def _panel(tmp_path: Path, monkeypatch) -> LineupWebPanel:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    panel = LineupWebPanel(project)
    # 掃這台機器上所有本機對話會讓這一支變成非決定性的。
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    return panel


def test_the_page_is_told_which_layer_each_slot_is_on(tmp_path, monkeypatch) -> None:
    """**這一條是「不是算出來沒人讀」那一半。**

    算得出來、存得下來，然後沒有推給畫面——這個專案找到的真 bug 超過一半
    是那個形狀。所以這裡量的是 `collect_state()` 真的送出去的那一份。
    """
    panel = _panel(tmp_path, monkeypatch)
    panel._lineup = branched()

    state = panel.collect_state()["lineup"]

    assert [slot["layer"] for slot in state["slots"]] == [0, 1, 1], (
        f"層次沒有推出去或推錯了：{[s.get('layer') for s in state['slots']]}"
    )
    assert [slot["upstream"] for slot in state["slots"]] == ["", FIRST, FIRST], (
        f"線沒有推出去或推錯了：{[s.get('upstream') for s in state['slots']]}"
    )
    assert state["rows"] == 2, f"框的高度沒有推出去或推錯了：{state.get('rows')}"
    assert state["shape_problems"] == []


def test_the_page_is_told_which_slot_cannot_be_drawn(tmp_path, monkeypatch) -> None:
    """接不上的那幾格要**推到畫面上**，而不是只在 Python 裡知道。

    畫面收到一個空的 `shape_problems` 跟收不到這個欄位長得一模一樣，所以
    這裡量的是有東西的那一種。
    """
    panel = _panel(tmp_path, monkeypatch)
    panel._lineup = Lineup.from_list(
        [
            {"role": FIRST, "provider": "codex"},
            {"role": SECOND, "provider": "claude", "parent": "不存在的那一格"},
        ]
    )

    state = panel.collect_state()["lineup"]

    assert state["shape_problems"] == [
        {"role": SECOND, "parent": "不存在的那一格", "reason": LINK_MISSING}
    ], f"接不上的那一格沒有推到畫面上：{state['shape_problems']}"
    # 而且推出去的形狀是**退回去的那一排**，不是半成品。
    assert [slot["layer"] for slot in state["slots"]] == [0, 1]


def test_the_page_can_send_the_wiring_back(tmp_path, monkeypatch) -> None:
    """畫面按下去要**送得回來**：`set_slot_parent|<第幾格>:<上一格的名字>`。

    這是分支唯一的入口。少了它，`layer` 與 `upstream` 是一組永遠等於
    「每一層一格」的常數——推出去很好看，而使用者一條分支都畫不出來。
    """
    panel = _panel(tmp_path, monkeypatch)
    panel._lineup = three_seats()

    panel.handle_action("set_slot_parent", f"2:{FIRST}")

    assert panel._lineup.shape.upstream == ("", FIRST, FIRST)
    assert panel.collect_state()["lineup"]["rows"] == 2
    # 而且真的寫到硬碟上了——推給畫面但沒有存，關掉再打開就沒了。
    assert SessionRegistry(panel._project).load_lineup().shape.rows == 2


def test_a_refused_wiring_reaches_the_user_and_the_line_up_does_not_move(
    tmp_path, monkeypatch
) -> None:
    """接不上的時候要**說一句話**，而且陣容一個字都不准變。

    按了沒反應跟接好了長得一模一樣，而網頁那一側是樂觀更新的：他按下去，
    那條線當場就畫上去了。
    """
    panel = _panel(tmp_path, monkeypatch)
    panel._lineup = three_seats()
    said: list[str] = []
    panel.action_failed.connect(said.append)

    panel.handle_action("set_slot_parent", "0:沒有這一格")

    assert said, "接不上，而畫面上一個字都沒說"
    assert FIRST in said[0], f"沒有講是哪一格：{said[0]}"
    assert panel._lineup.shape.upstream == ("", FIRST, SECOND), "被拒絕了卻改到了"

    # 第二半（2026-08-27 覆核補的）：**接自己也是畫面送得出來的**，而它
    # 以前收到的是「「粗剪」排在「粗剪」後面，先把「粗剪」往前移」。
    # 這裡量的是那一句被換成它自己那一句，而且是**走完整條路**——
    # `handle_action` → `_set_slot_parent` → `with_parent` → `reject()`。
    from app.i18n import t

    said.clear()
    panel.handle_action("set_slot_parent", f"1:{SECOND}")

    assert said == [t("lineup.parent_is_itself", role=SECOND)], (
        f"從畫面把一格接到它自己身上，收到的話是：{said}"
    )
    assert panel._lineup.shape.upstream == ("", FIRST, SECOND), "被拒絕了卻改到了"


def test_the_arrows_on_the_page_do_not_rewire_anything(tmp_path, monkeypatch) -> None:
    """上下移動那兩顆箭頭**不准改到他畫的那條線指向誰**。

    這一條的**理由 2026-08-27 換了一次，結論沒換。**

    以前：名字留在原位（`_move_slot` 只換坐在位子上的廠牌與對話），而線是用
    名字拉的，所以線也必須留在原位。
    現在：老闆拍板箭頭搬的是整個 node，名字跟著那一格走——所以線也跟著那一格
    走，而它指向的**還是同一格**。

    兩種實作要擋的是同一個症狀：「上字幕接在發想後面」按一次箭頭變成
    「粗剪接在發想後面」，而畫面上一個字都不說。

    **所以斷言打在「誰接誰」上，不是打在「第幾格有一條線」上**——後者兩種
    實作都是綠的，包含壞掉的那一種。
    """
    panel = _panel(tmp_path, monkeypatch)
    panel._lineup = Lineup.of(
        [
            AgentSlot(FIRST, "codex", duty=DUTY_REVIEW),
            AgentSlot(SECOND, "codex", duty=DUTY_REVIEW),
            AgentSlot(THIRD, "codex", duty=DUTY_APPROVE, parent=FIRST),
        ]
    )

    panel.handle_action("move_slot", "1:+1")

    assert panel._lineup.roles == (FIRST, THIRD, SECOND), "移動根本沒有發生"
    assert panel._lineup.for_role(THIRD).parent == FIRST, (
        f"排個順序，他畫的線被改到別格身上了："
        f"{[(s.role, s.parent) for s in panel._lineup]}"
    )
    # 解出來的形狀也要同意：`THIRD` 排到第二格，而它接的還是 `FIRST`；
    # `SECOND` 沒有明著拉過線，所以它接前面那一格（也就是 `THIRD`）。
    assert panel._lineup.shape.upstream == ("", FIRST, THIRD), (
        f"線指對了，而框裡算出來的形狀不同意：{panel._lineup.shape.upstream}"
    )
