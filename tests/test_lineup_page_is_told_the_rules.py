"""陣容頁的三個「可不可以」由引擎給，頁面不從位置推（ROADMAP P10 畫面缺口）。

ROADMAP 底下列著一條：三格 + 計畫序列時，頁面的 `dead = i >= executed_slots`
會把規劃者畫成會執行、審查者畫成不會被執行——**剛好兩個都相反**。照著查下去，
同一個根因在這一頁上長出三顆：

* **誰這次會跑** —— 用座位序號推。規劃者坐第一位而計畫序列不派工給它，
  所以位置與職責在這個組合下第一次分家，而示範資料是兩格開發序列，
  兩種推法給出一樣的答案——這個 bug 就是這樣活下來的。
* **能不能加一格** —— 用 `slots.length >= executed_slots` 推。開發序列裡
  每一格都有職責，所以那個不等式**恆真**：「＋ 加一格」永遠是關的，
  Stage B 做出來的第三格從這一頁按不到。
* **能不能移除** —— 用 `slots.length > 2` 推。後端只肯拿掉選配的那一格，
  於是三格時三顆「移除」都亮著，其中兩顆按下去 Python 直接 return。
  **一顆按了什麼都不會發生的按鈕，比一顆明著停用的更糟**：使用者會以為
  自己按錯地方，或以為程式壞了。

三顆的形狀一樣：**頁面從一個手邊有的數字去推引擎的規則**，而那個數字在
今天的預設值下剛好給出對的答案。所以修法不是把推論算式改對，是**讓引擎把
答案直接講出來**，頁面只負責畫。

這個檔案守 Python 這一半：那三件事有被送出去，而且送出去的值跟後端**真的
會做的事**一致。畫出來對不對是 DOM 層的事，守在
`tools/verify_web_pages.py` 的 `LINEUP_SEQ_PROBE`（三格＋計畫序列，
render() 真的畫一次）——那支不是 pytest，因為 QtWebEngine 的殘留 view
會讓整套測試以 139 離開。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.orchestrator import Orchestrator  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402

PAGE = Path("app/ui/web/lineup_v1.html")


def _panel(tmp_path: Path, monkeypatch) -> LineupWebPanel:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    panel = LineupWebPanel(project)
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    return panel


# --- 加一格：上限是引擎的，不是這次序列的 -----------------------------------


def test_the_page_is_given_the_engine_ceiling(tmp_path, monkeypatch) -> None:
    panel = _panel(tmp_path, monkeypatch)

    lineup = panel.collect_state()["lineup"]

    # 0 = 沒有上限。2026-08-22 之前這裡是 3，而那三個名字是引擎內部的常數，
    # 不是產品的規格——使用者要排五個節點就排不出來。
    assert lineup["max_slots"] == 0


def test_there_is_no_ceiling_and_the_page_is_told_so(
    tmp_path, monkeypatch
) -> None:
    """**這一組反過來了。**

    原本這裡有三條測試在守「上限」：上限不可以等於這次序列的派工數、上限
    要等於後端真的擋得住的數字、按到底會停在畫面寫的那個數字。它們守得很
    好——守的是一個不該存在的東西。

    使用者的原話：「AICC 最多只能放 3 格這她媽在放屁⋯⋯我今天講了這麼多次
    要模組化」。而【資料結構現在就做成 node 圖】那條定案的不變式寫著兩角色
    版必須是 node 圖的一個特例，**不得另立一套結構**——一份寫死的三人清單
    就是另立的那一套。

    留下來的性質只有一個，而且它才是那三條真正在守的東西：
    **畫面說得動的，後端就真的做得到。**所以這裡不比對數字，直接按下去。
    """
    panel = _panel(tmp_path, monkeypatch)

    assert panel.collect_state()["lineup"]["max_slots"] == 0, "0 = 沒有上限"

    for _ in range(6):
        panel.handle_action("add_slot", "")

    # 按六次就真的多六格。以前按第二次之後就沒反應了。
    assert len(panel._lineup) == 8
    assert len(panel.collect_state()["lineup"]["slots"]) == 8


def test_the_page_and_the_backend_agree_about_what_can_be_dropped(
    tmp_path, monkeypatch
) -> None:
    """畫面說得刪，後端就要真的刪得掉。

    兩邊各算一次的話，不一致會**兩個方向都沉默**：一顆按了沒反應的移除鈕，
    或者一顆被停用、但引擎其實會收的。

    規則現在只剩一條：**不准刪到零格。**以前是「只有選配的規劃者能刪，
    而且至少留兩格」，而那條下限擋掉了【出廠三張】明著給的一人表格。
    """
    panel = _panel(tmp_path, monkeypatch)

    two = panel.collect_state()["lineup"]["slots"]
    assert [s["can_remove"] for s in two] == [True, True]

    # 說得刪就真的刪得掉。
    panel.handle_action("remove_slot", "0")
    assert len(panel._lineup) == 1

    # 剩一格：畫面說不能刪，後端也真的不刪。
    one = panel.collect_state()["lineup"]["slots"]
    assert [s["can_remove"] for s in one] == [False]
    panel.handle_action("remove_slot", "0")
    assert len(panel._lineup) == 1





def test_a_seat_that_says_it_cannot_be_dropped_really_cannot(
    tmp_path, monkeypatch
) -> None:
    """旗標不是裝飾——把它送出去而後端行為不同，就是換一個地方說謊。"""
    panel = _panel(tmp_path, monkeypatch)
    panel.handle_action("add_slot", "")
    before = panel._lineup.roles

    for index, slot in enumerate(panel.collect_state()["lineup"]["slots"]):
        if slot["can_remove"]:
            continue
        panel.handle_action("remove_slot", str(index))
        assert panel._lineup.roles == before, (
            f"第 {index} 格說不能移除，後端卻拿掉了它"
        )



def _page_code() -> str:
    """頁面原始碼，去掉註解。"""
    source = PAGE.read_text(encoding="utf-8")
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))


# 找的是**讀取那個動作**（`lu.max_slots`、`s.can_remove`），不是欄位名。
#
# 第一版找的是欄位名，而負向驗證直接判它沒有牙齒：把讀取那一行改回自己推，
# 兩條都還是綠的。原因是這一頁自己帶了一份示範資料，而那份資料裡就寫著
# `max_slots:3` 與 `can_remove:false`——**它是程式碼不是註解，所以剝掉註解
# 也剝不掉它**。等於用「這個欄位有沒有被提到」去回答「這個欄位有沒有被讀」。
#
# 這跟這一整組 bug 是同一個形狀：一個看起來很合理的訊號，剛好在今天的資料下
# 給出對的答案。
# `lu.max_slots` 2026-08-25 從這張表上**移下去**了，理由不是它不重要，
# 是它換了一種守法：這一頁現在**一個上限都不算**（見底下那一條）。
# 一條要求頁面去讀某個欄位的檢查，會逼下一個人把那段算上限的程式碼接回來。
READS = [
    ("s.can_remove", "「移除」又改回用剩幾格推了"),
    # 2026-08-27 加。這一顆跟上面那一顆是同一個形狀：**畫面不准自己決定
    # 「插得進去嗎」**。無條件畫那顆 ⊕ 的話，它在 Python 收不下位置的那一天
    # 會加在最後面而寫著「在這裡插一格」——頁面自己的註解說得最清楚：
    # 「所以這裡不畫一顆說謊的按鈕」。
    ("lu.supports_insert_at", "那顆 ⊕ 又改回不問 Python 就自己畫了"),
]


@pytest.mark.parametrize("expression,why", READS)
def test_the_page_reads_what_python_sends(expression: str, why: str) -> None:
    assert expression in _page_code(), (
        f"頁面的程式碼沒有讀 `{expression}`——{why}。"
        "送得出去、沒有人讀，畫面就會繼續用它自己推出來的答案。"
    )


def test_the_page_does_not_work_out_a_seat_ceiling_at_all() -> None:
    """這一頁**一個格數上限都不算**。

    ## 這一條 2026-08-25 換過形狀，而換的理由值得留著

    它本來叫 `test_no_limit_does_not_render_as_a_ceiling_of_zero`，守的是
    `lu.max_slots === 0` 那一行有沒有在——也就是「算上限，但要記得 0 是
    沒有上限」。

    那個形狀來自一個真的 bug：後端把三格上限改成 0，而
    `lu.max_slots ?? ...` 只在 null/undefined 時退回，0 原樣穿過去，
    於是 `slots.length >= 0` 恆真——**加號永遠是關的，按鈕上還寫著
    「最多 0 格」**。比灰掉更糟：它明著講一句假話，還把剛做出來的功能鎖死。

    頁面設計 2026-08-25 把整段算上限的程式碼拿掉了，走的是老闆
    2026-08-22 的原話「**不要顯示數字，加號永遠可按就好**」，
    以及【陣容是一個框，不是一排卡片】那條定案裡的
    「格數不設畫面上限——上限是引擎的事實，畫面不准發明一個」。

    **那比原本的守法強**：沒有那段程式碼，就沒有那段程式碼可以寫錯。
    所以這一條跟著改成守新的形狀，不是刪掉。

    ## 為什麼要有底下那句「按鈕還在」

    只寫「不准出現 `lu.max_slots`」的話，一張空白的頁面是綠的。
    兩句要一起在：一句擋它把上限算回來，一句擋它連按鈕都不見。
    """
    code = _page_code()
    raw = PAGE.read_text(encoding="utf-8")

    assert "lu.max_slots" not in code, (
        "這一頁又開始算格數上限了。引擎送的 0 是「沒有上限」，"
        "而上一次算它的時候，加號永遠是關的、上面寫著「最多 0 格」。"
        "上限是引擎的事實，畫面不准發明一個。"
    )
    # 掃的是**剝掉註解之後**的程式碼：那三句「最多 N 格」現在活在註解裡，
    # 而註解正是在解釋「為什麼不要那樣寫」——把它判成違規等於教下一個人
    # 把這條檢查當成狼來了。（同一個判準 2026-08-24 在說明文字那一支付過學費。）
    # ⛔ 2026-09-01 之前這裡掃的是光溜溜的「最多」兩個字，而它 **2026-09-01
    # 誤報了一次**：AI 執行設定搬進這一頁之後，示範資料裡有引擎組好的那一行
    # 「⋯最多自動 FIX 1 次；最壞情況約 6 次模型呼叫⋯」——跟座位上限毫無關係。
    #
    # 收窄成它真正在擋的那一句：**「最多 ⋯ 格」**。
    # 一條會誤報的檢查比沒有更貴：它會訓練人忽略紅燈，而下一個人處理它的
    # 方式通常是把它整條刪掉。
    seat_cap = re.search("最多[^\\n。，；]{0,12}格", code)
    assert seat_cap is None, (
        "「最多 N 格」那句話回來了——它 2026-08-22 起講的一直是假話"
        f"（比到的是「{seat_cap.group(0) if seat_cap else ''}」）"
    )
    # 另一半：按鈕要還在，而且**不帶條件**。
    # 找的是那一行本身沒有樣板插值（`${`）——加上條件的話它會長回
    # `${full?' off':''}` 那種形狀，而那正是上一次把功能鎖死的寫法。
    # 2026-08-27 換錨：`data-add` 現在有**第二個用途**——每道縫那顆 ⊕
    # （`data-add="${at}"`，插在第幾格前面）。舊錨 `data-add="1"` 從此
    # 既比不到這一顆、又會比到那一種**本來就該帶條件**的。
    # 改用 `id="addLast"` ——它指名的就是「加在最後」那一顆，而這條檢查
    # 從第一天起守的就是它。
    button = [
        line for line in raw.splitlines()
        if 'id="addLast"' in line and "closest(" not in line
    ]
    assert button, "「＋ 加一格」那顆按鈕不見了"
    conditioned = [line.strip() for line in button if "${" in line]
    assert not conditioned, (
        "「＋ 加一格」又被加上條件了："
        + "、".join(conditioned)
        + "。老闆 2026-08-22 的原話是「加號永遠可按就好」。"
    )

    # **「還在 HTML 裡」不等於「按得到」。**
    #
    # 2026-08-27 那顆按鈕標籤原封不動留在原地，而頁面在 render 尾巴寫了
    # `addLast.hidden = canInsert` —— ⊕ 一出現它就自己消失。上面那兩條
    # 全綠，而使用者看不到它。
    #
    # 藏掉的代價不是少一個入口：縫裡那一排 ⊕ 長在 `.line` 裡面，那一區
    # **左右可捲**。所以「加在最後」這件最常用的事回到了會捲出畫面的地方，
    # 正是 `ROADMAP.md:2984` 記著的那次實測事故——
    # 「使用者按了加一格，然後他剛按的那顆鈕不見了」——換一顆按鈕重演。
    #
    # **掃 `code` 不掃 `raw`**：`code` 是去掉註解的那一份。用 `raw` 的話，
    # 上面那段解釋「2026-08-27 之前這裡寫的是 `addLast.hidden = canInsert`」
    # 的註解自己就會被判成違規——2026-08-27 真的紅了一次，而**同一個判準
    # 這一支檔案上面幾行才剛付過學費**（`assert "最多" not in code`）。
    hides = [
        line.strip() for line in code.splitlines()
        if "addLast" in line and ".hidden" in line and "= false" not in line
    ]
    assert not hides, (
        "「＋ 加一格」被藏起來了："
        + "、".join(hides)
        + chr(10)
        + "老闆 2026-08-22 的原話是「加號**永遠**可按就好」，"
        "而縫裡那一排 ⊕ 在可捲的區域裡——藏掉右上角那顆，"
        "格數一多「加在最後」就又捲出畫面了。"
    )
