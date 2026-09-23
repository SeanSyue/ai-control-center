"""介面文字只准變少，不准變多。

定案（2026-08-22）：

> 【先做結構與守衛，翻譯之後做】介面文字全部挖到一個地方，並且加一支
> 「新寫死的中文就變紅」的檢查；**這一輪不翻譯**。
>
> 否決：現在整套翻成英文——**介面文字每天在改，翻完就過期**。
> 也否決「先不管」——今天約 1,500 段中文字串寫死在 35 個檔案裡，
> 每多做一週就多幾百段，**這件事只會越來越貴**。
>
> 不變式：**翻譯會過期，結構不會。**

2026-08-24 實際量到 **1,587 段、63 個檔案**（提示詞不算，見
`tools/i18n_scan.NOT_INTERFACE_TEXT`）。

## 為什麼是棘輪，不是「全部搬完」

一次搬 1,587 段是一次巨大的機械改動，而它每一段都可能改錯一個字。
**搬家可以任何一天做，做一半也不會壞；止血只有現在做才有用。**

所以這份檔案守的不是「已經搬完了」，是**「它不會變成 2,000 段」**：
每個檔案的既有段數記在 `tools/i18n_baseline.json`，**只准少不准多**。

## 這支棘輪抓不到什麼（誠實講）

同一個檔案裡**加一段、刪一段**，總數不變，這條是綠的。

那個洞是刻意留的：更嚴的做法是記下每一段字串的雜湊，但這個專案的介面
用字**每天在改**（光是今天就改了「偵測卡」「等待決策」「會寫檔案」三處）
——那樣的檢查會天天誤報，而**一條會誤報的檢查比沒有更貴：它會訓練人
忽略紅燈**。

段數這個粒度剛好對上要守的那條規則：**改字不算，加字才算。**
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from tools.i18n_scan import BASELINE, NOT_INTERFACE_TEXT, scan


def _baseline() -> dict[str, int]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_the_baseline_exists_and_is_not_empty() -> None:
    """前提檢查：基準檔不見的話，底下每一條都會「全部通過」。"""
    assert BASELINE.is_file(), (
        f"{BASELINE} 不見了。跑 `python tools/i18n_scan.py --write` 重建，"
        "**但先確認為什麼它會不見**——把基準砍掉重錄，等於把欠債一筆勾銷。"
    )
    base = _baseline()
    assert sum(base.values()) > 500, (
        f"基準只有 {sum(base.values())} 段，而 2026-08-24 量到 1,587 段。"
        "這個數字掉這麼多，比較可能是掃描壞了，不是有人搬完了。"
    )


def test_no_file_gains_new_hardcoded_interface_text() -> None:
    """**這一條是這份檔案存在的理由。**

    新的介面文字要放進 `app/i18n.py`，用 `t("some.key")` 取。
    """
    base = _baseline()
    now = scan()
    grew = sorted(
        f"{name}：{base.get(name, 0)} → {count}"
        for name, count in now.items()
        if count > base.get(name, 0)
    )
    assert not grew, (
        "這幾個檔案多了寫死的中文介面文字：\n  "
        + "\n  ".join(grew)
        + "\n\n新的字串請放進 `app/i18n.py`，用 `t(\"some.key\")` 取。\n"
        "真的不是介面文字（例如給 AI 看的提示詞），"
        "把那個檔案加進 `tools/i18n_scan.NOT_INTERFACE_TEXT` 並**寫下理由**。\n"
        "改字不算，加字才算——所以這條紅了就是真的多了一段。"
    )


def test_a_brand_new_file_starts_at_zero() -> None:
    """**新檔案不會自動繼承既有的額度。**

    這一條是被這個專案自己的歷史逼出來的：新加的檔案不會自動繼承既有政策，
    而「基準檔裡沒有它」最省事的讀法是「那就隨便它」。
    """
    base = _baseline()
    now = scan()
    newcomers = sorted(name for name in now if name not in base)
    assert not newcomers, (
        "這幾個檔案是新的，而且一出生就帶著寫死的中文：\n  "
        + "\n  ".join(newcomers)
        + "\n\n新程式碼沒有理由再欠這筆債——請用 `app/i18n.py`。"
    )


# 同一串字、不同的東西——**這些是刻意的，不是漏掉的**。
#
# 這條檢查 2026-08-24 一開始寫成「同一句話不准有兩個 key」，而它**逼我去改
# 使用者看得到的字**：把名字來源那格的「對話名稱」改成「對話自己的名稱」，
# 只為了跟對話清單的欄位標題不撞。
#
# 老闆退了，而他是對的。這個專案自己記過同一件事：
#
# > **為了讓檢查過得去而改產品程式，順序反了。**
#
# 兩個 key 同一串字，有兩種可能：
#
# * **同一件事講了兩次** —— 那是真的問題（翻譯會翻出兩種說法）
# * **兩件事剛好用同一串中文** —— 那是中文的巧合，而英文很可能不一樣
#
# 分不出來的是機器，所以人要在這裡寫下來，**而且要寫得出兩者的差別**。
SAME_WORDS_DIFFERENT_THINGS: dict[frozenset, str] = {
    frozenset({"library.column.name", "title_source.session_name"}):
        "一個是對話清單那一欄的**標題**，一個是「這個名字**從哪裡來**」。"
        "英文會不一樣（Conversation name ／ From the conversation own name）。",
    frozenset({"gate.category.security", "memory.security"}):
        "一個是 **Decision Gate 的一種分類**（碰到安全相關的事就停下來問），"
        "一個是 `SECURITY_MODEL.md` 在共享記憶那一頁的**簡稱**。"
        "英文會不一樣（Security concerns ／ Security model）。",
    frozenset({"timeline.source.user", "audit.answered_by.user", "search.speaker.user"}):
        "三個都是「使用者」，但問的是三件事：時間軸的**來源篩選**"
        "（這一筆是誰講的）、決策紀錄的**答覆者**（這一題是誰回的）、"
        "對話預覽裡的**說話者**（這一句是誰說的）。"
        "英文會不一樣（From: user ／ Answered by: the user ／ You）。",
    # `role.worker`／`common.run` 與 `role.approver`／`step.approve` 兩筆
    # 2026-08-25 一起拿掉了：出廠座位名
    # 全部加了「者」（「執行者」），跟按鈕上的「執行」不再是同一串字。
    # **那條放行過期了，而過期的放行讀起來跟還成立的一模一樣**——
    # `test_the_excused_pairs_still_exist_and_still_clash` 當場點名它。
}


def test_the_catalogue_has_no_accidental_duplicates() -> None:
    """同一句話有兩個 key，**多數時候**是漏掉的。

    漏掉的樣子：翻譯的時候翻出兩種說法，而使用者在兩個地方看到同一件事的
    兩種講法，然後懷疑它們是不是不一樣。

    **但不是每一次都是漏掉的**——見上面那份清單。
    """
    from app.i18n import STRINGS

    seen: dict = {}
    for key, text in STRINGS.items():
        seen.setdefault(text, []).append(key)

    clashes = []
    for text, keys in seen.items():
        if len(keys) < 2:
            continue
        if frozenset(keys) in SAME_WORDS_DIFFERENT_THINGS:
            continue
        clashes.append(f"{'、'.join(keys)}：「{text[:24]}⋯」")

    assert not clashes, (
        "同一句話有兩個 key：\n  " + "\n  ".join(clashes)
        + "\n\n真的是同一件事的話，併成一個 key。"
        "**如果是兩件事剛好用同一串中文**，加進 SAME_WORDS_DIFFERENT_THINGS "
        "並寫下兩者的差別——**不要改使用者看得到的字來讓這條過**。"
    )


def test_the_excused_pairs_still_exist_and_still_clash() -> None:
    """放行清單不准腐爛。

    兩個方向都會紅：清單上的 key 不見了、或者它們已經不再是同一串字
    ——後者表示那筆放行過期了，而**過期的放行等於替下一個人先開好一扇門**。
    """
    from app.i18n import STRINGS

    for keys, reason in SAME_WORDS_DIFFERENT_THINGS.items():
        missing = sorted(k for k in keys if k not in STRINGS)
        assert not missing, f"放行清單上的 key 不見了：{missing}"
        texts = {STRINGS[k] for k in keys}
        assert len(texts) == 1, (
            f"{sorted(keys)} 已經不是同一串字了（{texts}），那筆放行過期了，請拿掉。"
        )
        assert len(reason.strip()) >= 20, f"{sorted(keys)} 的理由太短"

def test_every_key_used_anywhere_exists() -> None:
    """**沒有一個 key 進得了正式版而 `STRINGS` 裡沒有它。**

    這條在的話，`t()` 查不到就丟例外才是安全的。少了它，一個打錯的 key
    會在使用者按到那個按鈕的時候才炸——而那是最不該炸的時刻。
    """
    from app.i18n import STRINGS

    missing: list[str] = []
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "t" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if first.value not in STRINGS:
                    missing.append(f"{path.as_posix()}:{node.lineno} → {first.value!r}")
    assert not missing, (
        "這幾處要的介面文字不存在：\n  " + "\n  ".join(missing)
    )


def test_the_excused_files_say_why() -> None:
    """放行不能變成免死金牌。

    一個檔案名字就能讓守衛閉嘴的話，最省事的修法永遠是加一行到清單裡。
    所以**每一個都要寫得出理由**。
    """
    for name, reason in NOT_INTERFACE_TEXT.items():
        assert Path(name).is_file(), (
            f"{name} 不在了，但還留在放行清單裡——**過期的放行等於替下一個人"
            "先開好一扇門**"
        )
        assert len(reason.strip()) >= 20, f"{name} 的放行理由太短：{reason!r}"


def test_nobody_resolves_text_at_import_time() -> None:
    """**`t()` 只准在函式裡叫。**

    模組最上層叫 `t()` 的話，那句話會在 import 的當下被取出來凍住——
    之後換語言、換設定、換使用者都不會再變，而**畫面上看不出差別**。

    這條是我自己犯過兩次才加的：`LoginRoute.typed` 原本是 import 時算好的
    `typeable_name()` 結果（於是「Windows 上一定要帶 .cmd」那條檢查
    monkeypatch 不到它，安靜地變成恆真），而 `project_detection` 的把握
    標籤第一版也是 import 時就把字取出來。**同一個形狀，一天之內兩次。**
    """
    offenders: list[str] = []
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        inside: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for child in ast.walk(node):
                    inside.add(id(child))
        for node in ast.walk(tree):
            if id(node) in inside or not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "t" and node.args:
                offenders.append(f"{path.as_posix()}:{node.lineno}")
    assert not offenders, (
        "這幾處在模組最上層取介面文字：\n  "
        + "\n  ".join(offenders)
        + "\n\n那句話會在 import 的當下凍住。改成存 key，用到的時候才 `t()`。"
    )


def test_a_template_can_have_a_variable_called_key() -> None:
    """`t()` 的第一個參數只能用位置傳。

    不然模板裡不能有叫 `key` 的變數——而「`{key}` 要是一個物件」是一句
    非常自然的錯誤訊息，寫的人完全不會想到它撞到函式自己的參數名。

    **那個 TypeError 2026-08-24 真的發生過一次**，而它是在跑全套的時候才
    炸出來的：那條路平常走不到（設定檔形狀壞掉的時候才走）。
    """
    import inspect

    from app.i18n import t

    kinds = [p.kind for p in inspect.signature(t).parameters.values()]
    assert kinds[0] is inspect.Parameter.POSITIONAL_ONLY, (
        "`t()` 的第一個參數不是位置專用——模板裡就不能有叫 key 的變數了"
    )
