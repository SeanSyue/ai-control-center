r"""〔◀ 前移〕〔後移 ▶〕**搬的是整個 node**，一格都不留在原位。

老闆 2026-08-27 的原話：

> **我覺得箭頭要的東西是把整個 node 往後移或往前移，包含設定與 prompt**

## 在這之前它搬的是什麼

只有**廠牌、對話 id 與憑證**。名字、身分、職責、寫檔開關、線全部釘在位置上，
所以他那兩格（`node1` 是 Claude、`node2` 是 Codex）按下〔後移 ▶〕之後：

| | 他以為 | 真的發生 |
|---|---|---|
| 第一格 | node2（Codex）| **node1（Codex）**|
| 第二格 | node1（Claude）| **node2（Claude）**|

**卡片看起來沒有動**——名字還在原位，只有底下用哪個 AI 換過去了。

## 這一支守五件事

| | 守什麼 | 改壞的樣子 |
|---|---|---|
| 一 | **八個欄位一起走**，一欄一條斷言 | 某一欄又被釘在位置上，而卡片上看得到的只有名字——他不會發現身分留在原地 |
| 二 | **寫檔權限不會因為移動而無聲翻轉** | 他親手勾掉的「不編輯專案檔案」移完自己彈回來，下一次執行 AICC 動他的檔案 |
| 三 | **他畫的線移完還指著同一格** | 「上字幕接在發想後面」按一次箭頭變成「粗剪接在發想後面」，而那張圖看起來像他畫的 |
| 四 | **移動弄斷線的時候擋下來，指名是哪兩格，而且話真的到得了他面前** | 只在引擎裡丟例外＝一坨從頁面橋掉出去的 traceback；只 `return`＝按了沒反應，而那跟移好了長得一模一樣 |
| 五 | **硬碟上真的是新順序** | 畫面對、檔案舊，下一次打開這個專案就是舊的那一份，而他不會再按一次 |

## 一欄一條斷言，不准一條蓋八樣

`dataclasses.fields()` 掃一圈寫成一條斷言很省事，而它的失敗訊息只會說
「有一欄不對」。八條分開的代價是八行，換到的是**紅燈自己說出是哪一欄**——
這個專案為「一條檢查宣稱在守 N 件事、實際只守得到 N-1 件」付過學費
（`mutations_seat_name_and_duty.json` 的 P7，身分那一半從第一天起沒有守到
任何東西，而它每一次都亮綠燈）。

## 名字用他自己取的（`node1`／`node2`），不准換成 `author`

出廠名字會被 `AgentSlot.__post_init__` 照舊角色名回推職責，
`author` 剛好推回 `work`——**掉了跟沒掉長得一模一樣**。同一個理由寫在
`tests/test_a_seat_keeps_its_duty_through_every_button.py` 的檔頭。
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
    CREDENTIAL_API_KEY,
    CREDENTIAL_LOGIN,
    DUTY_APPROVE,
    DUTY_REVIEW,
    DUTY_WORK,
    VALID_DUTIES,
    VALID_PROVIDERS,
    WRITES_INHERIT,
    WRITES_OFF,
    AgentSlot,
    Lineup,
)
from app.i18n import t  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402

# 他自己取的名字。見檔頭。
HIS_FIRST = "node1"
HIS_SECOND = "node2"

# 第三條用的那三格（他剪片那份陣容的名字）。
IDEA = "發想"
CUT = "粗剪"
SUBS = "上字幕"


def _panel(tmp_path: Path, monkeypatch, lineup: Lineup | None = None) -> LineupWebPanel:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True, exist_ok=True)
    panel = LineupWebPanel(project)
    # 掃這台機器上所有本機對話會讓這一支變成非決定性的。
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    if lineup is not None:
        panel._lineup = lineup
        panel._persist()
    return panel


def _complaints(panel: LineupWebPanel) -> list[str]:
    said: list[str] = []
    panel.action_failed.connect(said.append)
    return said


def _on_disk(panel: LineupWebPanel) -> list[dict]:
    path = panel._project / ".aicc" / "sessions.json"
    return json.loads(path.read_text(encoding="utf-8"))["lineup"]


def _his_two_nodes() -> Lineup:
    """他那兩格，**八個欄位沒有一欄是一樣的**。

    一樣的那一欄，被釘在位置上跟跟著走長得一模一樣——這一份案例太順的話，
    上面那八條斷言全部是恆真的。
    """
    return Lineup.of(
        [
            AgentSlot(
                HIS_FIRST,
                "claude",
                session_id="chat-A",
                credential=CREDENTIAL_LOGIN,
                identity="你是熟悉境外開戶法規的資深顧問。",
                duty=DUTY_WORK,
                writes_files=WRITES_OFF,
            ),
            AgentSlot(
                HIS_SECOND,
                "codex",
                session_id="chat-B",
                credential=CREDENTIAL_API_KEY,
                identity="你是最後拍板的人。",
                duty=DUTY_APPROVE,
                writes_files=WRITES_INHERIT,
            ),
        ]
    )


# ---- 一、八個欄位一起走 ----------------------------------------------------


def test_one_press_carries_the_name(tmp_path, monkeypatch) -> None:
    """**這一條是老闆按下去第一眼會看的那件事。**

    改壞的樣子就是 2026-08-27 之前的樣子：名字釘在位置上，於是他按了
    〔後移 ▶〕，`node1` 還是排在第一格——卡片看起來沒有動。
    """
    panel = _panel(tmp_path, monkeypatch, _his_two_nodes())

    panel.handle_action("move_slot", "0:1")

    assert [s.role for s in panel._lineup] == [HIS_SECOND, HIS_FIRST], (
        "名字沒有跟著走——他按了排順序，卡片上的字一個都沒動"
    )


def test_one_press_carries_the_vendor(tmp_path, monkeypatch) -> None:
    panel = _panel(tmp_path, monkeypatch, _his_two_nodes())

    panel.handle_action("move_slot", "0:1")

    assert [s.provider for s in panel._lineup] == ["codex", "claude"]


def test_one_press_carries_the_conversation(tmp_path, monkeypatch) -> None:
    panel = _panel(tmp_path, monkeypatch, _his_two_nodes())

    panel.handle_action("move_slot", "0:1")

    assert [s.session_id for s in panel._lineup] == ["chat-B", "chat-A"]


def test_one_press_carries_the_credential(tmp_path, monkeypatch) -> None:
    """憑證是**帳單事後唯一解釋得了「這一次算在誰頭上」的那一欄**。

    它跟廠牌綁在一起，所以它跟著走這件事本來就成立——列一條，是因為
    「本來就成立」在下一個人重寫這一支的時候不會自己成立。
    """
    panel = _panel(tmp_path, monkeypatch, _his_two_nodes())

    panel.handle_action("move_slot", "0:1")

    assert [s.credential for s in panel._lineup] == [
        CREDENTIAL_API_KEY,
        CREDENTIAL_LOGIN,
    ]


def test_one_press_carries_the_identity(tmp_path, monkeypatch) -> None:
    """身分＝他打的那句「你是⋯」，也就是他說的 **prompt**。

    以前它釘在位置上，理由是「『你是很嚴格的審查員』屬於複查那一格」。
    他 2026-08-27 明說 prompt 要跟著 node 走，所以那條理由結束了。
    """
    panel = _panel(tmp_path, monkeypatch, _his_two_nodes())

    panel.handle_action("move_slot", "0:1")

    assert [s.identity for s in panel._lineup] == [
        "你是最後拍板的人。",
        "你是熟悉境外開戶法規的資深顧問。",
    ]


def test_one_press_carries_the_duty(tmp_path, monkeypatch) -> None:
    """職責是**引擎唯一看得懂的那一欄**（`steps/building.py` 靠它挑人）。

    釘在位置上的話，他排一次順序，做事的那一格換人做——而畫面上那張卡片
    的名字沒有動。
    """
    panel = _panel(tmp_path, monkeypatch, _his_two_nodes())

    panel.handle_action("move_slot", "0:1")

    assert [s.duty for s in panel._lineup] == [DUTY_APPROVE, DUTY_WORK]


def test_one_press_carries_the_write_switch(tmp_path, monkeypatch) -> None:
    panel = _panel(tmp_path, monkeypatch, _his_two_nodes())

    panel.handle_action("move_slot", "0:1")

    assert [s.writes_files for s in panel._lineup] == [WRITES_INHERIT, WRITES_OFF]


def test_one_press_carries_the_line(tmp_path, monkeypatch) -> None:
    """線是用**名字**拉的，而名字現在跟著走——所以這一欄也要跟著走。

    留在位置上的話，那條線會在按一次箭頭之後接到另一格身上（第三條測的
    就是這件事看起來的樣子）。
    """
    lineup = Lineup.of(
        [
            AgentSlot(IDEA, "codex", duty=DUTY_REVIEW),
            AgentSlot(CUT, "codex", duty=DUTY_REVIEW),
            AgentSlot(SUBS, "codex", duty=DUTY_APPROVE, parent=IDEA),
        ]
    )
    panel = _panel(tmp_path, monkeypatch, lineup)

    panel.handle_action("move_slot", "1:1")

    assert [(s.role, s.parent) for s in panel._lineup] == [
        (IDEA, ""),
        (SUBS, IDEA),
        (CUT, ""),
    ], "線沒有跟著它自己那一格走"


# ---- 二、寫檔權限不會因為移動而無聲翻轉 ------------------------------------


def test_moving_does_not_flip_the_write_switch_back_on(
    tmp_path, monkeypatch
) -> None:
    """**他親手關掉的「不編輯專案檔案」，移完還是關的。**

    2026-08-25 之前 `writes_files` 跟著人走而職責留在位置上，那個組合會
    **無聲地把權限打開**：做事那一格拿到旁邊那一格的空值，框自己彈回來，
    `may_write_files` 從 False 變成 True。他排順序，AICC 換回寫得了他檔案的
    設定，而畫面上沒有任何一個字說這件事。

    整個 node 一起搬之後那個坑消失了，**但理由換了**：不是因為開關被釘住，
    是因為權限是「職責＋開關」兩欄合起來算的，而兩欄鎖在同一格裡一起搬。
    這一條問的就是這個——**照權限問，不是照位置問。**
    """
    panel = _panel(tmp_path, monkeypatch, _his_two_nodes())
    before = {s.role: s.may_write_files for s in panel._lineup}
    assert before[HIS_FIRST] is False, "案例太順：他根本沒有關過那個開關"
    assert panel._lineup.for_role(HIS_FIRST).duty == DUTY_WORK, (
        "案例太順：這一格的職責不是執行，開關翻不翻都沒有差別"
    )

    panel.handle_action("move_slot", "0:1")

    after = {s.role: s.may_write_files for s in panel._lineup}
    assert after == before, (
        f"排一次順序，寫檔權限自己變了：{before} → {after}——"
        "而畫面上沒有任何一個字說這件事"
    )
    assert panel._lineup.for_role(HIS_FIRST).writes_files == WRITES_OFF, (
        "他親手勾掉的那個框自己彈回來了"
    )


def test_the_arrow_cannot_hand_a_seat_a_permission_it_did_not_have(
    tmp_path, monkeypatch
) -> None:
    """反方向：移動也不准**收走**權限。

    只守「不要多」的話，一個「移動一律把 `writes_files` 設成 off」的實作是
    綠的——而那個實作的症狀是他排一次順序，執行那一格再也不寫檔，那一輪跑
    完一個檔案都沒變。
    """
    lineup = Lineup.of(
        [
            AgentSlot(HIS_FIRST, "claude", duty=DUTY_WORK),
            AgentSlot(HIS_SECOND, "codex", duty=DUTY_APPROVE),
        ]
    )
    panel = _panel(tmp_path, monkeypatch, lineup)
    assert panel._lineup.for_role(HIS_FIRST).may_write_files is True, "前提壞了"

    panel.handle_action("move_slot", "0:1")

    assert panel._lineup.for_role(HIS_FIRST).may_write_files is True, (
        "排一次順序，執行那一格的寫入權被收走了——那一輪會跑完卻一個檔案都沒變"
    )


# ---- 三、他畫的線移完還指著同一格 ------------------------------------------


def test_the_line_he_drew_still_points_at_the_same_seat(
    tmp_path, monkeypatch
) -> None:
    """「上字幕接在發想後面」，移一次之後**還是接在發想後面**。

    以前名字留在位置上，所以線也必須留在位置上；線跟著人走的話，那條線會
    在按一次箭頭之後變成「粗剪接在發想後面」，而畫面上一個字都不說。

    現在兩樣都跟著 node 走，所以那條線指的還是同一格——**這一條問的是
    「指向誰」，不是「第幾格有一條線」**，後者兩種實作都是綠的。
    """
    lineup = Lineup.of(
        [
            AgentSlot(IDEA, "codex", duty=DUTY_REVIEW),
            AgentSlot(CUT, "codex", duty=DUTY_REVIEW),
            AgentSlot(SUBS, "codex", duty=DUTY_APPROVE, parent=IDEA),
        ]
    )
    panel = _panel(tmp_path, monkeypatch, lineup)
    said = _complaints(panel)

    panel.handle_action("move_slot", "1:1")

    assert not said, f"合法的移動被擋下來了：{said}"
    assert panel._lineup.roles == (IDEA, SUBS, CUT), "移動根本沒有發生"
    assert panel._lineup.for_role(SUBS).parent == IDEA, (
        f"排個順序，他畫的線被改到別格身上了："
        f"{[(s.role, s.parent) for s in panel._lineup]}"
    )
    # 解出來的形狀也要同意。`parent` 那一欄對、而 `shape` 讀成別的意思的話，
    # 畫面上那條線還是會接錯——這一頁已經為同一個形狀壞過兩次。
    assert panel._lineup.shape.upstream[1] == IDEA


# ---- 四、移動弄斷線的時候擋下來，指名是哪兩格，話真的到得了他面前 ----------


def test_a_move_that_would_break_a_line_is_refused_by_name(
    tmp_path, monkeypatch
) -> None:
    """**整個 node 一起搬之後才有的失敗模式。**

    線只准往前指（一格收不到還沒跑的那一格產出的東西）。把「上字幕」搬到
    它接的「粗剪」前面，那條線就反了。

    改壞的樣子有三種，而它們在畫面上長得一樣：

    * 自動幫他改線 → 他排一次順序，AICC 把他畫的分支接到另一格身上，
      而改完的那張圖看起來就像他畫的；
    * 擋下來但只在引擎裡丟例外 → 一坨從頁面橋掉出去的 traceback
      （`_move_slot` 2026-08-27 之前**沒有** `except LineupError`）；
    * 擋下來但安靜 `return` → 按了沒反應，而那跟移好了長得一模一樣。

    所以這一條走的是**整條路**：`handle_action` → `_move_slot` →
    `Lineup.moved` → `reject()`。
    """
    lineup = Lineup.of(
        [
            AgentSlot(IDEA, "codex", duty=DUTY_REVIEW),
            AgentSlot(CUT, "codex", duty=DUTY_REVIEW),
            AgentSlot(SUBS, "codex", duty=DUTY_APPROVE, parent=CUT),
        ]
    )
    panel = _panel(tmp_path, monkeypatch, lineup)
    said = _complaints(panel)

    panel.handle_action("move_slot", "2:-1")

    assert said, "移動把線弄斷了，而畫面上一個字都沒說"
    # **兩格的名字都要在**：一個是那條線的下游，一個是它接著的上游。
    # 他有九格可以猜，「形狀不合法」等於沒講。
    assert SUBS in said[0] and CUT in said[0], (
        f"訊息沒有同時指出是哪兩格：{said[0]!r}"
    )
    # **比整句，不只比名字。**只比名字的話，任何一句提到這兩格的話都會過，
    # 包含一句講別件事的（`lineup.parent_runs_later` 就同時有這兩個名字）。
    assert said[0] == t("lineup.move_would_break_a_link", role=SUBS, parent=CUT), (
        f"擋對了，但收到的是另一句話：{said[0]!r}"
    )
    assert panel._lineup.roles == (IDEA, CUT, SUBS), "被拒絕了卻移到了"


def test_a_refused_move_does_not_reach_the_disk(tmp_path, monkeypatch) -> None:
    """被擋下來的那一次，**硬碟上也不准動**。

    畫面上退回去而檔案已經寫了新順序的話，他關掉頁面再打開，看到的是那個
    被拒絕過的順序——而沒有人跟他說過為什麼。
    """
    lineup = Lineup.of(
        [
            AgentSlot(IDEA, "codex", duty=DUTY_REVIEW),
            AgentSlot(CUT, "codex", duty=DUTY_REVIEW),
            AgentSlot(SUBS, "codex", duty=DUTY_APPROVE, parent=CUT),
        ]
    )
    panel = _panel(tmp_path, monkeypatch, lineup)

    panel.handle_action("move_slot", "2:-1")

    assert [row.get("role") for row in _on_disk(panel)] == [IDEA, CUT, SUBS]


# ---- 五、硬碟上真的是新順序 ------------------------------------------------


def test_the_new_order_reaches_the_disk(tmp_path, monkeypatch) -> None:
    """**畫面對不算數，`.aicc/sessions.json` 也要是新順序。**

    只有畫面對的話，下一次打開這個專案就是舊的那一份——而他不會再按一次
    那顆箭頭，他會以為自己記錯了。

    整份寫回去，不只看名字：憑證與身分是這一輪才第一次跟著走的兩欄，而
    「畫面上搬了、寫回去的是舊值」在這兩欄上完全看不出來。
    """
    panel = _panel(tmp_path, monkeypatch, _his_two_nodes())

    panel.handle_action("move_slot", "0:1")

    rows = _on_disk(panel)
    assert [row.get("role") for row in rows] == [HIS_SECOND, HIS_FIRST]
    assert [row.get("provider") for row in rows] == ["codex", "claude"]
    assert [row.get("session_id") for row in rows] == ["chat-B", "chat-A"]
    assert [row.get("credential") for row in rows] == [
        CREDENTIAL_API_KEY,
        CREDENTIAL_LOGIN,
    ]
    assert [row.get("identity") for row in rows] == [
        "你是最後拍板的人。",
        "你是熟悉境外開戶法規的資深顧問。",
    ]
    assert [row.get("duty") for row in rows] == [DUTY_APPROVE, DUTY_WORK]
    assert [row.get("writes_files") for row in rows] == [WRITES_INHERIT, WRITES_OFF]


# ---- 反方向：修法不可以變成「一律不准」 ------------------------------------


def test_his_two_shipped_seats_can_finally_move(tmp_path, monkeypatch) -> None:
    """**出廠那兩格移得動了**——2026-08-27 之前它們一格都移不動。

    以前職責留在位置上而廠牌跟著人走，所以「執行＝Claude、複查＝Codex」
    這個出廠形狀一移動就會把 Codex 放上會寫檔案的位子，於是被擋。
    他第一次按這顆箭頭很可能就被擋，而他只是在排順序。

    這一條是那件事修好了的證據，也是「不准改成一律不准」的守衛。
    """
    panel = _panel(tmp_path, monkeypatch)
    assert [s.provider for s in panel._lineup] == ["claude", "codex"], "前提壞了"
    assert [s.duty for s in panel._lineup] == [DUTY_WORK, DUTY_REVIEW], "前提壞了"
    said = _complaints(panel)

    panel.handle_action("move_slot", "0:1")

    assert not said, f"出廠陣容的箭頭又被擋下來了：{said}"
    assert [s.role for s in panel._lineup] == ["reviewer", "author"]


# ---- 箭頭搬的是整個 node，由不變量守著 ------------------------------------
#
# `_move_slot` 2026-08-26 起有一條「這兩格對調之後會不會把 Codex 放上執行那
# 一格」的拒絕。整個 node 一起搬之後那條路**永遠走不到**，所以那道閘門在
# 2026-08-27 拿掉了——而「修好之後走不到的分支，它的破壞會永遠是綠的」。
#
# （2026-09-01 補記：那條規則本身也拿掉了——`vendor_cannot_write()` 整支
# 不存在了。底下這一條守的東西沒有變：**移動不准拆開任何一格。**）


def _seat(name: str, provider: str, duty: str) -> AgentSlot:
    return AgentSlot(name, provider, duty=duty)


def test_an_arrow_cannot_invent_a_pairing_that_was_not_there(
    tmp_path, monkeypatch
) -> None:
    """**`(廠牌, 職責)` 是移動的不變量。**

    枚舉兩格陣容的每一種廠牌／職責組合（`2 × 4` 的每一對）、每一個方向各按
    一次箭頭，比對移動前後 `(廠牌, 職責)` 的多重集合。一個都不准變——變了
    就代表某一欄又被留在位置上，而那正是「Codex ＋ 執行」湊得出來的那個
    形狀。

    刻意**連本來就違規的那幾份也一起枚舉**（手改過的 `sessions.json` 排得出
    來）：這一條問的是「移動有沒有造出新的違規」，不是「這份陣容合不合法」。
    後者是別人的工作，而在這裡問它會變成「檔案裡本來就有的一格違規讓他連
    別處都移不動」。
    """
    panel = _panel(tmp_path, monkeypatch)
    said = _complaints(panel)
    combos = [(p, d) for p in VALID_PROVIDERS for d in VALID_DUTIES]
    checked = 0

    for first in combos:
        for second in combos:
            for at, direction in ((0, 1), (1, -1)):
                before = Lineup.of(
                    [_seat(HIS_FIRST, *first), _seat(HIS_SECOND, *second)]
                )
                panel._lineup = before
                said.clear()

                panel.handle_action("move_slot", f"{at}:{direction:+d}")

                after = panel._lineup
                assert not said, (
                    f"{first} / {second} 這一份被擋下來了：{said}——"
                    "沒有線的陣容一律移得動"
                )
                assert [s.role for s in after] == [HIS_SECOND, HIS_FIRST], (
                    f"{first} / {second} 根本沒有移動，這一輪什麼都沒測到"
                )
                assert sorted((s.provider, s.duty) for s in after) == sorted(
                    (s.provider, s.duty) for s in before
                ), (
                    f"按一次箭頭，(廠牌, 職責) 的組合變了："
                    f"{[(s.provider, s.duty) for s in before]} → "
                    f"{[(s.provider, s.duty) for s in after]}——"
                    "某一欄被留在位置上了，而「Codex ＋ 執行」就是這樣湊出來的"
                )
                # ⛔ 這裡本來還有一條「移動不准生出新的違規者」，用
                # `vendor_cannot_write()` 問。那一支 2026-09-01 整個拿掉了
                # （老闆：「哪個智障說只有 claude 可以寫的」——查完他是對的，
                # 那條規則沒有人問過他）。兩家都寫得了檔之後它永遠回 False，
                # 留著就是一條恆真的斷言。
                #
                # 上面那條「`(廠牌, 職責)` 的多重集合不變」**沒有過期**：
                # 它守的是「箭頭搬的是整個 node」，跟誰寫得了檔無關。
                checked += 1

    assert checked == len(combos) * len(combos) * 2, (
        f"只走了 {checked} 種組合——枚舉迴圈自己壞了，這一條會變成恆綠的"
    )


def test_the_pairing_stays_put_on_a_three_seat_line_up(
    tmp_path, monkeypatch
) -> None:
    """同一件事，三格。

    兩格的話「往後移」與「往前移」是同一次對調，測不到「中間那一格移動會不
    會動到旁邊沒被碰到的那一格」。
    """
    panel = _panel(tmp_path, monkeypatch)
    said = _complaints(panel)
    combos = [("claude", DUTY_WORK), ("codex", DUTY_REVIEW), ("codex", DUTY_WORK)]
    names = (IDEA, CUT, SUBS)
    checked = 0

    for a in combos:
        for b in combos:
            for c in combos:
                for at, direction in ((0, 1), (1, 1), (1, -1), (2, -1)):
                    before = Lineup.of(
                        [
                            _seat(names[0], *a),
                            _seat(names[1], *b),
                            _seat(names[2], *c),
                        ]
                    )
                    panel._lineup = before
                    said.clear()

                    panel.handle_action("move_slot", f"{at}:{direction:+d}")

                    after = panel._lineup
                    assert not said, f"{a}/{b}/{c} 被擋下來了：{said}"
                    assert {s.role for s in after} == set(names)
                    assert [s.role for s in after] != [s.role for s in before], (
                        f"{a}/{b}/{c} 在 {at}:{direction} 沒有移動"
                    )
                    assert sorted((s.provider, s.duty) for s in after) == sorted(
                        (s.provider, s.duty) for s in before
                    )
                    checked += 1

    assert checked == len(combos) ** 3 * 4, f"只走了 {checked} 種組合"
