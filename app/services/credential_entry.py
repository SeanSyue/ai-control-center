"""憑證入口：AICC 把使用者送到登入那一步，然後**放手**。

老闆 2026-08-24：

> CODEX 和 CLAUDE 那邊基本上**先做以會員優先**。
> 如果一定要保留點什麼，那就把這個跟我討論，可能可以設定特例。

## AICC 一把憑證都不碰，而那不是自我要求，是查出來只能這樣

動工之前去問兩支 CLI 自己（`--help`，不是照文件抄）：

| | 有沒有「交出去就忘掉」的路 |
|---|---|
| **Codex** | ✅ `codex login --with-api-key`——從**標準輸入**讀，讀完進它自己的 store |
| **Claude** | ❌ 它的 `--help` 寫著 auth is **strictly `ANTHROPIC_API_KEY` or `apiKeyHelper`**，兩條都要求**有人一直握著那把 key** |

所以「兩邊都放一個貼 key 的欄位」是錯的：Claude 那邊放了，AICC 就得一直握著
它——而那是【金鑰只給名稱不給內容】那條定案的另一邊。

**會員優先之後這件事變簡單了**：兩邊都是「帶他去登入」，AICC 只準備指令，
不接觸任何憑證。這個模組**沒有任何一個函式收得下一把 key**，而那是刻意的
——收得下就代表某一天會有人存它。

## 為什麼不直接替他跑

登入是 OAuth，要開瀏覽器、要他自己點同意。AICC 代跑不了，也不該代跑——
**一個能替你登入的程式，就是一個握得住你帳號的程式。**
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from app.adapters.no_console import NEW_CONSOLE
from app.i18n import t

from app.services.startup_diagnostics import typeable_name

# 這兩支之外的一律不認。多一支就要先回答「它的憑證怎麼進去、AICC 碰不碰得到」
# ——而那一題答錯的代價是使用者的帳號。
SUPPORTED = ("claude", "codex")


@dataclass(frozen=True)
class LoginRoute:
    """帶他去登入的那一條路。

    `argv` 是**已經分好的引數**，不是一行字串——組字串再拆是這個專案踩過的坑
    （路徑裡有空格的那一天就會拆錯，而拆錯的症狀是「找不到指令」）。
    """

    provider: str
    # 介面文字**存 key 不存字**（`app/i18n.py`）。存字的話這個模組就變成
    # 「介面文字散在 64 個檔案裡」那 1,587 段的其中兩段，而它是 2026-08-24
    # 之後才寫的——新程式碼沒有理由再欠那筆債。
    label_key: str
    argv: tuple[str, ...]
    why_key: str

    @property
    def label(self) -> str:
        return t(self.label_key)

    @property
    def why(self) -> str:
        """為什麼要他自己跑，而不是 AICC 代跑。"""
        return t(self.why_key)

    @property
    def typed(self) -> str:
        """給人看的那一行：他可以自己複製去終端機貼。

        **用到才算，不在 import 的時候算好。**原本這是一個一般欄位，
        在模組載入時就把 `typeable_name()` 的結果凍進去了，而那有兩個後果：

        * 使用者事後用「選擇位置…」指了另一份 CLI，這句話還停在舊的；
        * 測試 monkeypatch 不到它——於是那條守著「Windows 上一定要帶 `.cmd`」
          的檢查（PowerShell 執行原則那個 bug）**會安靜地變成恆真**。

        第二點才是真正的理由：一條測不到的規則等於沒有規則。
        """
        return " ".join((typeable_name(self.argv[0]), *self.argv[1:]))


_ROUTES = {
    "claude": LoginRoute(
        provider="claude",
        label_key="credential.claude.label",
        # `setup-token`：**需要訂閱**，而這正是會員優先那條路。
        # 不是 `auth login`——那一支是互動式的完整登入流程，而他要的是
        # 「我已經有會員，給我一個長效的憑證」。
        argv=("claude", "setup-token"),
        why_key="credential.why.opens_browser",
    ),
    "codex": LoginRoute(
        provider="codex",
        label_key="credential.codex.label",
        argv=("codex", "login"),
        why_key="credential.why.same_as_above",
    ),
}


def route_for(provider: str) -> LoginRoute | None:
    """這一支要怎麼登入。不認得的回 `None`——**不猜一個指令出來**。

    猜出來的指令跑起來會失敗，而失敗訊息跟 AICC 無關，他會以為是自己打錯。
    """
    return _ROUTES.get(provider.strip().lower())


def routes_for_signed_out(signed_in: dict[str, bool | None]) -> list[LoginRoute]:
    """哪幾支要帶他去登入。

    `signed_in` 的值有三種：`True` 已登入、`False` 沒登入、`None` **不知道**。

    **`None` 不算要登入。**它的意思是探測失敗（CLI 裝了但問不出狀態），
    而把「不知道」畫成「沒登入」，會叫一個已經登入的人再登一次——
    那一次他會照做，然後開始懷疑其他每一句話。
    """
    return [
        route
        for provider in SUPPORTED
        if signed_in.get(provider) is False
        and (route := route_for(provider)) is not None
    ]


class CannotStartLogin(RuntimeError):
    """按了「帶我去登入」但那支 CLI 開不起來。

    自成一個型別，因為呼叫端要**畫出來給使用者看**，而不是吞掉。
    吞掉的樣子是：他按了按鈕，什麼都沒發生，然後他再按一次。
    """


def launch_login(route: LoginRoute) -> None:
    """把那支 CLI 開在一個**看得見的**主控台裡，然後放手。

    AICC 到此為止：瀏覽器是那支 CLI 開的、同意是他自己在瀏覽器裡點的、
    憑證進的是它自己的 store。**這個函式從頭到尾沒有碰到任何一把 key，
    也沒有等它跑完**——等它跑完就表示 AICC 要讀它的輸出，而它的輸出裡有碼。

    視窗是刻意的（`SHOWS_A_WINDOW_ON_PURPOSE`）：整個登入流程都發生在那裡，
    藏起來的話他會看到一顆按鈕按下去什麼都沒發生。
    """
    tool, *rest = route.argv
    # `shutil.which` 而不是直接丟 argv[0]：Windows 上這兩支是 npm 放的 `.cmd`，
    # 而「找不到」要在這裡變成一句話，不是一個 FileNotFoundError 追溯。
    resolved = shutil.which(tool)
    if resolved is None:
        raise CannotStartLogin(t("credential.error.not_found", tool=tool))
    try:
        subprocess.Popen(  # noqa: S603 - argv 是本檔案裡的字面值，沒有使用者輸入
            [resolved, *rest],
            creationflags=NEW_CONSOLE,  # SHOWS_A_WINDOW_ON_PURPOSE
        )
    except OSError as exc:
        raise CannotStartLogin(
            t("credential.error.launch_failed", tool=tool, reason=exc)
        ) from exc
