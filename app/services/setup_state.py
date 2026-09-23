"""憑證入口那一列是五種裡的哪一種：**用結構分，不看那句中文**。

憑證入口（`AICC-視覺概念/credentials_v1.html`）只回答一個問題——
「我現在按得下『開始執行』嗎」——而它把答案畫成五種列：

| state | 點的樣子 | 他要做的事 |
|---|---|---|
| `ok` | 薄荷 | 沒事 |
| `signed_out` | 金色·脈動 | 〔帶我去登入〕 |
| `missing` | 金色 | 〔怎麼辦〕→ 安裝／掃描／指路 |
| `not_runnable` | 玫瑰 | 〔怎麼辦〕→ 重裝 |
| `unknown` | 虛線圈 | 沒有按鈕，但話說出來 |

而引擎那一側的 `Check` 只有 `level`（三值）跟 `detail`（一句中文）。

## 為什麼推導住在 Python，不住在頁面裡

畫面讀的是一個**明著推過來的 `state` 欄位**（交件 2026-08-25-C §四 的規格就是
這樣訂的）。推導如果住在 JS 裡，頁面就得自己去解讀 `detail`——而 `detail`
是一句給人看的話，它的形狀會因為換一個字而變，也會因為翻譯而整句換掉。

## **那句中文不准當判準**

2026-08-24 一天之內在這個 repo 抓到五處「拿中文當查詢的鍵」。症狀一律相同：
**英文版上線那天比對永遠不成立，而畫面看起來完全正常**——點是有顏色的、
字是有的，只是每一顆都指到同一種狀態。

所以這支模組一個字都不讀 `detail`。判準只有四樣，全部是結構：
`level`、`action` 有沒有東西、`pick_provider` 有沒有東西、`sign_in_unknown`
有沒有立起來。`test_no_rule_ever_reads_that_sentence` 用語法樹釘住這件事
——它掃的是「這支模組有沒有讀過 `.detail`」，所以下一個人為了方便加一句
`if "尚未登入" in check.detail` 會當場紅。

## 五條規則互斥，所以**順序不是判準**

`state_of()` 是照 `_ROWS` 的順序找第一條合的，但那個順序不該有意義：五條規則
寫成互斥的（`missing` 與 `not_runnable` 都明寫 `not check.action`），
`test_no_check_can_fit_two_rows_at_once` 把「任何一種形狀最多只有一條規則合得上」
釘住。

差別在於哪一天會紅：靠順序的話，有人把兩條規則寫成重疊的，**什麼都不會紅**
——畫面上少掉的那一半（例如「選擇位置…」）安靜地不見，而它看起來就像那台機器
本來就沒有那條路。

## 沒有預設值：合不上的形狀**當場炸**

【有預設值的分類等於沒有分類】。所以最後一條規則寫的是「BLOCKED 而且兩條路
都沒有」，**不是 `else`**——`Level.OFF` 那一種（Git 沒裝、LINE 沒設定）不會
安靜地落成「可以用」，它會丟 `CannotDrawThisCheck`。

那不是防禦性程式碼，是這一頁的規格：**沒推的東西不編**。頁面在沒收到 `setup`
的時候畫的是「還沒有收到這台機器的檢查結果」，而那句話比一顆猜出來的薄荷點
誠實——薄荷點的意思是「你可以按開始執行了」，猜錯的代價是他付完整套設定的
成本才發現少一步。

## 那個縫：`_cli_check()` 裡 `route is None` 那一條

那條分支回 `Check(..., BLOCKED, f"{version}，但尚未登入")`——**沒有 action，
也沒有 pick_provider**，照上表落進 `not_runnable`，也就是畫面會叫一個
「只是還沒登入」的人去**重裝**。重裝完那一列還是長那樣，而那正是 ROADMAP P12
記著的那次實機事故的形狀（一個很有自信的錯誤指示）。

**這裡不替它多開一條規則。** 多一條「BLOCKED、兩條路都沒有、但其實是沒登入」
的規則，靠的只能是猜（它跟一個真的壞掉的 shim 在結構上完全一樣），而一條靠猜
的規則跟沒有那條規則一樣危險。

改成把那個縫**釘死在它的來源**：`_cli_check()` 收得到的每一個 provider 都要有
登入路徑。今天 claude 與 codex 兩支都有（`credential_entry.route_for()` 認得），
所以那條分支走不到；第三支 CLI 被接進來、而它沒有登入路徑的那一天，紅的是
`test_the_gap_where_a_cli_has_no_login_route_is_kept_shut`，**不是使用者畫面上
那一句「請重新安裝」**。
"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from app.services.startup_diagnostics import Check, Level


class SetupState(str, Enum):
    """頁面認得的那五個字。

    值就是 `credentials_v1.html` 的 `STATE_CLASS` 那張表的鍵。改動這裡的值等於
    改動一份跨兩個語言的契約：頁面查不到的時候走 `|| 'unknown'`，
    於是**每一列都會變成虛線圈**，而且不會有任何一個地方報錯。
    """

    OK = "ok"
    SIGNED_OUT = "signed_out"
    MISSING = "missing"
    NOT_RUNNABLE = "not_runnable"
    UNKNOWN = "unknown"


class CannotDrawThisCheck(ValueError):
    """這個 `Check` 合不上任何一列（或同時合上兩列）。

    **刻意不回一個預設值。**回 `ok` 的話畫面會端出一顆薄荷點，那顆點的意思是
    「你可以按開始執行了」；回 `not_runnable` 的話它會叫他去重裝一個沒壞的東西。
    兩種都是很有自信的錯誤指示，而它們在畫面上跟正確的那一列長得一模一樣。

    炸掉的下場是這一頁畫「還沒有收到這台機器的檢查結果」——那句話是真的。
    """


def _sign_in_unreadable(check: Check) -> bool:
    """這一列的登入狀態問不出來。

    `ok` 跟 `unknown` **共用這一句**，不然它們會分開腐爛：其中一條被改成
    「不管怎樣都算已登入」的那天，另一條照樣成立，於是同一個 `Check` 兩列都合
    ——或者兩列都不合。
    """
    return check.sign_in_unknown


# 五條規則。**互斥**（見模組 docstring），所以順序不是判準。
_ROWS: tuple[tuple[SetupState, Callable[[Check], bool]], ...] = (
    # 「問不出來」跟「真的登入了」都是 `Level.OK`——分辨得出來的只有這個欄位。
    (
        SetupState.UNKNOWN,
        lambda check: check.level is Level.OK and _sign_in_unreadable(check),
    ),
    (
        SetupState.OK,
        lambda check: check.level is Level.OK and not _sign_in_unreadable(check),
    ),
    # `action` 全檔只有一個地方會設：`_cli_check()` 裡 `signed is False` 那一條。
    (
        SetupState.SIGNED_OUT,
        lambda check: check.level is Level.BLOCKED and bool(check.action),
    ),
    # `pick_provider` 是 `_not_on_path()` 兩條分支都會設的那一個。
    (
        SetupState.MISSING,
        lambda check: check.level is Level.BLOCKED
        and not check.action
        and bool(check.pick_provider),
    ),
    # 其餘的 BLOCKED。**寫出它的條件，不寫 `else`**——寫 `else` 的話
    # `Level.OFF` 會安靜地變成「找得到但跑不起來」。
    (
        SetupState.NOT_RUNNABLE,
        lambda check: check.level is Level.BLOCKED
        and not check.action
        and not check.pick_provider,
    ),
)


def state_of(check: Check) -> SetupState:
    """這一列在憑證入口上是哪一種。合不上就丟 `CannotDrawThisCheck`。

    推給畫面的是 `state_of(check).value`（`"ok"` / `"signed_out"` / …）。

    收得下的只有憑證入口畫得出來的那幾種 `Check`，也就是 `_cli_check()` 產的
    那些。整份檢查清單裡的 `Level.OFF`（Git 沒裝、LINE 沒設定）**不是**這一頁
    的東西，丟進來會炸——那是刻意的：那幾列在這一頁上沒有任何一種畫法是對的。
    """
    fits = [state for state, matches in _ROWS if matches(check)]
    if len(fits) != 1:
        raise CannotDrawThisCheck(
            f"{len(fits)} of the {len(_ROWS)} rows fit this check "
            f"(key={check.key!r}, level={check.level.value!r}, "
            f"action={bool(check.action)}, "
            f"pick_provider={bool(check.pick_provider)}, "
            f"sign_in_unknown={check.sign_in_unknown}) -> "
            f"{[state.value for state in fits]}. "
            "Every check the credentials page draws must fit exactly one row. "
            "Widen or narrow the rules so it does - never add a fallback, and "
            "never tell the rows apart by reading Check.detail."
        )
    return fits[0]
