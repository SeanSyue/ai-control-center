"""憑證入口那五種列，**是用結構分出來的，不是用那句中文**。

規格（交件給 PM 2026-08-25-C §三／§四）要頁面畫五種列，而引擎那一側的
`Check` 只給 `level`（三值）跟 `detail`（一句中文）。

分辨的方式**不可以**是比對 `detail`。那是 2026-08-24 一天之內在這個 repo 抓到
五處的那個病（「不准拿中文當查詢的鍵」）：

> 症狀是英文版上線那天比對永遠不成立，**而畫面看起來完全正常**。

「看起來完全正常」是這一整份檔案的重點。比對失敗不會丟例外、不會留下紀錄，
它只會讓每一列都落到同一種狀態——點是有顏色的、字是有的，只是每一顆都指錯。
沒有任何東西會紅，除非有東西專門守著它。這裡就是那個東西。

推導住在 `app/services/setup_state.py`，這份檔案守它三件事：

1. 五種列**各自分得出來**（少一種，那一種的使用者會拿到別人的指示）
2. **沒有一條規則讀得懂中文**（語法樹掃的，不是靠人記得）
3. 合不上的形狀**當場炸**，不安靜落成某一種（【有預設值的分類等於沒有分類】）
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from app.services import cli_discovery, credential_entry
from app.services import startup_diagnostics as sd
from app.services.setup_state import CannotDrawThisCheck, SetupState, state_of

# 互斥那一條要數「幾條規則合得上」，而那個數字沒有公開的出口。**故意伸手進去
# 拿私有的那一份**：換成用 `state_of()` 反推的話，兩條規則同時成立跟零條同時
# 成立會被同一個例外洗成同一件事，而這條測試要分的正好是那兩件。
from app.services.setup_state import _ROWS

VERSION = "claude 2.1.226"

MODULE = Path("app/services/setup_state.py")
ENGINE = Path("app/services/startup_diagnostics.py")


# --- 把五種機器造出來（這台開發機兩支都裝好也登入了，跑不到其中四種）-------


def _ready(monkeypatch) -> sd.Check:
    """裝好了、跑得起來、登入了。"""
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\bin\claude.cmd")
    monkeypatch.setattr(sd, "_tool_version", lambda executable: VERSION)
    monkeypatch.setattr(sd, "_signed_in", lambda executable: True)
    return sd.check_claude()


def _signed_out(monkeypatch) -> sd.Check:
    """裝好了、跑得起來、**沒登入**。"""
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\bin\claude.cmd")
    monkeypatch.setattr(sd, "_tool_version", lambda executable: VERSION)
    monkeypatch.setattr(sd, "_signed_in", lambda executable: False)
    return sd.check_claude()


def _nowhere_to_be_found(monkeypatch) -> sd.Check:
    """PATH 上沒有，四層猜測也全部落空。"""
    monkeypatch.setattr(sd.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_discovery, "discover", lambda provider, chosen=None: [])
    return sd.check_claude()


def _will_not_run(monkeypatch) -> sd.Check:
    """PATH 上有，但 `--version` 跑不出東西（被卸載的 npm 留下的殘骸）。"""
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\bin\claude.cmd")
    monkeypatch.setattr(sd, "_tool_version", lambda executable: "")
    monkeypatch.setattr(sd, "_signed_in", lambda executable: True)
    return sd.check_claude()


def _cannot_tell_if_signed_in() -> sd.Check:
    """登入狀態問不出來的那一列。

    **這一種是手工造的，不是從 `check_claude()` 出來的**，而那是刻意的：
    這幾條守的是**規則本身**（給定這個形狀，`state_of()` 分不分得出來），
    不是「引擎那一側有沒有把欄位立起來」。

    引擎那一側 2026-08-26 接上了（`_cli_check()` 裡 `signed is None` 那一條），
    釘住那一段接線的是
    `test_a_login_state_we_cannot_read_is_not_drawn_as_signed_in.py::
    test_the_credentials_page_does_not_draw_that_row_as_ready`——**那一條是
    走真的 `check_claude()`**。兩邊分開守，是因為它們會分開壞：欄位被拿掉的
    那天規則照樣對，規則被寫寬的那天欄位照樣立著。
    """
    return sd.Check(
        "claude",
        "Claude Code",
        sd.Level.OK,
        f"{VERSION}（登入狀態不明）",
        sign_in_unknown=True,
    )


# --- 五種列各自分得出來 ------------------------------------------------------


def test_a_cli_that_is_ready_is_the_only_one_drawn_as_ready(monkeypatch) -> None:
    """改壞的樣子：`ok` 那條規則被寫寬了，於是別種列也拿到薄荷點。

    使用者會看到：一顆薄荷點，意思是「可以按開始執行了」。他按下去，
    設定全部付完，然後每一次執行都死在中間。
    """
    assert state_of(_ready(monkeypatch)) is SetupState.OK


def test_a_signed_out_cli_is_not_drawn_as_broken(monkeypatch) -> None:
    """改壞的樣子：判準退化成「BLOCKED 就是壞了」，`action` 那個欄位沒人看。

    使用者會看到：玫瑰點加一句「找得到但跑不起來」，然後 〔怎麼辦〕 展開的是
    **重裝說明**。他重裝一遍，那一列一個字都沒變——因為他要做的事一直是登入。
    """
    check = _signed_out(monkeypatch)

    assert check.action, "前提檢查：這台假機器沒有造出「沒登入」那一種"
    assert state_of(check) is SetupState.SIGNED_OUT


def test_a_cli_nobody_can_find_keeps_its_way_out(monkeypatch) -> None:
    """改壞的樣子：`missing` 那條規則不見了，它落進 `not_runnable`。

    使用者會看到：一列玫瑰點寫著「跑不起來」，而那台機器上根本沒有這支 CLI。
    `missing` 那一列底下的三段（安裝指令／掃描這台機器／選擇位置…）
    ——也就是**唯一的出口**——整組不見。
    """
    check = _nowhere_to_be_found(monkeypatch)

    assert check.pick_provider, "前提檢查：這台假機器沒有造出「找不到」那一種"
    assert state_of(check) is SetupState.MISSING


def test_a_cli_that_will_not_run_lands_on_the_last_row(monkeypatch) -> None:
    """改壞的樣子：最後那條規則被寫成 `else`，什麼都往這裡掉。

    使用者會看到：真的壞掉的那一列跟其他每一列長得一樣，於是「重裝」這個指示
    出現在不需要重裝的地方；而下一次它真的該出現時，他已經學會不看它了。
    """
    check = _will_not_run(monkeypatch)

    assert check.blocking and not check.action and not check.pick_provider
    assert state_of(check) is SetupState.NOT_RUNNABLE


def test_a_login_state_that_cannot_be_read_is_not_drawn_as_ready() -> None:
    """改壞的樣子：`ok` 那條規則不再問「問不出來了嗎」，`unknown` 被它吃掉。

    使用者會看到：一顆薄荷點——跟一台完全正常的機器**一模一樣**的一列。
    而 `_signed_in()` 這個函式存在的理由逐字就是那顆綠燈：
    「`--version` 跑得好好的、清單全綠，然後每一次執行都死在中間」。
    """
    assert state_of(_cannot_tell_if_signed_in()) is SetupState.UNKNOWN


def test_a_cli_that_really_is_signed_in_does_not_get_the_dashed_circle(
    monkeypatch,
) -> None:
    """反方向：真的登入的那一列不准被貼上「問不出來」。

    改壞的樣子：判斷寫成「不是沒登入就算問不出來」。
    使用者會看到：兩支 CLI 都畫成虛線圈，包含他五分鐘前才登入的那一支——
    而一個每一列都出現的提醒等於沒有提醒。
    """
    assert state_of(_ready(monkeypatch)) is not SetupState.UNKNOWN


# --- 那句中文不准當判準 ------------------------------------------------------


def test_translating_that_sentence_moves_nothing(monkeypatch) -> None:
    """把 `detail` 與 `fix` 整句換掉，五種列**一格都不准動**。

    改壞的樣子：有人加一句 `if "尚未登入" in check.detail`。
    使用者會看到：**在中文版上什麼都看不到**——它今天是對的。
    英文版上線的那一天（或者只是有人把那句話從「但尚未登入」改成
    「尚未登入」），比對永遠不成立，每一列一起落到同一種狀態，
    而畫面看起來完全正常。
    """
    cases = {
        SetupState.OK: _ready(monkeypatch),
        SetupState.SIGNED_OUT: _signed_out(monkeypatch),
        SetupState.NOT_RUNNABLE: _will_not_run(monkeypatch),
        SetupState.UNKNOWN: _cannot_tell_if_signed_in(),
    }
    for expected, check in cases.items():
        translated = replace(check, detail="Not signed in.", fix="Run this instead.")
        assert state_of(translated) is expected, (
            f"換掉那句話之後，{check.key} 從 {expected.value} 變成 "
            f"{state_of(translated).value}——有人拿中文當判準了"
        )


def test_no_rule_ever_reads_that_sentence() -> None:
    """語法樹掃：這支模組**一次都不准讀 `.detail`**。

    上面那條是行為，這條是形狀。差別在於：行為那條只驗得到我想得到的那幾種
    句子；而「這支模組讀不讀得到那句話」是一個可以掃完的問題。

    改壞的樣子：為了少寫一個欄位，用 `check.detail` 分辨。
    使用者會看到：跟上面那條一樣的東西——今天什麼都看不到。
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8-sig"))
    reads = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "detail"
    ]
    assert not reads, (
        f"{MODULE} 在第 {reads} 行讀了 `detail`。那是一句**給人看的中文**，"
        "不是判準——拿它分辨的東西會在翻譯的那一天整組失效，而且不會有東西紅。\n"
        "要分辨就加一個結構欄位（`sign_in_unknown` 就是這樣加的）。"
    )


# --- 規則本身的形狀 ----------------------------------------------------------


def _every_shape() -> list[sd.Check]:
    """所有結構上湊得出來的 `Check`：三種 level × 有沒有 action × 有沒有
    pick_provider × 問不問得出登入狀態。"""
    shapes = []
    for level in sd.Level:
        for action in ((), ("claude", "setup-token")):
            for pick in ("", "claude"):
                for unreadable in (False, True):
                    shapes.append(
                        sd.Check(
                            "claude",
                            "Claude Code",
                            level,
                            "任何一句話",
                            action=action,
                            pick_provider=pick,
                            sign_in_unknown=unreadable,
                        )
                    )
    return shapes


def test_no_check_can_fit_two_rows_at_once() -> None:
    """五條規則互斥，所以**寫在前面**不是一種判準。

    改壞的樣子：有人把 `missing` 的條件放寬（拿掉 `not check.action`），
    於是「沒登入」的那一列同時合上兩條規則，而實際畫出來的是排在前面那一條。

    使用者會看到：**少掉的那一半，看起來就像那台機器本來就沒有那條路。**
    沒有任何東西會紅——這條測試就是那個「有東西會紅」。
    """
    for check in _every_shape():
        fits = [state.value for state, matches in _ROWS if matches(check)]
        assert len(fits) <= 1, (
            f"這一種形狀同時合上 {fits}："
            f"level={check.level.value}、action={bool(check.action)}、"
            f"pick_provider={bool(check.pick_provider)}、"
            f"sign_in_unknown={check.sign_in_unknown}。\n"
            "重疊的規則要收窄，不要靠排列順序決定——排列順序改壞的那天，"
            "紅的東西是零個。"
        )


def test_a_shape_nobody_covered_explodes_instead_of_being_called_ready() -> None:
    """【有預設值的分類等於沒有分類】。

    `Level.OFF` 是「這個東西沒設定，而且那不是問題」（Git 沒裝、LINE 沒開）。
    這一頁畫不出那種列——五種裡沒有一種是對的。

    改壞的樣子：結尾補一句 `return SetupState.OK`（或 `NOT_RUNNABLE`）。
    使用者會看到：一顆很有自信的點。薄荷的話它在說「可以按開始執行了」；
    玫瑰的話它叫他去重裝一個沒有壞的東西。兩種都不會有人發現。
    """
    off = sd.Check("git", "Git", sd.Level.OFF, "沒有安裝")

    with pytest.raises(CannotDrawThisCheck):
        state_of(off)


def test_the_five_names_are_the_ones_the_page_reads() -> None:
    """這五個字是跨兩個語言的契約，改一個字沒有人會報錯。

    改壞的樣子：把 `signed_out` 拼成 `signedout`。
    使用者會看到：頁面的 `STATE_CLASS[p.state]` 查不到，走 `|| 'unknown'`，
    於是那一列變成**虛線圈**——「登入狀態不明」，而我們明明知道他沒登入。
    """
    assert {state.value for state in SetupState} == {
        "ok",
        "signed_out",
        "missing",
        "not_runnable",
        "unknown",
    }


# --- 推導要接得到畫面，不然它只是一份自己對自己說話的規則 --------------------


def test_the_name_the_page_reaches_for_is_really_there(monkeypatch) -> None:
    """`credentials_web_panel.state_of()` 伸手拿的第一個名字就是這一支。

    那支面板的 docstring 逐字寫著它等的兩種形狀，`provider_state()` 是排在
    前面的那一個（另一個是 `Check` 自己帶一個 `state` 欄位——**這裡刻意不做
    那一種**，那一格留給面板那一側的測試當替身，做了會把它們的替身蓋掉）。

    順帶記著為什麼不叫 `credential_state`：那個函式名會被
    `test_no_new_door_opens_for_an_api_key` 當成一扇新開的收憑證的門而變紅。

    改壞的樣子：推導寫完了、測試全綠，但**沒有人叫得到它**——面板 `getattr`
    拿到 `None`，退回去看 `Check.state`，也沒有，於是回空字串。
    使用者會看到：那一頁永遠寫著「還沒有收到這台機器的檢查結果」，
    而檢查其實跑完了、答案其實算出來了。

    這是 `tools/zero_callers.py` 的 docstring 講的那一種，而它比死碼糟：
    「有人寫下一條規則、也寫了測試證明它成立，然後那條規則從來沒有到達畫面。」
    """
    state = sd.provider_state(_signed_out(monkeypatch))

    assert state == SetupState.SIGNED_OUT.value
    assert type(state) is str, (
        f"`provider_state()` 回的是 {type(state).__name__}，不是普通字串。"
        "頁面拿它去查 `STATE_CLASS` 那張表，查不到就走 `|| 'unknown'`——"
        "每一列都會變成虛線圈，而且不會有任何一個地方報錯。"
    )


def test_the_row_the_page_cannot_draw_is_not_smuggled_through_that_name() -> None:
    """`provider_state()` 不准替 `Level.OFF` 編一個答案出來。

    改壞的樣子：為了「面板不要炸」，在這一支裡包一層 try，接不住就回 `"ok"`。
    使用者會看到：Git 沒裝那一列被畫成一顆薄荷點的 provider——
    而那顆點的意思是「你可以按開始執行了」。
    """
    with pytest.raises(CannotDrawThisCheck):
        sd.provider_state(sd.Check("git", "Git", sd.Level.OFF, "沒有安裝"))


# --- 那個縫 ------------------------------------------------------------------


def _providers_handed_to_cli_check() -> list[str]:
    """`_cli_check()` 每一處呼叫收到的 provider。

    看不懂的形狀（不是字面字串）**當成失敗，不是跳過**——一條找不到目標就
    跳過的斷言，跟一條通過的斷言在報告上長得一模一樣。
    """
    tree = ast.parse(ENGINE.read_text(encoding="utf-8-sig"))
    providers: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_cli_check"):
            continue
        assert len(node.args) >= 4, (
            f"{ENGINE}:{node.lineno} 的 `_cli_check()` 少了 provider 那個引數，"
            "這條檢查看不懂它"
        )
        provider = node.args[3]
        assert isinstance(provider, ast.Constant) and isinstance(provider.value, str), (
            f"{ENGINE}:{node.lineno} 的 provider 不是字面字串，這條檢查讀不出來"
            "——看不懂就是失敗，不是跳過"
        )
        providers.append(provider.value)
    assert providers, (
        f"{ENGINE} 裡找不到任何一處呼叫 `_cli_check()`。"
        "這條檢查已經指不到東西了，底下那句斷言等於恆真。"
    )
    return providers


def test_the_gap_where_a_cli_has_no_login_route_is_kept_shut() -> None:
    """那個縫在 `_cli_check()` 裡，而它**釘死在來源，不是靠多一條規則猜**。

    `route is None` 那條分支回的是 BLOCKED、沒有 action、也沒有 pick_provider
    ——結構上跟「裝了但跑不起來」**一模一樣**，所以推導那一側分不出來，
    也不該假裝分得出來。

    改壞的樣子：接一支新的 CLI 進 `_cli_check()`，而 `credential_entry`
    不認得它。
    使用者會看到：那一支沒登入的時候畫成玫瑰點加「請重新安裝」。他照做，
    重裝完那一列一個字都沒變——ROADMAP P12 記著的就是這個形狀。
    """
    homeless = [
        provider
        for provider in _providers_handed_to_cli_check()
        if credential_entry.route_for(provider) is None
    ]
    assert not homeless, (
        f"這幾支 CLI 沒有登入路徑：{homeless}。\n"
        "`_cli_check()` 裡 `route is None` 那條分支會產出一個"
        "**跟壞掉的 shim 在結構上完全一樣**的 Check，於是憑證入口會叫一個"
        "只是還沒登入的人去重裝。\n"
        "先在 `credential_entry._ROUTES` 給它一條路，"
        "或者不要把它接進 `_cli_check()`。"
    )
