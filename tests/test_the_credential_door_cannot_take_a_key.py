"""憑證入口：AICC 帶他去登入，**碰不到任何一把 key**。

## 先講範圍，因為檔名比這份檔案守的東西大（2026-08-24）

**這裡守的是一個模組**：`app/services/credential_entry.py`，27 行寫死在
`MODULE`。檔名讀起來像「整個 AICC 都碰不到 key」，而那句話**在這個 repo 裡
就有反例**：`line_notifier.save_credentials(channel_access_token)` 由
`main_window` 真的呼叫得到——使用者在畫面上貼 LINE token，AICC 用 DPAPI
存進 `%APPDATA%`。那不是漏洞，是 `ROADMAP.md:1004-1008` 與
`SECURITY_MODEL.md:23` 拍過板的例外（「使用者自己交出來的 API key 可收可存」，
2026-08-09）；產品從來沒承諾過檔名那句話。`DECISIONS.md:271` 自己也把不變式
寫成模組級的。

所以要嘛檔名改小，要嘛守備範圍放大。**兩件事這一輪都沒有整個做完**：
改檔名會同時動到 `.aicc/knowledge/DECISIONS.md`、`docs/憑證入口.md`、
`tests/test_aicc_runs_nothing_destructive.py` 與
`tools/mutations/mutations_credentials_pass_through.json` 裡的引用，
那些不在這一輪的範圍裡——**留著一堆指不到東西的引用，比檔名大一號更糟**。

放大範圍那一半做了：`DECISIONS.md:271` 的推論「要開 API key 那條路之前
**先問他**」現在有機制了，在
`tests/test_no_new_door_opens_for_an_api_key.py`——`app/` 底下任何新模組長出
一個收得下憑證的入口，那支會紅。在這之前那句話只是一句話。

老闆 2026-08-24：

> CODEX 和 CLAUDE 那邊基本上**先做以會員優先**。

會員優先之後兩邊都是「帶我去登入」，而這份檔案守的是那件事**不會偷偷長回來**
——某一天有人為了「方便」加一個貼 key 的欄位，AICC 就開始保管憑證了，
而畫面上看起來只是多了一格輸入框。

## 為什麼這條要用語法樹掃，不是靠人記得

【金鑰只給變數名稱不給內容】是定案，但定案是一句話。
一句話擋不住一個 `def save_api_key(...)`——**擋得住的只有會變紅的檢查。**
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.services.credential_entry import (
    SUPPORTED,
    route_for,
    routes_for_signed_out,
)

MODULE = Path("app/services/credential_entry.py")


def test_every_supported_cli_has_a_way_in() -> None:
    """前提檢查：少一支的話，那一支的使用者會卡在一個沒有出口的畫面。"""
    for provider in SUPPORTED:
        route = route_for(provider)
        assert route is not None, f"{provider} 沒有登入路徑"
        assert route.argv, f"{provider} 的指令是空的"
        assert route.typed.strip(), f"{provider} 沒有可以複製的那一行"


def test_an_unknown_cli_gets_nothing_rather_than_a_guess() -> None:
    """**不猜。**猜出來的指令跑起來會失敗，而他會以為是自己打錯。"""
    assert route_for("gemini") is None
    assert route_for("") is None


def test_not_knowing_is_not_the_same_as_signed_out() -> None:
    """**這一條是這份檔案最容易被改壞的地方。**

    探測失敗（`None`）跟沒登入（`False`）長得很像，而把前者當後者，
    會叫一個已經登入的人再登一次——那一次他會照做，然後開始懷疑
    畫面上其他每一句話。
    """
    assert [r.provider for r in routes_for_signed_out({"claude": False})] == ["claude"]
    assert routes_for_signed_out({"claude": None, "codex": None}) == []
    assert routes_for_signed_out({"claude": True, "codex": True}) == []


def test_the_order_is_stable() -> None:
    """兩支都要登入的時候順序固定——每次開起來位置不一樣的清單很難用。"""
    both = routes_for_signed_out({"claude": False, "codex": False})
    assert [r.provider for r in both] == list(SUPPORTED)


def _parameter_names() -> set[str]:
    """這個模組每一個函式的每一個參數名。"""
    tree = ast.parse(MODULE.read_text(encoding="utf-8-sig"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                names.add(arg.arg)
            for extra in (args.vararg, args.kwarg):
                if extra is not None:
                    names.add(extra.arg)
    return names


# 一把 key 進得了這個模組的話，只可能從參數進來。
SMELLS_LIKE_A_SECRET = ("key", "token", "secret", "password", "credential", "auth")


def test_no_function_here_can_even_accept_a_key() -> None:
    """**收得下就代表某一天會有人存它。**

    這條不是在檢查「有沒有存」——存或不存是行為，而行為隔一次改動就會變。
    它檢查的是**這個模組的形狀**：沒有任何一個入口收得下憑證，
    所以「不小心存起來」這件事在這裡不是紀律問題，是打不出來的程式。
    """
    offenders = sorted(
        name
        for name in _parameter_names()
        if any(smell in name.lower() for smell in SMELLS_LIKE_A_SECRET)
    )
    assert not offenders, (
        f"{MODULE} 出現了收得下憑證的參數：{offenders}\n"
        "AICC 是把人送到登入那一步的手，不是保管的人。"
        "真的要開這條路，先去改 SECURITY_MODEL.md，不要從這裡開始。"
    )


# --- 沒登入的時候，畫面要帶他去 ------------------------------------------

def _signed_out_check(monkeypatch, provider: str):
    """把那台「裝了但沒登入」的機器造出來。

    這台開發機兩支都登入了，所以**這條路平常跑不到**——而跑不到的路
    就是會壞掉的路。
    """
    from app.services import startup_diagnostics as sd

    monkeypatch.setattr(sd.shutil, "which", lambda _: f"C:/fake/{provider}.cmd")
    monkeypatch.setattr(sd, "_tool_version", lambda _: "9.9.9")
    monkeypatch.setattr(sd, "_signed_in", lambda *_: False)
    return getattr(sd, f"check_{provider}")()


def test_a_signed_out_cli_offers_a_button_not_just_a_sentence(monkeypatch) -> None:
    """**「請自己去終端機打這一行」是把工作退回給他。**

    `action` / `action_label` 這兩個欄位從 2026-08-10 空到 2026-08-24，
    註解寫著「替『按一下就開始登入』留的位置」。P12 拍板之後就是現在。
    """
    for provider in ("claude", "codex"):
        check = _signed_out_check(monkeypatch, provider)
        assert check.blocking, f"{provider} 沒登入卻不擋"
        assert check.action, f"{provider} 沒登入只給了一句話，沒有按鈕"
        assert check.action_label.strip(), f"{provider} 的按鈕沒有字"


def test_the_login_line_has_exactly_one_source(monkeypatch) -> None:
    """畫面上那一句，跟 `credential_entry` 準備的那一句，**必須是同一句**。

    改壞的樣子就是 2026-08-24 當天的樣子：會員優先拍板之後
    `credential_entry` 改成了 `claude setup-token`，而 `startup_diagnostics`
    裡那份寫死的字串還停在 `claude auth login`——於是畫面繼續叫使用者
    去打一句**已經不是我們要他打的指令**。
    """
    for provider in ("claude", "codex"):
        check = _signed_out_check(monkeypatch, provider)
        route = route_for(provider)
        assert route is not None
        assert route.typed in check.fix, (
            f"{provider}：畫面說「{check.fix}」，"
            f"但憑證入口準備的是「{route.typed}」——兩份寫死的指令又長回來了"
        )
        assert tuple(check.action) == route.argv
