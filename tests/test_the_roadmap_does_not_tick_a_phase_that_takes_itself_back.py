"""藍圖頁不准把一句「⋯完成，可是還卡在⋯」畫成一個做完的階段。

## 使用者會看到什麼（沒有這幾條的話）

藍圖頁標題印「**11** / 14 階段完成 · 2 個進行中」。P12「首次設定與憑證入口」
的星點是完成色、軌道連到它那一段是亮的，而展開之後第一列寫的是
「狀態：方向待決定（2026-08-09 提出）⋯」。P6 同樣：那句話自己說「只剩線上
檢查更新，而那件事卡在一個還沒有的東西」，畫面說它做完了。

判準是「這句話裡有沒有『完成』兩個字」，而那兩個字在一句轉折句裡照樣出現。

## 為什麼原本那條測試沒有講話

`tests/test_roadmap_web_panel.py:97` 是唯一一條吃真實 `ROADMAP.md` 的檢查，
而它只驗形狀（有幾個階段、每個 dict 有沒有那三個 key），判錯照樣綠燈。
它的 `SAMPLE` 裡 P6 還寫著「仍未開始」——那句話在真的檔案裡早就整段改掉了。
**一份跟真實檔案對不上的樣本，測到的是樣本。**

## 這幾條擋不住什麼

擋不住沒有用到那幾個轉折詞的句子（「⋯已完成，但發佈來源還沒有著落」裡的
「但」不在清單上）。這是啟發式，不是解析。真正的最後一道防線是**那句話
本身永遠印在該階段的第一列**，所以猜錯藏不住——那一列的完成勾勾還沒拿掉，
見 `docs/WHY.md`。
"""
from __future__ import annotations

from app.core.prompt_markers import ROADMAP_UNFINISHED_TURN
from app.ui.roadmap_web_panel import ROADMAP, parse_roadmap, state_from_prose

# 逐字抄自 `ROADMAP.md` 的兩句話（2026-08-24）。**抄進來是刻意的**：
# 真的檔案會改，而這兩句是這條規則的來源——它們要在原檔案改掉之後仍然
# 被測到，不然這份檔案守的東西會跟著那次編輯一起消失。
P12_SENTENCE = (
    "方向待決定（2026-08-09 提出）。CLI 探索與用量記錄兩個服務層都已完成，"
    "介面規格已交付設計端"
)
P6_SENTENCE = (
    "安裝程式基礎建設已完成（2026-08-05）；診斷、乾淨錯誤訊息、rollback、"
    "版本比對、自訂圖示、中文安裝介面已完成（2026-08-08）。只剩「線上檢查"
    "更新」，而那件事卡在一個還沒有的東西：發佈來源。"
)


def test_a_sentence_that_says_completed_and_then_takes_it_back_is_not_done() -> None:
    """**這一條是這份檔案存在的理由。**

    改壞了的樣子：這兩個階段的星點變成完成色、軌道整段亮起來，標題那句
    「N / 14 階段完成」多算兩個——而它們一個在等使用者拍板方向，一個卡在
    還沒有的發佈來源。
    """
    assert state_from_prose(P12_SENTENCE) == "now", (
        "「方向待決定」被判成做完了——它只是句子裡有「完成」兩個字"
    )
    assert state_from_prose(P6_SENTENCE) == "now", (
        "「只剩線上檢查更新，而那件事卡在⋯」被判成做完了"
    )


def test_a_sentence_that_really_is_finished_still_counts_as_done() -> None:
    """反方向也要守：**降級規則不准把每一段都降下去。**

    只看「有沒有完成兩個字」會多算；改成「有轉折詞就降級」而清單放太寬，
    會變成整份藍圖沒有一個階段是完成的——那同樣是一份沒有資訊的畫面，
    只是錯在另一邊。
    """
    assert state_from_prose("第一版完成，已加入匯出前預覽與安全掃描。") == "done"
    assert state_from_prose("**改版收尾完成（2026-08-08）**。") == "done"


def test_the_real_roadmap_still_has_a_sentence_that_takes_itself_back() -> None:
    """前提檢查：**真的檔案裡還有這種句子。**

    一段都沒有的話，底下那條全稱檢查會變成恆真——而它讀起來跟真的守著
    一模一樣。這條紅了不代表壞掉，可能是那幾段真的做完了；那就把上面兩句
    逐字樣本留著，把這條改成「曾經有過」的紀錄。
    """
    text = ROADMAP.read_text(encoding="utf-8")
    hits = [word for word in ROADMAP_UNFINISHED_TURN if word in text]
    assert hits, (
        "ROADMAP.md 裡一個轉折詞都沒有了——底下那條全稱檢查現在是恆真的"
    )


def test_no_phase_the_page_calls_done_has_a_sentence_that_takes_it_back() -> None:
    """全稱句：畫面上每一個完成色的階段，它自己那句話都不准打自己的臉。

    這一條吃的是真的 `ROADMAP.md`，所以下一次有人在某段狀態句裡寫下
    「只剩⋯」而忘了改那段的措辭時，它會在使用者看到那個完成勾勾之前變紅。
    """
    from app.i18n import t

    phases = parse_roadmap(ROADMAP.read_text(encoding="utf-8"))
    assert phases, "一個階段都沒解析出來——這條檢查是恆真的"

    lying = []
    for phase in phases:
        if phase["state"] != "done":
            continue
        first = phase["items"][0] if phase["items"] else {}
        if first.get("title") != t("dash.detail.state"):
            continue
        sentence = first.get("note", "")
        turns = [word for word in ROADMAP_UNFINISHED_TURN if word in sentence]
        if turns:
            lying.append(f"{phase['id']}：{turns} ／「{sentence[:40]}⋯」")

    assert not lying, (
        "這幾個階段被畫成完成，而它們自己那句話說了相反的話：\n  "
        + "\n  ".join(lying)
    )
