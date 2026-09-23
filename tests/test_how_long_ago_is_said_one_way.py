"""「多久以前」只有一種講法，而且**沒有人自己再寫一份**。

搬家搬到最後才發現五個面板各有一份一模一樣的 `_relative()`（連 90 秒
那個門檻都一樣）。它們一直是對的，因為五份剛好同步——而「剛好同步」
不是機制，是運氣。

這裡守兩件事：

1. **講出來的字對**——含邊界（89 秒 vs 90 秒），因為那個 `<` 寫成 `<=`
   不會有任何一個現有測試變紅。
2. **沒有第二份**——任何檔案自己算「N 分鐘前」就紅。這一條才是重點：
   上面那一條只保護這一份，而下一個抄一份過去的人不會來改它。
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app.i18n import t
from app.services import relative_time

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# 這些字**只准**從 `relative_time` 出來。寫在別的地方就是第六份。
ONLY_HERE = ("分鐘前", "小時前", "天前")

# 「剛剛」單獨當一句話才算標籤。**當子字串抓會誤傷**：`prompt_builder`
# 裡有「而你自己剛剛才要蓋章說『我看過了』」——那是給 AI 的提示詞裡的
# 一個副詞，跟時間標籤無關。誤傷的代價不是這條紅燈，是下一個人為了讓它
# 綠而去改那句提示詞。
EXACTLY = ("剛剛",)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, t("time.just_now")),
        (89, t("time.just_now")),
        # 剛好跨過去：`<` 寫成 `<=` 的話這一格會變成「剛剛」。
        (90, t("time.minutes_ago", n=1)),
        (3599, t("time.minutes_ago", n=59)),
        (3600, t("time.hours_ago", n=1)),
        (86399, t("time.hours_ago", n=23)),
        (86400, t("time.days_ago", n=1)),
    ],
)
def test_the_words_for_how_long_ago(seconds: float, expected: str) -> None:
    assert relative_time.describe_age(seconds) == expected


def test_a_time_in_the_future_never_shows_a_negative_number() -> None:
    """時鐘被調過的那一天，畫面上不准出現「-3 分鐘前」或「-9 分 40 秒」。

    三個函式各自的擋法不一樣，**而那個差別是量出來的**：前兩個是門檻
    接住的（負數落進第一格），計時器是夾住的（它直接進 `divmod`，而
    `divmod(-500, 60)` 給 `(-9, 40)`）。三個都測，因為擋法不同就代表
    它們會各自壞。
    """
    assert relative_time.describe_age(-500) == t("time.just_now")
    assert relative_time.describe_span(-500) == t("time.under_a_minute")
    assert relative_time.describe_clock(-500) == t("time.minutes_seconds", m=0, s="00")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, t("time.under_a_minute")),
        (59, t("time.under_a_minute")),
        (60, t("time.minutes", n=1)),
        (3600, t("time.hours", n=1)),
        (3660, t("time.hours_minutes", h=1, m=1)),
        (86400, t("time.days", d=1)),
        (90000, t("time.days_hours", d=1, h=1)),
    ],
)
def test_the_words_for_how_long_it_took(seconds: float, expected: str) -> None:
    """**「3 分鐘」不是「3 分鐘前」**——差一個字，回答的是不同問題。"""
    assert relative_time.describe_span(seconds) == expected


def test_a_two_second_answer_is_not_rounded_up_to_a_minute() -> None:
    """進位的話，「AI 到底有沒有在想」那一題就永遠看不到答案。"""
    assert relative_time.describe_span(2) == t("time.under_a_minute")
    assert relative_time.describe_span(2) != t("time.minutes", n=1)


def test_the_waiting_clock_keeps_its_seconds() -> None:
    """等待計時器的秒不能省——省掉的話它每分鐘才動一次，看起來像當掉。"""
    assert relative_time.describe_clock(187) == t("time.minutes_seconds", m=3, s="07")


def test_nobody_else_spells_out_how_long_ago() -> None:
    """整個 `app/` 只有 `relative_time` 講得出這幾個字。

    掃語法樹裡的字串常數，**包含 f-string 的碎片**——五份裡有四份是
    `f"{int(seconds // 60)} 分鐘前"`，純字面比對抓不到它。
    """
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if path.name in {"i18n.py", "relative_time.py"}:
            continue
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        # docstring 不算：這個專案刻意用中文寫說明，而說明裡提到這些字
        # （包括 `relative_time` 自己那份「為什麼會有這個檔案」）是好事。
        doc_ids = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node,
                (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            )
            and getattr(node, "body", None)
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in doc_ids:
                continue
            for word in (*ONLY_HERE, *EXACTLY):
                hit = node.value == word if word in EXACTLY else word in node.value
                if hit:
                    offenders.append(
                        f"{path.relative_to(APP.parent).as_posix()}:{node.lineno} "
                        f"自己寫了「{word}」"
                    )
    assert not offenders, (
        "「多久以前」又長出第二份。它要嘛呼叫 "
        "`relative_time.describe_age()`，要嘛就是一個新的問題、"
        "該有自己的名字：\n  " + "\n  ".join(offenders)
    )


# 本來還有一條「頁面不准自己算相對時間」，**寫完就拿掉了**。
#
# 它紅了四頁，而四頁都是**示範資料**（`DEMO = [{elapsed:'18 小時前'}]`
# ——頁面要能單獨打開給設計看，那些字非在那裡不可）。真正在算的只有
# `dashboard_v5` 的 `ANCHORS`，而它算的是**星軌上的位置**，不是文字。
#
# 一條分不出「示範資料」與「第二個格式化器」的檢查，會被下一個人用
# 「把示範資料刪掉」弄綠——而那讓頁面**更難**單獨打開，比原本的問題還糟。
#
# 記在這裡而不是刪掉，是因為那個風險是真的：頁面某天開始自己算，
# 這裡沒有東西擋。想擋的話要擋在「頁面有沒有拿到已經算好的字」那一側，
# 而那是頁面設計的檔案，不是我該單方面訂的。
