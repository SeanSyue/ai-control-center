"""廠牌在畫面上叫什麼。**一份，不是每個畫面自己寫一份。**

## 為什麼要有這一個檔案

引擎裡的廠牌永遠是小寫代號（`claude`／`codex`）——那是拿去組指令、比對
設定用的，**不是給人看的**。畫面上要寫「Claude Code」跟「Codex CLI」。

頁面設計 2026-08-24 問的原話：

> 廠牌名稱畫面上怎麼寫？引擎給的永遠是小寫 `claude`／`codex`，
> 而 repo 裡沒有任何顯示名對照表。
> **我建議回引擎補一份唯一來源**——那三份漂開的敏感度標籤，起因就是
> 當初有人在畫面上自己寫了一份。

他們說的那件事已經發生過：`SENSITIVITY_LABELS` 是唯一來源，而
`settings_v1.html` 裡有自己寫的一份，兩邊的字已經不一樣了。

**同一個病不要得第二次。**

## 查不到的回代號本身，不丟例外

跟 `outcomes` 那種「不認得就丟例外」刻意不同：那裡漏一種收場會讓使用者
看到一個**畫錯的畫面**（綠燈畫成紅燈）；這裡漏一個廠牌只會讓他看到
`gemini` 而不是「Gemini CLI」——**難看，但不會騙他**。

而丟例外的代價是：某一天多支援一個廠牌，畫面會在使用者打開陣容頁的那一刻
炸掉，而那是最不該炸的時刻。
"""
from __future__ import annotations

from app.i18n import t

# 代號 → 介面文字的 key。**存 key 不存字**：存字的話它會在 import 的當下
# 凍住（同一個形狀 2026-08-24 一天之內犯過三次）。
_NAME_KEYS = {
    "claude": "credential.claude.label",
    "codex": "credential.codex.label",
}


def display_name(provider: str) -> str:
    """這個廠牌在畫面上叫什麼。

    不認得的**回代號本身**，不丟例外也不回空字串——回空字串的話畫面上會
    出現一個沒有名字的下拉選項，而那比顯示 `gemini` 糟得多。
    """
    key = _NAME_KEYS.get(provider.strip().lower())
    return t(key) if key else provider


def known() -> tuple[str, ...]:
    """畫面認得的廠牌代號。順序固定——每次打開位置不一樣的清單很難用。"""
    return tuple(_NAME_KEYS)
