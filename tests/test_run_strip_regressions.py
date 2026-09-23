"""三個修好了但會安靜回來的外框問題（ROADMAP P11 收尾）。

這三條原本掛在 ROADMAP 的「還在設計端」清單上。2026-08-10 對照程式碼查證，
三條都已經修好了——**而那份清單還列著它們**。

一份過期的「還沒做」清單不是沒有用，是有害的：照著它做的人會去重做一件已經
做完的事，或者去追一個已經不存在的 bug。所以這個檔案做兩件事：把清單改掉，
並且讓這三件事回來的時候會有東西變紅。

三條都是「壞掉的時候不會報錯，只會變得難用或看不出來」的那一種，
自動測試看不到、實機才會發現——那正是最需要被釘住的一種。
"""
from __future__ import annotations

import re
from pathlib import Path

SHELL_JS = Path("app/ui/web/shell.js")
AICC_CSS = Path("app/ui/web/aicc.css")


def _js() -> str:
    return SHELL_JS.read_text(encoding="utf-8")


def _css() -> str:
    return AICC_CSS.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """註解先剝掉。

    這一頁上已經有兩次斷言是被解釋 bug 的註解自己撐住的，兩次都是負向驗證
    抓到的。提到某個東西不等於做了某個東西。
    """
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))


def test_the_run_strip_does_not_rebuild_itself_every_second() -> None:
    """執行中每秒推一次 state。整條 `innerHTML` 換掉的話，使用者打開的 ⋯ 選單
    會在一秒內自己關起來——展開中的 select、文字選取也一起被打斷。

    所以只有「形狀」變了才重建，秒數這種只動那一個文字節點。
    """
    code = _strip_comments(_js())

    # 「函式還在」不等於「有人呼叫它」。第一版只斷言 `shapeKey` 這個字出現過，
    # 而負向驗證把 `const key = shapeKey(d)` 換成 `Math.random()` 之後照樣全綠
    # ——定義還在，比對已經死了。所以這裡驗的是**那個值從哪裡來**。
    assert re.search(r"key\s*=\s*shapeKey\s*\(", code), (
        "key 不是從 shapeKey() 來的，形狀比對等於沒作用"
    )
    # 早退那一條才是重點：算了 key 卻照樣往下重建，等於沒有比對。
    assert re.search(r"if\s*\(\s*key\s*===\s*lastKey\s*\)", code), (
        "少了『形狀沒變就早退』那一條"
    )
    assert "textContent" in code, "秒數不是用文字節點更新的，那就是在重建"


def test_a_rebuild_puts_the_open_menu_back() -> None:
    """形狀真的變了（例如專案名稱改了）也可能發生在選單開著的時候。

    驗的是**順序**，不是某個變數名字。第一版斷言 `"wasOpen" in code`，
    而把宣告改名之後底下的 `if (wasOpen)` 仍然含有那個字串——測試綠著，
    實際上是一個 ReferenceError。
    """
    code = _strip_comments(_js())
    rebuild = code.index("top.innerHTML =")
    before, after = code[:rebuild], code[rebuild:]

    captured = re.search(
        r"(?:const|let|var)\s+(\w+)\s*=\s*!!\s*top\.querySelector\(\s*'#shellMore\.open'",
        before,
    )
    assert captured, "重建之前沒有先記下選單是不是開著的"

    # 把兩處綁在同一個名字上，而不是各自找一個字串。這一版之前，把宣告改名
    # 就能讓測試繼續綠——底下的 `if (wasOpen)` 仍然含有那個字串，而實際上
    # 那已經是一個 ReferenceError。
    name = captured.group(1)
    assert re.search(
        rf"if\s*\(\s*{re.escape(name)}\s*\)[^\n]*classList\.add\(\s*'open'\s*\)",
        after,
    ), f"重建之後沒有用 {name} 把選單打開回去"


def test_finished_does_not_look_the_same_as_never_run() -> None:
    """「跑完和沒跑的圓點一樣是灰的」。

    這是這個專案反覆出現的那一條：**沒有顯示跟什麼都沒發生不可以長得一樣。**
    圓點的預設色是 `--text-3`（灰），所以任何沒有自己顏色的狀態都會退回
    「看起來像沒跑過」。
    """
    css = _css()
    dot = r"\.shell-exec \.status\.%s \.k\{[^}]*background:var\(--([a-z0-9-]+)\)"

    colours = {}
    for state in ("running", "gated", "error", "done", "partial"):
        found = re.search(dot % state, css)
        assert found, f"{state} 的圓點沒有自己的顏色，會退回灰色"
        colours[state] = found.group(1)

    assert colours["done"] != "text-3", "「完成」還是灰的"
    # 「跑完了但有狀況」不是完成：薄荷色的「完成」旁邊寫著「Codex 未完成審查」
    # 會自相矛盾，所以這兩個不可以同色。
    assert colours["done"] != colours["partial"], colours


def test_every_run_state_the_strip_can_show_has_a_label() -> None:
    # 反方向：顏色齊了但少一個文字，那一格會是空的。
    code = _strip_comments(_js())
    labels = re.search(r"RUN_LABEL\s*=\s*\{(.*?)\}", code, re.S)
    assert labels, "RUN_LABEL 不見了"

    keys = set(re.findall(r"(\w+)\s*:", labels.group(1)))
    assert {"idle", "waiting", "running", "gated", "error", "done", "partial"} <= keys


def test_a_bare_select_is_not_a_white_box_on_a_dark_bar() -> None:
    """`appearance:none` 會把原生外觀整個拿掉。

    只寫這一行、不補底色字色的話，任何沒被特別上樣式的 select 都會變成深色列
    上的白框——敏感度那個下拉就是這樣。所以全域 select 必須自帶完整外觀。
    """
    css = _css()
    rule = re.search(r"(?<![\w.\-])select\{([^}]*)\}", css)

    assert rule, "全域 select 規則不見了"
    body = rule.group(1)
    assert "appearance:none" in body, body
    assert "background:" in body, "拿掉了原生外觀卻沒有給底色"
    assert "color:" in body, "拿掉了原生外觀卻沒有給字色"
    # 下拉展開的那一層是另一個元素，父層的顏色管不到它。
    assert re.search(r"select option\{[^}]*background:", css), "展開的選項沒有底色"


# 本來還有一條「ROADMAP 不可以再把這三件列成待辦」。**拿掉了，因為它沒有牙齒。**
# 它比對的是散文（找「還在設計端」這幾個字），而我在同一次改動裡把那段標題
# 改寫成「交給設計端的三條」——於是那個字串本來就不在，斷言恆真。負向驗證把
# 結論句改掉之後照樣全綠。
#
# 修不修得好不是重點：一條靠文案措辭成立的檢查，下一次有人潤稿就會再度失效，
# 而它看起來還是綠的。**沒有牙齒的測試比沒有測試更糟**，所以這裡選擇不留。
# 上面五條驗的是程式碼本身，那才是「這三件有沒有回來」的真正答案。
