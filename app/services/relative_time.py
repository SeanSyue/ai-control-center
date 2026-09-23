"""「多久以前」與「花了多久」寫成中文的地方。**只有這一份。**

## 為什麼會有這個檔案

2026-08-24 搬家搬到最後，`grep 剛剛` 撈出**五個一模一樣的 `_relative()`**：

| 在哪 | 它收什麼 |
|---|---|
| `dashboard_web_panel` | 秒數 |
| `conversation_web_panel` | ISO 字串 |
| `documents_web_panel` | ISO 字串或 epoch |
| `knowledge_web_panel` | `datetime` |
| `main_window._ask_waiting_text` | `time.monotonic()` 的差 |

**收的東西每一個都不一樣，吐出來的字每一個都一樣。**那不是巧合——
它是同一句話被抄了五次，而抄的人每次只需要改前三行。

差別在「怎麼算出秒數」，不在「秒數怎麼講成人話」。所以切在那裡：
**算秒數留在各自的面板**（它們本來就知道自己收到什麼格式），
**講成人話收到這裡**。

## 這件事為什麼值得收

`「3 分鐘前」` 翻成英文是 `"3 minutes ago"`，而 `1` 要變 `"1 minute ago"`
——**英文在這裡會長出一條中文沒有的規則**。五份的話，那條規則要對五次，
而漏掉的那一份不會報錯，它會印出 `"1 minutes ago"`，然後沒有人回報。

同樣的病這個專案今天已經抓到兩次（廠牌顯示名五處、對話來源兩份）。
差別是那兩次是我讀出來的，這一次是搬家的機械動作**逼出來的**——
把字串集中到一個地方，重複的東西自己會撞在一起。

## 兩個家族，刻意不合併

* `describe_age()`——**多久以前**（「3 分鐘前」）
* `describe_span()`——**花了多久**（「3 分鐘」）

差一個「前」字，但它們回答的是不同問題，而且英文差更多（`ago` vs 空）。
合併的話會出現一個 `with_suffix=True` 參數，而那種參數的意思是
「這裡其實有兩個函式」。
"""

from __future__ import annotations

from app.i18n import t

MINUTE = 60
HOUR = 3600
DAY = 86400

# 90 秒而不是 60：一件 61 秒前發生的事講「1 分鐘前」在畫面上是對的，
# 但它會在使用者盯著看的時候從「剛剛」跳走，而那一跳沒有任何資訊。
JUST_NOW_UNDER = 90


def describe_age(seconds: float) -> str:
    """這件事發生在多久以前。未來的時間講「剛剛」，**不編故事**。

    時鐘調過、檔案時間被手動改過都會產生負的差，而畫面上不准出現
    「-3 分鐘前」——那個值是拿來排序與掃視的，一個看起來壞掉的數字
    會讓整張卡片跟著不可信。

    這裡本來有一行 `seconds = max(0.0, seconds)`，**破壞驗證證明它是死的**
    （T2 弄掉它，測試照樣綠）：負數本來就落進下面第一個門檻。
    留著會讓人以為那條規則被那一行保護著，而它其實是被 `< 90` 保護著
    ——真正要動的時候，人會去改錯的那一行。
    """
    if seconds < JUST_NOW_UNDER:
        return t("time.just_now")
    if seconds < HOUR:
        return t("time.minutes_ago", n=int(seconds // MINUTE))
    if seconds < DAY:
        return t("time.hours_ago", n=int(seconds // HOUR))
    return t("time.days_ago", n=int(seconds // DAY))


def describe_span(seconds: float) -> str:
    """這件事花了多久。**不到一分鐘的不進位成「1 分鐘」。**

    進位的話，一個 2 秒就回答完的問題會顯示成「1 分鐘」，
    而那正好是「AI 到底有沒有在想」那一題最在意的區間。

    負數同樣落進第一格（理由同 `describe_age`），所以這裡也沒有夾。
    """
    if seconds < MINUTE:
        return t("time.under_a_minute")
    minutes = int(seconds // MINUTE)
    if minutes < MINUTE:
        return t("time.minutes", n=minutes)
    hours, minutes = divmod(minutes, MINUTE)
    if hours < 24:
        if minutes == 0:
            return t("time.hours", n=hours)
        return t("time.hours_minutes", h=hours, m=minutes)
    days, hours = divmod(hours, 24)
    if hours == 0:
        return t("time.days", d=days)
    return t("time.days_hours", d=days, h=hours)


def describe_clock(seconds: float) -> str:
    """等了多久，**秒還在跳的那一種**（「3 分 07 秒」）。

    只有一個呼叫端：畫面上「它在等你回答」那個計時器。它跟
    `describe_span()` 的差別是**秒不能省**——省掉的話那個數字每分鐘
    才動一次，而使用者會以為畫面當掉了。

    **這裡的夾是真的在做事**，跟上面兩個不一樣：`divmod(-500, 60)` 在
    Python 是 `(-9, 40)`，也就是畫面上會出現「-9 分 40 秒」。上面兩個
    有門檻接住負數，這裡沒有——它直接進 `divmod`。
    """
    total = int(max(0.0, seconds))
    minutes, remainder = divmod(total, MINUTE)
    return t("time.minutes_seconds", m=minutes, s=f"{remainder:02d}")
