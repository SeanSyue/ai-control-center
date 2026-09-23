"""剝除開場白用的那些字，要跟**引擎真的送出去的**一模一樣。

## 為什麼這條非有不可

`session_panel` 要從一段對話紀錄裡猜出「這個對話在做什麼」，而那段紀錄的
開頭通常是 AICC 自己寫進去的提示詞。那些字要被剝掉，不然**每一個對話的
標題都會變成同一句開場白**。

比對的字跟提示詞之間 2026-08-24 之前**沒有任何連結**：改了 prompt_builder
不會有任何東西提醒你回來改剝除規則，而症狀是所有標題慢慢退化成同一句話
——沒有人會回報，因為它看起來只是「標題有點怪」。

## 而它已經發生了

搬進 `prompt_markers` 的時候一個一個去比對，量到三個字串在今天的
`prompt_builder` 裡**一次都不出現**：`任務要求`、`請完成以下修正`、
`技術審查`。

它們留著（舊對話紀錄裡還有），**但現在明著標成 `LEGACY`**，而不是混在
現行的裡面看起來一樣有效。
"""
from __future__ import annotations

from pathlib import Path

from app.core.prompt_markers import CURRENT, LEGACY, is_boilerplate, opening_markers

BUILDER = Path("app/core/prompt_builder.py")


def test_every_current_marker_really_appears_in_a_prompt() -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：有人改了提示詞的開場白，而剝除規則還停在舊的
    ——每一個對話的標題慢慢變成同一句話。
    """
    source = BUILDER.read_text(encoding="utf-8")
    missing = [marker for marker in CURRENT if marker not in source]
    assert not missing, (
        f"這幾個標記在 `prompt_builder` 裡一次都不出現：{missing}\n"
        "要嘛提示詞改了而這裡沒跟著改（那些字現在剝不掉任何東西），"
        "要嘛它其實是歷史——**是歷史的話請移到 `LEGACY` 並寫下理由**，"
        "不要留在現行那一份裡看起來一樣有效。"
    )


def test_the_legacy_ones_are_really_gone_from_todays_prompts() -> None:
    """`LEGACY` 是「舊版寫過、現在不寫」。

    如果它其實還在寫，那它就不是歷史——放錯地方的分類，下一個人會照著
    分類做判斷。
    """
    source = BUILDER.read_text(encoding="utf-8")
    still_here = [marker for marker in LEGACY if marker in source]
    assert not still_here, (
        f"這幾個標成「歷史」的字，今天的提示詞裡還在寫：{still_here}\n"
        "把它們移回 `CURRENT`。"
    )


def test_every_legacy_marker_says_why_it_is_still_here() -> None:
    """留著一個沒有人知道它在擋什麼的字，跟留著一段死程式一樣。"""
    for marker, reason in LEGACY.items():
        assert len(reason.strip()) >= 10, f"「{marker}」的理由太短：{reason!r}"


def test_both_kinds_of_colon_are_covered() -> None:
    """**全形與半形都要。**

    使用者的對話紀錄橫跨好幾版 AICC，而其中一版用的是半形冒號。
    只比一種的話，那一版寫的紀錄會整段留著開場白。
    """
    markers = opening_markers()
    for stem in ("請完成以下任務", "原始任務"):
        assert f"{stem}：" in markers, f"{stem} 少了全形冒號那一種"
        assert f"{stem}:" in markers, f"{stem} 少了半形冒號那一種"


def test_ordinary_content_is_not_mistaken_for_boilerplate() -> None:
    """**剝除規則不准咬到真的內容。**

    咬到的樣子比漏掉更糟：使用者那個對話真正在做的事被判成開場白丟掉，
    而標題變成空的。
    """
    assert is_boilerplate("你是此專案的執行者，請完成以下任務")
    assert not is_boilerplate("把這段影片剪到三分鐘")
    assert not is_boilerplate("重構 KYC 權限流程")


def test_this_module_is_not_treated_as_interface_text() -> None:
    """**這些字不准被翻譯。**

    介面翻成英文的那一天，使用者硬碟上的舊對話紀錄還是中文——翻掉這裡
    等於讓剝除規則對所有既有紀錄失效。

    `tools/i18n_scan.NOT_INTERFACE_TEXT` 要放行這個檔案，而放行要有理由。
    """
    from tools.i18n_scan import NOT_INTERFACE_TEXT

    assert "app/core/prompt_markers.py" in NOT_INTERFACE_TEXT
    assert len(NOT_INTERFACE_TEXT["app/core/prompt_markers.py"].strip()) >= 20
