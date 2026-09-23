"""「問不出來」不准長得跟「真的登入了」一模一樣。

`_signed_in()` 回的是三態，而它自己的 docstring 寫著這個函式存在的理由：

> `--version` 跑得好好的、清單全綠，然後每一次執行都死在中間。

`True` 跟 `False` 都被好好接住了。**`None` 沒有**——它一路被小心保留到
`_cli_check()`，然後在那裡落進最後一行 `Check(..., Level.OK, version)`，
於是「這支 CLI 的登入狀態問不出來」在畫面上就是**綠燈一顆加一個版本號**。
那正好是上面那句話描述的那個綠燈，被同一個函式自己重現了一次。

## 修法不是把它改成紅燈

`test_a_cli_whose_login_state_cannot_be_read_is_not_called_signed_out`
釘著 `Level.OK`，理由是「不要很有自信地指控一個已經登入的人沒登入」，
**那是對的**：改成 BLOCKED 的話，一個登入正常、只是把子指令改名了的 CLI
會把使用者趕去重登一次，而他重登完狀態還是問不出來。

「不要指控他沒登入」跟「不要謊報綠燈」不是二選一。中間那條路是
**不擋、不指控，但把話說出來**：`Level.OK` 不變，`detail` 從
`2.1.226` 變成 `2.1.226（登入狀態不明）`。

所以這份檔案守的是三件事，缺一件那句話就白講了：

1. 那句話真的有講出來（不然就是原本那顆綠燈）
2. 版本號還在（問不出來不代表把「它跑得起來」也一起丟掉）
3. **真的登入的人不會被貼上這張標籤**（不然這句話變成每一列都有，
   而每一列都有的警告等於沒有警告）
4. **憑證入口那一頁不准把這一列畫成薄荷點**

第 4 件是這份檔案第一版漏掉的那一半，而它漏得很難看出來：`detail` 那句話
加上去之後，上面三條全部是綠的、`pytest` 全過、負向驗證五條全紅。
但那一頁分辨五種列的東西是 `Check.sign_in_unknown`（`setup_state.state_of()`
**一個字都不讀 `detail`**，那是它自己的規格），而 `_cli_check()` 沒有立它。

於是畫面上是：**薄荷點 ＋ 點旁邊一行小字寫著「登入狀態不明」**。
薄荷點的意思逐字是「你可以按開始執行了」，而他先看到的是點。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.i18n import t
from app.services import startup_diagnostics as sd
from app.services.setup_state import SetupState, state_of

VERSION = "claude 2.1.226"


def _a_cli_that_will_not_say(monkeypatch, signed: bool | None) -> sd.Check:
    """PATH 上有、跑得起來，登入狀態由呼叫端指定。

    三個都要 stub。少 stub 一個的話這條測試會去問這台開發機上真正的 CLI，
    而結果取決於跑它的人裝了什麼、登入了沒——**那種測試綠燈的時候不代表
    任何事**（同一個坑 `test_a_working_cli_reports_its_version` 踩過一次）。
    """
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\bin\claude.cmd")
    monkeypatch.setattr(sd, "_tool_version", lambda executable: VERSION)
    monkeypatch.setattr(sd, "_signed_in", lambda executable: signed)
    return sd.check_claude()


def test_a_login_state_that_cannot_be_read_is_not_drawn_as_a_plain_green_version(
    monkeypatch,
) -> None:
    """改壞的樣子：`None` 又落回最後那一行 `Check(..., Level.OK, version)`。

    使用者會看到：跟一台完全正常的機器**一模一樣**的一列——綠燈、版本號、
    沒有任何一個字提到登入。他按下「開始執行」，設定全部付完，然後每一次
    執行都死在中間，訊息是 `Not logged in`。
    """
    check = _a_cli_that_will_not_say(monkeypatch, None)

    assert check.level is sd.Level.OK, "不要指控一個可能已經登入的人沒登入"
    assert not check.blocking, "問不出來不是「壞掉」，不擋他"
    assert t("diag.sign_in_unknown") in check.detail, (
        "「問不出來」被畫成一顆什麼都沒說的綠燈——"
        f"detail 現在是 {check.detail!r}。不擋、不指控，但要把話說出來。"
    )


def test_the_version_survives_saying_i_cannot_tell(monkeypatch) -> None:
    """改壞的樣子：`detail` 只剩「登入狀態不明」，版本號被那句話擠掉了。

    使用者會看到：一列只寫著「登入狀態不明」的 CLI，看不出來它到底裝好了
    沒有。他會去重裝一個裝好好的東西——而重裝完那一列還是長那樣。
    """
    check = _a_cli_that_will_not_say(monkeypatch, None)

    assert VERSION in check.detail, (
        f"版本號不見了（detail={check.detail!r}）。問不出來的是**登入狀態**，"
        "不是「它跑不跑得起來」——那件事這一列已經知道答案了。"
    )


def test_a_cli_that_really_is_signed_in_does_not_get_the_note(monkeypatch) -> None:
    """改壞的樣子：判斷寫成「不是 False 就加註」，於是已登入的也被貼上標籤。

    使用者會看到：兩支 CLI 都寫著「登入狀態不明」，包含他五分鐘前才登入的
    那一支。一個每一列都出現的提醒等於沒有提醒，而下一次它真的出現在需要
    的那一列時，他已經學會不看它了。
    """
    signed_in = _a_cli_that_will_not_say(monkeypatch, True)
    unknown = _a_cli_that_will_not_say(monkeypatch, None)

    assert signed_in.detail == VERSION, (
        f"已經登入的那一列被加了話：{signed_in.detail!r}"
    )
    assert unknown.detail != signed_in.detail, (
        "「問不出來」跟「真的登入了」在畫面上長得一模一樣——"
        "那正是這一整份檔案要消滅的東西"
    )


def test_the_credentials_page_does_not_draw_that_row_as_ready(monkeypatch) -> None:
    """改壞的樣子：那一行只加了那句話，沒有立 `sign_in_unknown=True`。

    使用者會看到：憑證入口那一頁上，那一支 CLI 是一顆**薄荷點**——跟一台
    完全正常的機器同一顆點——旁邊一行小字寫著「登入狀態不明」。而那顆點的
    意思逐字是「你可以按開始執行了」，他先看到的是點，不是那行字。

    這一條跟上面那三條的差別：上面三條驗的是**那句話有沒有講出來**，
    這一條驗的是**那句話有沒有改變畫面**。只有前者的話，三條全綠、
    `pytest` 全過，而那一頁畫的東西一格都沒動。
    """
    check = _a_cli_that_will_not_say(monkeypatch, None)

    assert state_of(check) is SetupState.UNKNOWN, (
        f"憑證入口把這一列畫成 {state_of(check).value}——"
        "`state_of()` 只看 `sign_in_unknown`（它不讀 `detail`，那是它的規格），"
        "所以只加那句話而沒立那個欄位的話，畫面上一格都不會動。"
    )
