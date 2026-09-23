"""Who is on a workflow line-up, and in what order (ROADMAP P10).

Today a run is "the Claude one writes, the Codex one reviews", and that sentence
is written into the code three times over: `SessionRegistry.VALID_PROVIDERS`,
`RunInfo`'s two named session-id fields, and `Orchestrator.run_claude()` /
`run_codex_review()`, where the method name *is* the role. Vendor and role are
the same thing, so the code cannot express Claude × Claude, and cannot express a
third position at all.

This module separates them. A line-up is an ordered list of

    AgentSlot(role, provider, session_id)

which covers same-vendor pairs and chains of any length in one shape. That
matters beyond tidiness: a fix that only taught the code about "two agents,
possibly the same vendor" would still have no way to say "Claude → Codex →
Claude", and would have to be redone.

**Role is the position; provider is who fills it.** The workflow cares about
`author` / `reviewer` / `finisher`; only the CLI adapter underneath cares
whether that is `claude` or `codex`.

Ordering is meaningful - `slots` is the order the line-up runs in, which is what
makes a relay expressible.

Roles are not a closed set. Three are named here because they are the three the
current workflow has, but a longer chain needs to be able to name its own
positions, and a hard-coded enum would be the same mistake this module exists to
undo, one level up.
"""
from __future__ import annotations

from app.i18n import t

import json
from dataclasses import dataclass, replace
from typing import Any, Iterable, Iterator

# The providers AICC can actually launch. This one *is* a closed set: each entry
# needs a CLI adapter behind it, so an unknown value here is a crash later
# rather than a line-up someone was allowed to compose.
VALID_PROVIDERS = ("claude", "codex")

ROLE_AUTHOR = "author"
ROLE_REVIEWER = "reviewer"
ROLE_FINISHER = "finisher"
# The optional third seat (ROADMAP P10 Stage B, 形狀一). It runs **first** and
# writes nothing: it produces the approach the author then implements.
#
# First rather than last because that is the only position where a new seat
# adds no new failure branch - it sits ahead of every mechanism on the line
# (tests, the decision banner, memory maintenance), so nothing has to answer
# 「它說不行的時候退回誰修」. A finisher at the end would.
ROLE_PLANNER = "planner"

# What the classic two-agent workflow is, said in the new vocabulary.
CLASSIC_ROLES = (ROLE_AUTHOR, ROLE_REVIEWER)
# With the planner seated. The order is the run order, which is why the
# planner is written first here and drawn first on the composer.
PLANNED_ROLES = (ROLE_PLANNER, ROLE_AUTHOR, ROLE_REVIEWER)


class LineupError(ValueError):
    """A line-up that could not be built, with the reason a user could act on.

    `field` 是**讀不出來的那一欄的代號**（`"provider"`／`"duty"` ⋯⋯），不是
    給人看的字。它存在的理由只有一個：讀檔那一層要能講出「這一格是哪一欄
    讀不懂」，而唯一知道答案的是丟這個例外的那個判斷。

    **不要靠比對訊息字串反推。**那等於在第二個地方再判一次同一件事，而兩份
    判準對同一個輸入給不同答案的那一天，沒有任何東西會變紅——這個檔案的
    `with_duty` 已經為了同一個理由寫過一次「只擋一次」。

    存代號不存中文：這個值會被存進畫面狀態、也可能被寫進檔案，而中文當鍵
    的東西一翻譯就對不上（`_LEGACY_PROFILE_NAMES` 就是那個教訓）。
    """

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.field = field


# How a slot's provider was paid for. Not the credential itself - just which
# kind, so a bill can be explained afterwards.
CREDENTIAL_LOGIN = "login"
CREDENTIAL_API_KEY = "api_key"
VALID_CREDENTIALS = ("", CREDENTIAL_LOGIN, CREDENTIAL_API_KEY)

# A seat's custom 「你是⋯⋯」 line is one sentence, not a second task brief.
# Truncated rather than rejected: this is a text box a person types into, and
# losing what they wrote because it was one character over is a worse outcome
# than a shortened job title. The cap exists because the identity is prepended
# to every prompt that seat runs - a thousand-word one would be paid for on
# every call, silently.
IDENTITY_MAX_CHARS = 200

# 一個位子在流程裡**做哪一種事**。
#
# 這是引擎唯一需要知道的東西，而且刻意是封閉集合——因為引擎必須知道要不要
# 給它寫檔權限、要不要跑驗證、要不要把它的輸出當成待審的東西。
#
# **`role` 不受這個限制。**使用者可以把位子叫做「發想」「粗剪」「上字幕」，
# 那是他的事；`duty` 只回答「引擎該怎麼對待它」。兩者分開，是因為
# 【角色表格】那條定案要求角色名可改，而引擎又不能靠猜。
DUTY_PLAN = "plan"        # 想清楚再說，唯讀。與使用者來回確認需求也是這一種。
DUTY_WORK = "work"        # 真的產出東西，**唯一可以寫檔的**。
DUTY_REVIEW = "review"    # 看別人做的，唯讀。
DUTY_APPROVE = "approve"  # 看所有的章能不能拍板，唯讀。

VALID_DUTIES = (DUTY_PLAN, DUTY_WORK, DUTY_REVIEW, DUTY_APPROVE)


# 一格位子**動不動得了使用者專案裡的檔案**。老闆 2026-08-25 的原話：
#
# > 一般使用者像我的話可以自己選擇要不要寫入文件
#
# **這不是職責的一部分，而且刻意不併進去。** 職責回答「這一格在流程裡是
# 什麼」，這一欄回答「它動不動得了我的檔案」。併成一欄的話，唯一關掉寫檔的
# 辦法就是把職責從 `work` 改掉——而 `steps/building.py` 挑人只挑
# `duty == DUTY_WORK`，於是那一格**整個不會被派到那一步**：它不再開口，
# 後面的位子也收不到它本來會說的話。那不是「照跑但不寫檔」，那是「不跑」。
WRITES_INHERIT = ""  # 沒有人被問過這一題 → 照職責的答案（只有 work 寫得了）
WRITES_ON = "on"     # 明著給。**給不出職責沒有的權限**——複查照樣寫不了。
WRITES_OFF = "off"   # 明著關。照跑、照講話、話照樣往下傳，只是動不了檔案。

VALID_WRITES = (WRITES_INHERIT, WRITES_ON, WRITES_OFF)


# 這一格**接在哪一格後面**（老闆 2026-08-22：「有分支或沒分支無所謂，應該要
# 是在一個方框內，有需要才往下增加分支，然後才把方框垂直距離增加放下第二
# 條線」）。左右＝階層，同一階層的分支往下堆，**連線畫在框裡**。
#
# ## 為什麼存「上一格是誰」，不存「第幾層」
#
# 兩者可以互推，但**推不回來的方向正好是他要的那一個**：一個 int 存得下
# 「這兩格同一層」，存不下「這一條線從哪一格拉過來」。而他那句話的重點是
# 框裡的**連線**——三格同層的時候，「第幾層」畫不出那三條線分別接誰。
#
# 反過來，層次由這一欄推得出來（自己的層 = 上一格的層 + 1），所以存 parent
# 是**存那個推不回來的方向**，另一個方向讓它算。存兩欄的話它們會漂，而漂
# 開的那天沒有任何東西會變紅。
#
# ## parent 的代價（環）由另一條規則付掉，不是靠偵測
#
# parent 存得下環（a 接 b、b 接 a），而環在畫面上就是一條畫不完的線。這裡
# **不寫環偵測**——寫了它會是一段永遠走不到的程式（`with_parent` 規定上一
# 格必須排在自己前面，而 `slots` 的順序就是執行順序），而永遠走不到的分支，
# 它的破壞永遠是綠的。**規則比偵測強**：不可能發生，比發生了抓得到好。
PARENT_PREVIOUS = ""  # 沒有人被問過這一題 → 接在它前面那一格（今天那一排）


# 一格位子上，**這一版讀不出來的可能是哪一欄**。代號是英文的：這幾個值會
# 被存進畫面狀態、也會被拿去查該顯示哪一句話，而中文當鍵一翻譯就對不上。
FIELD_ROLE = "role"
FIELD_PROVIDER = "provider"
FIELD_CREDENTIAL = "credential"
FIELD_DUTY = "duty"
FIELD_WRITES = "writes_files"
FIELD_PARENT = "parent"


# 一條接不上的線：**哪一格、想接誰、為什麼接不到**。
#
# 三個欄位都要，因為使用者要修的是「哪一格」，而他能改的是「想接誰」。
# 只講「這份陣容的形狀不合法」等於沒講——他有九格。
LINK_MISSING = "missing"   # 想接的那個名字，這份陣容裡沒有
LINK_FORWARD = "forward"   # 想接的那一格排在它後面（東西還沒做出來）


@dataclass(frozen=True)
class UnreadableSlot:
    """一格**使用者真的排過、而這一版讀不出來**的位子。

    為什麼要留著這個東西，而不是像以前一樣安靜丟掉：丟掉之後，同一份被砍過
    的 `Lineup` 會同時當「容錯之後的結果」與「該有哪些章」的真相來源——那一
    格的簽章要求跟著消失，拍板檢查於是說「一個都不缺」。畫面上少一個節點，
    而沒有任何一句話說少了什麼。

    **`role` 空的那種不會變成這個東西**：一筆連名字都沒有的資料不是使用者
    排的位子，是垃圾，照舊丟。會走到這裡的是「名字讀得出來，只有某一欄這一
    版不認得」——多支援一個廠牌、或是新版寫的檔案被舊版打開，就長這樣。

    `raw` 是那一筆原始 JSON 的**字面**，一個字都沒有動過。存字串而不是存
    dict，是因為這個資料類別要能放進 frozen dataclass 的欄位裡（dict 不能
    雜湊）；而留著原文的理由是 `to_list()` 要把它原封不動寫回去——照這一版
    的形狀重寫，新版多出來的欄位會在寫回的當下消失。
    """

    index: int
    role: str
    field: str
    raw: str

    def entry(self) -> Any:
        """原始的那一筆，原樣。寫回檔案的時候用這個。"""
        return json.loads(self.raw)


def criteria_seat(lineup) -> str:  # noqa: ANN001
    """訂驗收標準的那一格：陣容裡**第一個不做事的節點**。

    為什麼是「第一個」：它是唯一一格在任何東西被做出來之前就會跑的，而驗收
    標準必須在動手之前就存在——事後訂的標準，訂的人已經看過成品了。

    為什麼排除做事的那一格：【驗收標準由複查角色訂】那條定案防的就是自己
    出考卷自己考。整條線只有執行者的陣容**回傳空字串**，不硬挑一格——挑到
    的那一格會拿到「訂標準」的權限，而使用者從來沒有把它排成那個角色。

    放在這裡而不是放在蓋章那一份：它問的是陣容的形狀，跟章沒有關係，而且
    提示詞組裝那邊也要用它（要在提示詞裡寫出「這份標準是誰訂的」）。

    ## 有一格讀不出來的時候，這裡不挑人

    「第一個不做事的節點」這句話的答案，可能就是那一格。跳過它往下挑，等於
    把**訂驗收標準的權限**交給使用者從來沒有排成那個角色的人——而畫面上
    看不出來換過人。這正是上面那條定案要防的東西，只是換一個入口進來。

    `getattr` 而不是 `lineup.unreadable`：這支只吃「一串位子」，測試與呼叫端
    都餵過純 list。改成硬取屬性會讓那些呼叫在讀不到屬性的當下炸掉，而那是
    一個跟這條規則無關的失敗。
    """
    if getattr(lineup, "unreadable", ()):
        return ""
    for slot in lineup:
        if slot.duty != DUTY_WORK:
            return slot.role
    return ""


def chain_checker(lineup) -> str:  # noqa: ANN001
    """驗簽章的那一格。**最後一格**，除非有人被明確排成核准。

    老闆 2026-08-24 拍板：

    > 簽章驗證在 NODE5 啊⋯⋯反正使用者設定 chain 的最後最後一個 NODE 一定要
    > 放入確認簽章的 PROMPT，**或者規定要最後一個 NODE 去驗證**

    所以這是一條**規定**：一條線不管排成什麼形狀，最後那一格都會被問
    「這一整條鏈站不站得住」。

    ## 這條推翻了 2026-08-22 的一條，而且是他親自推翻的

    舊的：「沒有核准那一格的陣容完全不跑這一步，一次呼叫都不多——出廠的一人
    與兩人表格成本一毛都不該變」。

    新的代價講清楚：**沒有排核准那一格的陣容，每一次執行會多一次唯讀呼叫**
    （多等一輪、多吃一次額度）。他 2026-08-24 看過這個代價之後說「套用」。
    換到的是：**跑完之後一定有人說過「這條鏈站得住」**，而不是沒有人說過。

    ## 為什麼這一格可以自動指定，而「執行」「訂標準」那兩格不行

    那兩種職責會給出**權限**：訂標準的決定怎樣算做完，做事的拿到寫檔案的
    能力。使用者沒排就自己挑，等於發出一個他沒有授權的權力。

    **驗簽章不給任何權限**——它唯讀、不重做、推翻不了機器章，說「可以」還得
    寫理由，而它唯一能做的事是「說這條鏈站不住，退給某一格」。
    多問一格「這樣算完成了嗎」，最壞的情況是多一個意見。

    ## 空陣容回空字串

    沒有格子就沒有最後一格。回一個假的名字，派工會派給一個不存在的人。

    ## 有一格讀不出來的時候，「最後一格」這句話不可信

    上面那條「可以自動指定」的推論，前提是**這份陣容就是使用者排的那一份**。
    有一格被讀掉之後前提不成立：真正的最後一格可能正是讀不出來的那一格，
    而這裡會把驗簽章交給往前數的那一位——實測過，那一位就是**做事的那一格**，
    於是變成自己驗自己的鏈，而 `_self_review_role` 只比對驗收標準與執行章，
    抓不到這一種。

    明著排了核准的陣容不受影響：那是使用者親手指定的，不是位置推出來的。
    """
    approvers = [slot.role for slot in lineup if slot.duty == DUTY_APPROVE]
    if approvers:
        return approvers[-1]
    if getattr(lineup, "unreadable", ()):
        return ""
    slots = list(lineup)
    return slots[-1].role if slots else ""


def duty_of(lineup, role: str) -> str:  # noqa: ANN001
    """這一格的職責。查不到回空字串。

    **查不到不是錯**：`run_claude` 這一類有出廠預設的角色名，而使用者的陣容
    裡可能根本沒有那個名字。回空字串讓上層自己決定要怎麼保守處理——這裡
    猜一個職責出來，猜錯的代價是那一格收到不對的規則，而畫面上看不出來。
    """
    for slot in lineup:
        if slot.role == role:
            return slot.duty
    return ""


# 2026-08-22 之前，職責是由角色名推出來的（author 就是寫程式的）。舊的
# sessions.json 裡沒有 duty 這一格，所以照舊的名字推回去——**不推的話，
# 每一個既有專案的陣容都會在升級的當下失去它的職責，而畫面上看起來正常。**
_DUTY_BY_LEGACY_ROLE = {
    "planner": DUTY_PLAN,
    "author": DUTY_WORK,
    "reviewer": DUTY_REVIEW,
    "finisher": DUTY_APPROVE,
}


# 出廠角色的中文名。使用者自訂的名字直接照用——這裡只是不要讓內建的那三個
# 英文代號漏到畫面上。
#
# **住在 `lineup.py`，因為角色代號住在這裡**。2026-08-24 之前有兩份：
# `steps/__init__.py` 一份、`workflow_worker.py` 一份，而前者的註解寫著
# 「放在這裡而不是各扇門各抄一份」——**那句話寫下來之後，第二份還是長出來了**。
# 註解攔不住抄一份，只有「另一個地方沒有這張表」攔得住。
#
# 2026-08-25 合併：這裡本來指向 `seat.*`，而 `role.*` 是另一份一模一樣的
# 四個名字（出廠模式用它排座位）。兩份的字已經開始漂了——`seat.reviewer`
# 是「審查者」而 `role.reviewer` 是「複查」。
# `test_the_catalogue_has_no_accidental_duplicates` 當場點名了這件事，
# 而它的訊息寫對了改法：「真的是同一件事的話，**併成一個 key**」。
#
# 併掉 `seat.author`（「開發工程師」）也順手修掉一個
# 【不是所有使用者都寫程式】：那一格可能在剪一支影片。
#
# 而 `ROLE_FINISHER` 以前**不在這張表裡**，於是它是唯一一個代號會原樣漏到
# 畫面上的出廠角色。
_SEAT_LABEL_KEYS = {
    ROLE_PLANNER: "role.planner",
    ROLE_AUTHOR: "role.worker",
    ROLE_REVIEWER: "role.reviewer",
    ROLE_FINISHER: "role.approver",
}


def shown_name(seat: str) -> str:
    """這一格在畫面上叫什麼。**認不得的照原樣回**——那是使用者自己取的名字。

    這裡的「查不到就用原值」跟【有預設值的分類等於沒有分類】不衝突：
    分類的預設值會把「漏掉一種」藏起來，而這裡查不到本來就是正常情況
    （使用者把「審查者」改叫「Tim」），回原值是**唯一**誠實的答案。
    """
    from app.i18n import t

    key = _SEAT_LABEL_KEYS.get(seat)
    return t(key) if key else seat


@dataclass(frozen=True)
class AgentSlot:
    """One position in a line-up: what it does, who fills it, which session."""

    role: str
    provider: str
    session_id: str = ""
    # Which kind of credential this seat ran on. Recorded because it is only
    # knowable while the run happens: when a bill arrives, "which of these runs
    # were charged to the key" cannot be worked out afterwards from anything
    # else on disk. It costs one string now and is unrecoverable later.
    #
    # Never the key itself.
    #
    # **Empty means unknown, and today it is always empty on purpose.** AICC
    # does not supply a credential yet - the CLI uses whatever it already has,
    # which may be a seat login or an `ANTHROPIC_API_KEY` in the environment
    # that AICC cannot see. Writing "login" for every current run would be a
    # guess dressed as a record. It gets filled by whichever code actually
    # hands a credential over (ROADMAP P12), and by nothing else.
    credential: str = ""
    # What this seat is told it is - the 「你是⋯⋯」 line, replacing the shipped
    # one for **every step this seat runs** (ROADMAP P10 Stage D).
    #
    # Empty means "use the shipped persona", which is also what a cleared box
    # means. That is the one-click restore: there is no separate "reset"
    # state to get out of sync with the text.
    #
    # It replaces the *identity* only. The safety sentences welded to the same
    # opening line in the shipped text (「不得修改任何檔案」) are a separate
    # field in `prompt_builder.StepPersona` and never travel this path.
    identity: str = ""
    # 這個位子做哪一種事。空字串代表「照舊的角色名推」——見
    # `_DUTY_BY_LEGACY_ROLE`。推不出來的一律當成 review：**唯讀是安全的那一邊**，
    # 猜錯成 work 會讓一個不該碰檔案的位子拿到寫入權。
    duty: str = ""
    # 這一格動不動得了專案裡的檔案（`VALID_WRITES`）。**跟 `duty` 是兩欄。**
    #
    # ## 空的那一欄怎麼讀，以及為什麼
    #
    # 2026-08-25 以前寫的 `sessions.json` 裡沒有這一欄，而且那個檔案裡
    # **沒有任何一個地方**記著使用者對這一題的答案——這一版才第一次問他。
    # 所以空字串讀成「還沒有人被問過」，答案照他**已經記下來的**那一欄（職責）走。
    #
    # 空的讀成「不准寫」試過，代價寫在 `DECISIONS.md`【版本號要真的被讀，
    # 而且看不懂的檔案不准蓋】那一條的不變式裡：「舊專案會靜靜地變成不能寫檔，
    # 而畫面上完全正常」。而且這一輪畫面另外交接，**沒有任何一個開關打得開它**
    # ——升級之後每一個既有專案的執行那一格都只會交出一篇說明，
    # 而使用者看到的是「跑完了，一個檔案都沒有變」。
    #
    # ## 那「安全的那一邊」在哪裡：**這一欄只減不加**
    #
    # `may_write_files` 先問職責，`work` 以外一律 False，所以這一欄
    # **永遠給不出**寫入權限，只拿得走。舊檔讀回來之後每一格碰得到的東西
    # 跟升級前一模一樣：複查／規劃／核准照舊碰不到檔案，而使用者親手排成
    # 「執行」的那一格照舊寫得了——**沒有任何一格因為升級而多拿到權限。**
    #
    # 認不得的值不走這條路：`__post_init__` 丟 `LineupError`，那一格變成
    # `UnreadableSlot`，引擎一格都派不動它（也就寫不了任何東西），
    # 而不是被悄悄讀成兩個答案裡的某一個。**看不懂的權限一律當成沒有。**
    writes_files: str = WRITES_INHERIT
    # **這一格接在哪一格後面**（那一格的 `role`）。見 `PARENT_PREVIOUS`。
    #
    # 空字串是「還沒有人被問過這一題」，讀成**接它前面那一格**——所以
    # 2026-08-27 以前寫的 `sessions.json`（裡面根本沒有這一欄）讀回來是一條
    # 直鏈，也就是**今天畫面上那一排**，一個像素都不變。出廠陣容同理：
    # 那幾格一個都沒有設過這一欄。
    #
    # 空的讀成「我是第一層」試過，代價是**每一格都變成一條獨立的線**：
    # 舊專案打開之後不是同一個形狀，而畫面上看起來像九條各自為政的支線。
    # 那正是【看不懂的檔案不准蓋】那條定案否決過的形狀，只是換一個入口。
    #
    # **這一欄不進派工。**這一輪 `slots` 的順序仍然是執行順序，這一欄只回答
    # 「線怎麼畫」。兩件事今天在直鏈上剛好一樣，而它們哪一天要分家，是
    # 老闆的下一個決定，不是這個欄位偷偷做掉的事。
    parent: str = PARENT_PREVIOUS

    @property
    def may_write_files(self) -> bool:
        """這一格這一次**動不動得了專案檔案**。兩欄都點頭才算。

        職責是門，開關是鎖：`work` 以外的職責一格都進不來，而排成 `work`
        的那一格還要這個開關沒有被關掉。

        **合成一句話放在這裡，而不是讓每個呼叫端各問一次兩欄。**兩個地方各
        寫一次 `duty == work and writes != off`，它們漂開的那天沒有任何東西
        會變紅——而漂開的方向有一半是「某一條路忘了看開關」，也就是使用者
        關掉的那一格照樣動了他的檔案。
        """
        if self.duty != DUTY_WORK:
            return False
        return self.writes_files != WRITES_OFF

    def __post_init__(self) -> None:
        role = self.role.strip().lower()
        provider = self.provider.strip().lower()
        if not role:
            raise LineupError(t("lineup.role_required"), field=FIELD_ROLE)
        if provider not in VALID_PROVIDERS:
            raise LineupError(
                f"不支援的 provider：{self.provider!r}"
                f"（目前支援 {'、'.join(VALID_PROVIDERS)}）",
                field=FIELD_PROVIDER,
            )
        credential = self.credential.strip().lower()
        if credential not in VALID_CREDENTIALS:
            raise LineupError(
                f"不認得的憑證種類：{self.credential!r}"
                f"（目前支援 {'、'.join(c for c in VALID_CREDENTIALS if c)}）",
                field=FIELD_CREDENTIAL,
            )
        duty = self.duty.strip().lower()
        if not duty:
            duty = _DUTY_BY_LEGACY_ROLE.get(role, DUTY_REVIEW)
        if duty not in VALID_DUTIES:
            raise LineupError(
                f"不認得的職責：{self.duty!r}"
                f"（目前支援 {'、'.join(VALID_DUTIES)}）",
                field=FIELD_DUTY,
            )
        writes = self.writes_files.strip().lower()
        if writes not in VALID_WRITES:
            # 退回某一個答案不行：這一欄講的是**權限**，而一個讀不懂的權限
            # 被讀成「可以」是無聲的授權，被讀成「不行」是無聲的癱瘓。丟出去
            # 之後這一格變成 `UnreadableSlot`——畫得出名字、派不到工、
            # 也寫不了任何東西，而且原文一個字不動地留在硬碟上。
            raise LineupError(
                t("lineup.unknown_writes_switch", value=self.writes_files),
                field=FIELD_WRITES,
            )
        parent = self.parent.strip().lower()
        if parent and parent == role:
            # **自己接自己**。這一條在這裡擋，不在 `Lineup` 擋，因為它是
            # 這一格自己就答得出來的問題（不用看別人）——而在這裡擋換到的是
            # 一份手改壞的檔案讀回來時，那一格變成 `UnreadableSlot`：名字還
            # 在、原文一個字沒動、派不到工。**看不懂的形狀一律當成沒有。**
            #
            # 「上一格是誰」這一類**要看整份陣容才答得出來**的問題（指到不
            # 存在的格子、指到排在後面的格子）刻意不在這裡問：那兩個問題
            # 在這裡問不出來，而在這裡丟例外的代價是整份陣容變成讀不出來，
            # 使用者看到一個空的編組頁。（`vendor_cannot_write()` 2026-09-01
            # 拿掉了，但它當時寫下的那個理由本身沒有過期。）
            raise LineupError(
                t("lineup.parent_is_itself", role=role), field=FIELD_PARENT
            )
        object.__setattr__(self, "parent", parent)
        object.__setattr__(self, "writes_files", writes)
        object.__setattr__(self, "duty", duty)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "session_id", self.session_id.strip())
        object.__setattr__(self, "credential", credential)
        # Not lower-cased: this one is prose a person wrote, and 「Python」 is
        # not 「python」 to a reader. Only trimmed, so a box holding spaces
        # counts as empty and falls back to the shipped persona.
        object.__setattr__(
            self, "identity", self.identity.strip()[:IDENTITY_MAX_CHARS]
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "provider": self.provider,
            "session_id": self.session_id,
            "credential": self.credential,
            "identity": self.identity,
            "duty": self.duty,
            # 一定要存。少了它，使用者關掉的那一格**下一次讀回來就是開的**
            # ——而 `save()` 每次都走一趟 `from_list` → `to_list`，所以那不是
            # 「換機器才會發生」，是他在陣容頁動任何一格就會發生。
            "writes_files": self.writes_files,
            # 同一個理由。少了它，他拉出來的分支**下一次讀回來就攤成一排**
            # ——而那一排看起來完全正常，正是這一整欄要防的東西。
            "parent": self.parent,
        }

    @classmethod
    def read(cls, data: Any) -> "tuple[AgentSlot | None, str]":
        """(位子, 讀不出來的那一欄)。**兩個都空代表這一筆根本不是一格。**

        分兩種回答，是因為呼叫端要分得出兩件事：

        * **垃圾**——不是 dict、或者連名字都沒有。使用者沒有排過這個東西，
          丟掉不會讓他失去任何他知道自己有的東西。
        * **一格讀不出來的位子**——名字讀得出來，只有某一欄這一版不認得
          （多支援一個廠牌、或新版寫的檔案被舊版打開）。**這一種丟掉就是
          無聲的資料損失**，而定案【看不懂的檔案不准蓋】的不變式寫著
          「使用者失去東西的時候一定要看得到」。

        欄位代號由丟例外的那個判斷自己講（`LineupError.field`），不在這裡
        重判一次——重判的兩份判準會各自演化，而它們吵架的那天不會有任何
        東西變紅。
        """
        if not isinstance(data, dict):
            return None, ""
        try:
            return cls(
                role=str(data.get("role", "")),
                provider=str(data.get("provider", "")),
                session_id=str(data.get("session_id", "")),
                credential=str(data.get("credential", "")),
                identity=str(data.get("identity", "")),
                # 舊檔案沒有這一格。空字串會走 `_DUTY_BY_LEGACY_ROLE` 推回去，
                # 所以既有專案升級之後職責不會消失。
                duty=str(data.get("duty", "")),
                # 同樣沒有這一格，而空字串代表「這一版才第一次問他這一題」
                # ——見 `AgentSlot.writes_files`。**不要在這裡改成別的預設值**：
                # 那個判斷屬於欄位本身，寫兩份的話它們吵架的那天沒有東西會紅。
                writes_files=str(data.get("writes_files", WRITES_INHERIT)),
                # 2026-08-27 以前寫的檔案沒有這一欄。空字串＝「接前一格」，
                # 也就是**今天那一排**——見 `AgentSlot.parent`。
                # **不要在這裡改成別的預設值**：那個判斷屬於欄位本身。
                parent=str(data.get("parent", PARENT_PREVIOUS)),
            ), ""
        except LineupError as exc:
            failed = getattr(exc, "field", "")
            if failed in ("", FIELD_ROLE):
                return None, ""
            return None, failed

    @classmethod
    def from_dict(cls, data: Any) -> "AgentSlot | None":
        """Build a slot from stored JSON, or None if it is not usable.

        Returns None rather than raising: this reads files written by older
        builds and by hand, and one unreadable entry should cost that entry,
        not the whole line-up.

        **這一支講不出「為什麼讀不出來」**，所以讀整份檔案的
        `Lineup.from_list` 不走它，走 `read()`——沒有那個理由的話，那一格
        就是安靜地消失，而那正是 2026-08-24 掃到的那條假綠燈。
        """
        slot, _field = cls.read(data)
        return slot


def name_already_taken(role: str) -> LineupError:
    """「已經有一個位子叫 X 了」。**一句話，三個地方叫。**

    `Lineup.with_node`、`Lineup.renamed`、`lineup_presets.apply_preset` 問的是
    同一件事，而 2026-08-25 之前前兩個各抄了一份一模一樣的句子、第三個根本
    沒問。抄一份的代價不是不整齊：這句話要講清楚**為什麼**同名不行，而兩份
    句子會各自被改——改到只剩一份講得出理由的那天，沒有任何東西會變紅。

    訊息裡刻意帶著理由，因為使用者看到的是「不能取這個名字」，而他有權知道
    那不是介面的任性：整條線是靠名字指人的。

    **住在模組層而不是 `LineupError` 身上，是為了不動上面那幾行的行號。**
    `docs/Graph-Engineering-對照AICC-2026-08-25.md` 用 `檔名:行號` 指
    `criteria_seat`（:167）、`chain_checker`（:198）與 `AgentSlot`（:315），
    而 `tests/test_the_graph_notes_still_point_at_real_code.py` 守著那三個
    數字。往 `LineupError` 裡加東西會把它們整批往下推，換來的是一份**指錯
    地方、而讀起來跟對的一模一樣**的對照表。

    **2026-08-25 那三個數字動過一次**（+17）：`writes_files` 那一欄的常數
    住在模組層、而且必須在 `class AgentSlot` 之前定義。那一輪的處理是把
    文件與 `mutations_graph_notes_point_at_real_code.json` 的 Q1／Q2／Q3
    一起更新——**這條守衛紅了要改的是那三個數字，不是把守衛關掉。**
    """
    return LineupError(
        f"已經有一個位子叫「{role}」了。"
        "整條線是靠名字指人的（派工、駁回、蓋章都是），"
        "同名的話「退給它」會有兩個答案。"
    )


# 會寫檔案的位子配得起哪些廠牌。**今天只有一個。**
#
# 為什麼是一份常數而不是四處各寫一次 `provider != "claude"`：問這件事的入口
# 有四個（換廠牌、改職責、上下移動、套用模式），而 Codex 哪一天真的驗過寫入
# 沙箱旗標，改掉其中一份的人不會知道另外三份在哪——**而那三份的綠燈跟對的
# 綠燈長得一模一樣**。
# ⛔ 這裡 2026-09-01 之前是 `WRITING_PROVIDERS = ("claude",)`，加上
# `vendor_cannot_write()` 與 `writing_provider_names()` 兩支，
# 而畫面三道閘門、三個測試檔、一份破壞配方都掛在它們上面。
#
# 老闆 2026-09-01 看到那個拒絕訊息的反應：
#
#   > 哪個智障說只有 claude 可以寫的，我之前明明就說都可以，
#   > 只是在預設設定的時候說過那個 node 用 codex 剛好只有審閱、
#   > 不需要寫資料而已
#
# **他是對的。** 翻遍全部對話紀錄、`DECISIONS.md`、`WHY.md`——
# 沒有一句是他講的。源頭是 `orchestrator` 裡「Codex 的沙箱寫入旗標
# 沒被驗證過，猜一個等於用沒人選過的權限跑他的 repo」那段註解，
# 也就是**一個還沒填的值**，不是一條規則。
#
# 填上 `-s workspace-write` 之後，`VALID_PROVIDERS` 這兩家都寫得了檔，
# 於是 `vendor_cannot_write()` **永遠回 False**——照這個檔案自己在
# `Lineup.moved()` 那裡立下的規矩，走不到的分支要拿掉：
#
#   「修好之後走不到的分支，它的破壞會永遠是綠的」
#
# 真正還要守的不變式換了一個：**會寫檔的步驟要拿到寫得了檔的沙箱**，
# 而那件事守在 `orchestrator._run_role_prompt` 的沙箱翻譯那一行，
# 由 `tests/test_a_writing_codex_gets_a_writing_sandbox.py` 盯著。


def session_for_new_provider(seated: "AgentSlot | None", provider: str) -> str:
    """換廠牌之後，這一格還留不留得住原本綁的那段對話。**對話 id 屬於廠牌。**

    Claude 的對話 id 拿去給 Codex 續接，續接不到——而**畫面上看不出來**：
    卡片只看 `session_id` 是不是空的來決定要不要畫成「已指定對話」，所以那一格
    會顯示成排好了（標題那一欄是空的，因為掃描結果裡找不到這個 id），
    執行列那顆按鈕也照樣亮著。他按下去才會知道，而那一步是要花錢的。

    **一份，兩個入口叫。**問這件事的地方有兩個——換廠牌的下拉
    （`lineup_web_panel._set_slot_provider`）與套用模式
    （`lineup_presets.apply_preset`）——而 2026-08-26 之前只有前者問過：
    套用一個把某一格從 Claude 換成 Codex 的模式，那一格會**帶著 Claude 的
    對話 id** 被寫回 `.aicc/sessions.json`，一句話都沒有。同一個形狀的東西
    這個檔案已經收過三份（職責、寫檔開關、名字），理由都一樣：
    **抄一份的那一天不會有任何東西變紅。**

    `seated` 是**換之前坐在這一格的人**，`None` 代表這一格本來就沒有人
    （模式加出來的新位子）——那時候沒有東西可以留，回空字串。
    """
    if seated is None:
        return ""
    return seated.session_id if seated.provider == provider.strip().lower() else ""


@dataclass(frozen=True)
class BrokenLink:
    """一條接不上的線。**講得出是哪一格**，不然使用者有九格可以猜。

    `role` 是接不上的那一格，`parent` 是它想接的那個名字（原樣，不做任何
    修正——修正一個名字等於替他決定他想接誰），`reason` 是 `LINK_MISSING`
    或 `LINK_FORWARD`。

    存代號不存中文，理由同 `LineupError.field`：這個值會被推進畫面狀態，
    而中文當鍵一翻譯就對不上。
    """

    role: str
    parent: str
    reason: str


@dataclass(frozen=True)
class LineupShape:
    """框裡的形狀：**每一格接在誰後面、在第幾層、框要多高**。

    ## 解不開的時候，這裡不拼半成品

    `problems` 非空的時候，`upstream`／`layers`／`rows` 回的是**執行順序
    那一排**（每一層剛好一格，也就是今天畫面上的樣子），不是「把接得上的
    那幾條拼起來、接不上的隨便擺」。

    理由是 fail closed：拼出來的那張圖**看起來是他排的**，而其中有幾條線
    是這支程式自己補的。他會照著一張沒有人畫過的圖做事。退回那一排至少
    是一句真話（那就是這一輪的執行順序），而 `problems` 同時指名是哪幾格
    接不上。
    """

    upstream: tuple[str, ...]   # 每一格接在哪一格後面（""＝它是第一層）
    layers: tuple[int, ...]     # 每一格在第幾層（左右）
    rows: int                   # 框的高度＝最多那一層有幾格
    problems: tuple[BrokenLink, ...]

    @property
    def resolved(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class Lineup:
    """An ordered line-up. `slots` is the order it runs in.

    `unreadable` 是**檔案裡有、而這一版讀不出來的那幾格**（見 `UnreadableSlot`）。
    它不是統計數字，是一份要被帶著走的東西：`to_list()` 會把它們原封不動寫
    回去，所以一次「讀進來再存回去」不會把使用者排的位子從硬碟上抹掉。
    """

    slots: tuple[AgentSlot, ...] = ()
    unreadable: tuple[UnreadableSlot, ...] = ()

    def __iter__(self) -> Iterator[AgentSlot]:
        return iter(self.slots)

    def __len__(self) -> int:
        # **只算讀得出來的**。這個數字回答的是「引擎派得動幾格」，而讀不出來
        # 的那幾格引擎一格都派不動。想問「檔案裡有沒有東西」用 `bool()`。
        return len(self.slots)

    def __bool__(self) -> bool:
        # 跟 `len()` 刻意不一樣，而且不一樣的那個情況正是要害：整份都讀不出
        # 來的時候 `slots` 是空的，而 `SessionRegistry._lineup_from_raw` 看到
        # 空的就會**改用出廠的兩人陣容**，然後把它寫回檔案——使用者排的每
        # 一格在那一刻從硬碟上消失，畫面上是一份看起來很正常的預設陣容。
        return bool(self.slots or self.unreadable)

    @property
    def has_unreadable(self) -> bool:
        """這份陣容裡有沒有讀不出來的位子。"""
        return bool(self.unreadable)

    @property
    def unreadable_names(self) -> tuple[str, ...]:
        """讀不出來的那幾格**各自叫什麼**。

        它們的名字是讀得出來的——會走到 `UnreadableSlot` 的前提就是「名字
        讀得出來，只有某一欄這一版不認得」（連名字都沒有的那種當垃圾丟）。
        所以「畫不出來」不等於「不知道它叫什麼」，而**名字正是整條線指人的
        方式**。
        """
        return tuple(lost.role for lost in self.unreadable if lost.role)

    @property
    def taken_names(self) -> tuple[str, ...]:
        """這份陣容裡**已經有人叫的名字**，含這一版讀不出來的那幾格。

        ## 為什麼不是 `self.roles`

        `roles` 只算讀得出來的那幾格，而 2026-08-24 把讀不出來的那幾格留下來
        之後，「檔案裡有哪些名字」跟「畫面上畫得出哪些名字」就不是同一件事
        了。拿 `roles` 當「這個名字有沒有人用」的答案，會讓使用者取到一個
        **檔案裡已經有的名字**——寫回去之後 `.aicc/sessions.json` 裡有兩格
        叫同一個名字，而 `with_node` 自己的訊息早就寫著那會怎樣：「退給它」
        有兩個答案。

        這是「把讀不出來的那一格留下來」換來的新風險：留著它是對的（丟掉是
        無聲的資料損失），但留著之後，每一個**問「這份陣容長什麼樣」**的地方
        都要重新回答一次「你問的是畫得出來的那幾格，還是檔案裡的全部」。
        """
        return tuple(slot.role for slot in self.slots) + self.unreadable_names

    # ---- building ------------------------------------------------------
    @classmethod
    def of(cls, slots: Iterable[AgentSlot]) -> "Lineup":
        return cls(tuple(slots))

    @classmethod
    def classic(cls, claude_session_id: str = "", codex_session_id: str = "") -> "Lineup":
        """The pairing AICC has always run: Claude writes, Codex reviews.

        Kept as a named constructor rather than a default, so that every place
        still assuming it has to say so.
        """
        return cls(
            (
                AgentSlot(ROLE_AUTHOR, "claude", claude_session_id),
                AgentSlot(ROLE_REVIEWER, "codex", codex_session_id),
            )
        )

    @classmethod
    def from_list(cls, data: Any) -> "Lineup":
        """讀一份存起來的陣容。**讀不出來的那幾格會被記下來，不是被丟掉。**

        以前這裡是「讀得懂的留、讀不懂的丟」，而那正是
        `DECISIONS.md`【版本號要真的被讀，而且看不懂的檔案不准蓋】否決清單
        上的第二項：「就是無聲的資料損失」。
        """
        if not isinstance(data, list):
            return cls()
        slots: list[AgentSlot] = []
        lost: list[UnreadableSlot] = []
        for index, item in enumerate(data):
            slot, failed = AgentSlot.read(item)
            if slot is not None:
                slots.append(slot)
                continue
            if not failed:
                # 連名字都沒有，或者根本不是一筆位子。使用者沒排過它。
                continue
            lost.append(
                UnreadableSlot(
                    index=index,
                    role=str(item.get("role", "")).strip().lower(),
                    field=failed,
                    # 原文，不是照這一版的形狀重寫的複本。
                    raw=json.dumps(item, ensure_ascii=False, sort_keys=True),
                )
            )
        return cls(tuple(slots), tuple(lost))

    def to_list(self) -> list[dict[str, str]]:
        """寫回檔案的形狀。**讀不出來的那幾格原樣接回原本的位置。**

        這一行是整條止血線：`SessionRegistry.save()` 會把陣容經過
        `from_list` → `to_list` 來回一趟，而在這之前那一趟每跑一次就少一格
        ——匯入別台機器的專案、以及使用者在陣容頁動任何一格，都會跑到它。

        接回原本的 index 而不是接在最後：順序就是執行順序，把一格搬到隊尾
        是一個使用者沒有下過的指令。
        """
        rows: list[dict[str, str]] = [slot.to_dict() for slot in self.slots]
        for lost in self.unreadable:
            rows.insert(max(0, min(lost.index, len(rows))), lost.entry())
        return rows

    def with_session(self, role: str, session_id: str) -> "Lineup":
        """Return a copy with `role`'s session id replaced."""
        target = role.strip().lower()
        return Lineup(
            tuple(
                # `replace()` 而不是重新建一個：**只換要換的那一格，其餘
                # 一個欄位都不准掉。**
                #
                # 這裡原本是逐個位置參數重建的，於是後來加上的 `duty` 每次
                # 設定 session 就被丟掉一次——2026-08-22 同一個形狀在三個
                # 地方各發生一次（`_lineup_for_run`、這裡、`with_identity`）。
                # 症狀都一樣：執行照跑，只是**跑起來的是別人**，而畫面上
                # 完全看不出來。
                replace(slot, session_id=session_id)
                if slot.role == target
                else slot
                for slot in self.slots
            ),
            self.unreadable,
        )

    def without_sessions(self) -> "Lineup":
        """Return a copy with every seat's session id cleared.

        For carrying a line-up to a different machine (ROADMAP P5). **The
        session ids live here, on the slots** - `sessions.json`'s `selected`
        block is derived from them, not the other way round, so clearing that
        block alone changes nothing and is silently undone on the next load.

        Everything else is kept: who sits where, in what order, each seat's
        `identity` and `credential`. Those are what the user configured and
        they travel fine; only the id of a conversation held on another
        machine's CLI does not.
        """
        return Lineup(
            tuple(replace(slot, session_id="") for slot in self.slots),
            self.unreadable,
        )

    def with_identity(self, role: str, identity: str) -> "Lineup":
        """Return a copy with `role`'s 「你是⋯⋯」 line replaced.

        A blank identity clears it, which is the same thing as "use the
        shipped one" - see `AgentSlot.identity`.
        """
        target = role.strip().lower()
        return Lineup(
            tuple(
                replace(slot, identity=identity)
                if slot.role == target
                else slot
                for slot in self.slots
            ),
            self.unreadable,
        )

    def identity_for(self, role: str) -> str:
        slot = self.for_role(role)
        return slot.identity if slot else ""

    def with_planner(self, provider: str = "codex") -> "Lineup":
        """Return a copy with a planner seated in front.

        Already-seated roles keep their provider and session. An earlier draft
        renamed seats by position (`slots[0]` is always the author), which
        would have turned "add a planner" into "the Claude that was writing is
        now planning, and the Codex that was reviewing now writes" - every
        seat silently reassigned to a different job.
        """
        if self.for_role(ROLE_PLANNER) is not None:
            return self
        return Lineup(
            (AgentSlot(ROLE_PLANNER, provider), *self.slots), self.unreadable
        )

    def without_planner(self) -> "Lineup":
        return Lineup(
            tuple(slot for slot in self.slots if slot.role != ROLE_PLANNER),
            self.unreadable,
        )

    # ---- 任意節點的增減（2026-08-22）--------------------------------------
    #
    # 上面那兩支只處理規劃者，因為當初只有那一個位子是選配的。使用者的原話：
    # 「工作線內無法自由刪減很不方便」——而【角色表格】那條定案的不變式本來
    # 就寫著**角色名與連線都可改**。只能加一種位子，就是「連線不可改」。

    def with_node(
        self,
        role: str,
        provider: str,
        duty: str = "",
        at: int | None = None,
    ) -> "Lineup":
        """插入一個位子。`at` 是插在第幾格前面，None 代表接在最後。

        名字重複會被拒絕（丟 `LineupError`），因為整條線是**靠名字**在指人的
        ——派工、駁回、蓋章、失效範圍全都是。兩個同名的位子會讓「退給複查」
        這句話有兩個答案，而畫面上看不出來。

        **比對的是 `taken_names` 而不是 `roles`**：讀不出來的那幾格也在檔案
        裡，也有名字。只比畫得出來的那幾格，等於讓使用者取到一個檔案裡已經
        有的名字，然後把兩格同名的陣容寫回硬碟——而那正是這一段在防的東西，
        只是從一個看不見的入口進來。
        """
        target = role.strip().lower()
        if not target:
            raise LineupError(t("lineup.slot_needs_name"))
        if target in self.taken_names:
            raise name_already_taken(target)
        fresh = AgentSlot(role=target, provider=provider, duty=duty)
        slots = list(self.slots)
        index = len(slots) if at is None else max(0, min(int(at), len(slots)))
        slots.insert(index, fresh)
        return Lineup(tuple(slots), self.unreadable)

    def renamed(self, role: str, new_name: str) -> "Lineup":
        """改一格的名字。其餘一個欄位都不動。

        名字是使用者取的、引擎不看的那一半（引擎看的是 `duty`）。但它**是**
        整條線指人的方式——派工、駁回、蓋章、討論指名全都用它，所以：

        * 重複的名字要拒絕，理由跟 `with_node` 一樣：「退給它」會有兩個答案。
          **含讀不出來的那幾格**（`taken_names`）——它們也在檔案裡，也有名字。
        * 空的名字要拒絕。一格沒有名字的節點，駁回的時候指不到。

        改名之後**這一次執行已經蓋的章不會跟著改名**——那是刻意的：章記的是
        當時是誰蓋的，事後改名不該改寫歷史。跑到一半改名字會讓拍板檢查說缺
        章，而那句話是對的。
        """
        target = role.strip().lower()
        fresh = new_name.strip().lower()
        if not fresh:
            raise LineupError(t("lineup.slot_needs_name_why"))
        if fresh == target:
            return self
        if fresh in self.taken_names:
            raise name_already_taken(fresh)
        if not any(slot.role == target for slot in self.slots):
            return self
        return Lineup(
            tuple(
                # **接在它下面的那幾格要跟著改名。**線是用名字拉的，改完名字
                # 不改線的話，那幾條線指向一個已經不存在的名字——`shape` 會
                # 把整個框退回那一排，而使用者只是改了一個字。
                #
                # 這一段不需要「這一版讀不出來的那幾格」跟著改：它們的原文
                # 一個字都不動地寫回硬碟（`UnreadableSlot.raw`），改它等於
                # 替一份這一版看不懂的資料重寫內容。**代價要記著**：舊版
                # 寫的那一格如果接著被改名的這一格，它的線會在新版裡斷掉，
                # 而 `shape.problems` 會指名它——看得見，好過安靜改掉。
                replace(
                    slot,
                    role=fresh if slot.role == target else slot.role,
                    parent=fresh if slot.parent == target else slot.parent,
                )
                if slot.role == target or slot.parent == target
                else slot
                for slot in self.slots
            ),
            self.unreadable,
        )

    def with_duty(self, role: str, duty: str) -> "Lineup":
        """改一格的職責。其餘一個欄位都不動。

        **引擎只看這個。**名字是給人讀的，職責決定它會不會被派工、拿不拿得到
        寫入權限、駁回的時候找不找得到它。

        不認得的職責要拒絕而不是退回預設值：退回預設值的話，使用者在畫面上
        選了一個東西、按下去沒有錯誤訊息、而那一格變成別的職責——他不會發現。
        """
        target = role.strip().lower()
        wanted = duty.strip().lower()
        # 不認得的職責由 `AgentSlot.__post_init__` 擋——**只擋一次**。在這裡
        # 再擋一次的話，兩份判準會各自演化，而它們對同一個輸入給不同答案的
        # 那一天，沒有任何東西會變紅。
        if not any(slot.role == target for slot in self.slots):
            return self
        return Lineup(
            tuple(
                replace(slot, duty=wanted) if slot.role == target else slot
                for slot in self.slots
            ),
            self.unreadable,
        )

    def with_writes_files(self, role: str, writes: str) -> "Lineup":
        """開或關一格的寫檔開關。其餘一個欄位都不動。

        **關掉不等於改職責。**改職責的話 `steps/building.py` 整個不會挑到
        那一格，它這一輪一句話都不會說；關掉開關則是照樣派工、照樣講話、
        話照樣接給下一格，只是那一次派工走唯讀那條路。

        認不得的值由 `AgentSlot.__post_init__` 擋——**只擋一次**，理由跟
        `with_duty` 那一行一模一樣：兩份判準會各自演化，而它們對同一個輸入
        給不同答案的那一天，沒有任何東西會變紅。
        """
        target = role.strip().lower()
        wanted = writes.strip().lower()
        if not any(slot.role == target for slot in self.slots):
            return self
        return Lineup(
            tuple(
                replace(slot, writes_files=wanted)
                if slot.role == target
                else slot
                for slot in self.slots
            ),
            self.unreadable,
        )

    def with_parent(self, role: str, parent: str) -> "Lineup":
        """把一格接到另一格後面。其餘一個欄位都不動。空的 `parent` ＝接前一格。

        **這是分支唯一的入口。**老闆 2026-08-22 要的是「有需要才往下增加
        分支」，而「增加一條分支」這件事在資料上就是這一句：把第三格接到第
        一格後面，於是第二格與第三格同一層，框長高一列。

        ## 三種不合法，三句話，而且每一句都指名是哪一格

        * **接自己** —— 這裡擋。
        * **接一個不存在的名字** —— 這裡擋。
        * **接一個排在自己後面的格子** —— 這裡擋。

        第一條 2026-08-27 覆核的時候補在這裡，而它原本寫著「由
        `AgentSlot.__post_init__` 擋」——**那句話對這條路是假的**。`AgentSlot`
        那一層確實也擋（而且必須擋：一份手改壞的檔案讀回來要走
        `UnreadableSlot`），但這一支**根本走不到它**：`wanted == target` 的
        時候 `index_of_role(wanted) == index`，所以會先撞上底下那條「排在
        自己後面」，於是使用者從畫面上把一格接到它自己身上，收到的是
        **「「b」排在「b」後面，先把「b」往前移」**——一句叫他把 b 移到 b
        前面的話。擋對了、話講錯了，而這一頁的規矩是**擋下來要講得出下一
        步**。

        所以這裡是**訊息分流**，不是第二份判準：兩層都認定接自己不合法，
        差別只在**誰在問**——畫面按下去的那一下由這裡答（說一句話、狀態推
        回去），硬碟上讀進來的那一格由 `AgentSlot` 答（那一格讀不出來、原文
        一個字不動）。判準漂開的那天，`test_a_slot_cannot_be_wired_to_itself`
        兩半都會紅。

        最後那一條是這個資料結構的**地基**，不是一條額外的保險：`slots` 的
        順序就是執行順序，而一格不可能收到還沒跑的那一格產出的東西。它同時
        讓**環變成不可能存在**（每一條線都往前指，環要至少一條往後），所以
        這個檔案裡沒有環偵測——見 `PARENT_PREVIOUS`。

        ## 為什麼「這一格不在」是安靜回原樣，而「接不到」是丟例外

        前者跟 `with_duty`／`with_writes_files` 一樣：呼叫端指了一個不存在
        的名字，那是呼叫端的事，不是使用者做錯了什麼。後者是**使用者按下去
        的那一下真的不能做**，而擋下來不講跟沒擋一樣。
        """
        target = role.strip().lower()
        wanted = parent.strip().lower()
        index = self.index_of_role(target)
        if index is None:
            return self
        if wanted:
            # **接自己要先問**，不然它會掉進底下那條「排在自己後面」，而那
            # 句話會變成「「b」排在「b」後面，先把「b」往前移」。見上面。
            if wanted == target:
                raise LineupError(
                    t("lineup.parent_is_itself", role=target), field=FIELD_PARENT
                )
            upstream = self.index_of_role(wanted)
            if upstream is None:
                raise LineupError(
                    t("lineup.parent_not_seated", role=target, parent=wanted),
                    field=FIELD_PARENT,
                )
            if upstream >= index:
                raise LineupError(
                    t("lineup.parent_runs_later", role=target, parent=wanted),
                    field=FIELD_PARENT,
                )
        return Lineup(
            tuple(
                replace(slot, parent=wanted) if slot.role == target else slot
                for slot in self.slots
            ),
            self.unreadable,
        )

    def may_write_files(self, role: str) -> bool:
        """`role` 這一格動不動得了專案檔案。**排不到的位子一律 False。**

        排不到就回 False，跟 `duty_of` 回空字串是同一個保守：這裡猜一個
        「可以」出來，猜錯的代價是一個陣容裡根本不存在的名字拿到寫入權。
        """
        slot = self.for_role(role)
        return slot.may_write_files if slot else False

    def without_node(self, role: str) -> "Lineup":
        """拿掉一個位子。不在裡面就原樣回傳。

        **這裡不設下限。**「至少要兩個」以前寫在畫面那一層，理由是「一個人
        審自己不算審查」——那是對的，但它是**角色表格**該表達的事（一人表格
        由使用者本人訂驗收標準），不是這個資料結構該擋的。

        ## 它下面那一層怎麼辦

        **接到它的上一格**——也就是把線接起來。這不是這支程式挑的一個答案，
        是**今天就已經在發生的那一個**：現在那一排是 a → b → c，拿掉 b 之後
        畫面上是 a → c。分支只是把同一件事寫出來。

        `parent` 是明著寫的那幾格才要改；空的那幾格本來就是「接前一格」，
        清單短一格之後它們自己就對了。

        ## 拿掉第一層那一格，而且有人明著接著它 → 擋下來

        那時候沒有「上一格」可以接。接到它後面的那一格（＝把兩條支線串起來）
        是**替他發明一條他沒有畫過的線**，而畫出來看起來就像他畫的。
        `fail closed`：擋下來，並且**指名是哪幾格**——他要嘛先把那幾格改接
        到別處，要嘛他其實想刪的是別的東西。
        """
        target = role.strip().lower()
        index = self.index_of_role(target)
        if index is None:
            return self
        # 它自己接在誰後面——那就是它下面那幾格改接的對象。
        # **不要寫 `index_of_role(...) or 0`**：第一格的 index 是 0，而 0 是
        # 假值，那個寫法會把「第一格」跟「找不到」讀成同一件事。
        inherits = self.shape.upstream[index]
        orphans = [s.role for s in self.slots if s.parent == target]
        if orphans and not inherits:
            raise LineupError(
                t(
                    "lineup.removing_would_orphan",
                    role=target,
                    orphans="、".join(orphans),
                ),
                field=FIELD_PARENT,
            )
        return Lineup(
            tuple(
                replace(s, parent=inherits) if s.parent == target else s
                for s in self.slots
                if s.role != target
            ),
            self.unreadable,
        )

    def moved(self, role: str, to: int) -> "Lineup":
        """把一個位子搬到第幾格。順序就是執行順序。

        ## 搬動會不會把線弄壞

        會，而且是**唯一**一條不碰任何一格的欄位就能把線弄壞的路：線規定
        只能往前指（`with_parent`），而搬動改的正是「誰在誰前面」。搬完之後
        某一格排到它接的那一格前面，那條線就變成指向一個還沒跑的格子。

        **擋下來，不自動改線。**自動改的話，他排一次順序，AICC 把他畫的
        分支改成另一個形狀——而改完的那張圖看起來就像他畫的。這一頁已經
        為同一個形狀寫過三次「不退回預設值」（`_set_slot_duty`、
        `AgentSlot.writes_files`、`_move_slot`）。

        **出廠陣容與所有舊檔碰不到這一條**：它們的 `parent` 都是空的
        （＝接前一格），而空的那幾條線是跟著順序走的，搬到哪裡都成立。
        它是給**明著拉過線**的陣容用的。

        ## 誰在叫它

        `LineupWebPanel._move_slot`——陣容頁上那兩顆〔◀ 前移〕〔後移 ▶〕，
        2026-08-27 老闆拍板「箭頭要搬的是整個 node」之後接上來的（在那之前
        那一支自己重組 `AgentSlot`，只把坐在位子上的廠牌與對話換過去）。
        **順序這件事只有這一支回答**，畫面那一層不再抄一份判準；它接到的
        `LineupError` 走 `reject()`，所以上面那條線的拒絕是他看得到的一句話，
        不是一坨 traceback。
        """
        target = role.strip().lower()
        slots = list(self.slots)
        found = next((i for i, s in enumerate(slots) if s.role == target), None)
        if found is None:
            return self
        slot = slots.pop(found)
        slots.insert(max(0, min(int(to), len(slots))), slot)
        order = {s.role: i for i, s in enumerate(slots)}
        for index, s in enumerate(slots):
            if s.parent and order.get(s.parent, index) >= index:
                raise LineupError(
                    t("lineup.move_would_break_a_link", role=s.role, parent=s.parent),
                    field=FIELD_PARENT,
                )
        return Lineup(tuple(slots), self.unreadable)

    # ---- 框裡的形狀（2026-08-27）------------------------------------------
    @property
    def shape(self) -> LineupShape:
        """框裡的形狀：每一格接在誰後面、在第幾層、框要多高。

        **算出來的，不是存下來的。**存兩份的話（一份 parent、一份第幾層），
        它們漂開的那天沒有任何東西會變紅——而漂開的樣子是畫面上那條線接到
        A，而層次說它在 B 後面。

        今天的那一排在這裡是 `upstream = ("", 前一格, 前一格⋯)`、
        `layers = (0, 1, 2⋯)`、`rows = 1`——**每一層剛好一格**，也就是老闆
        那句話裡的特例。

        解不開的時候整個退回那一排，並且 `problems` 指名是哪幾格；
        **不拼半成品**，理由寫在 `LineupShape`。
        """
        slots = self.slots
        flat = tuple(
            "" if index == 0 else slots[index - 1].role
            for index in range(len(slots))
        )
        seen: dict[str, int] = {}
        upstream: list[str] = []
        problems: list[BrokenLink] = []
        for index, slot in enumerate(slots):
            if not slot.parent:
                upstream.append(flat[index])
            elif slot.parent in seen:
                upstream.append(slot.parent)
            else:
                upstream.append("")
                problems.append(
                    BrokenLink(
                        slot.role,
                        slot.parent,
                        # 名字在這份陣容裡找得到 → 它排在後面（往回指）；
                        # 找不到 → 這份陣容裡根本沒有那一格。兩件事，
                        # 兩句話：一句叫他改順序，一句叫他改名字。
                        LINK_FORWARD if slot.parent in self.roles else LINK_MISSING,
                    )
                )
            seen[slot.role] = index
        if problems:
            return LineupShape(
                flat, tuple(range(len(slots))), 1 if slots else 0, tuple(problems)
            )
        layer_by_role: dict[str, int] = {}
        layers: list[int] = []
        for index, slot in enumerate(slots):
            above = upstream[index]
            layer = layer_by_role[above] + 1 if above else 0
            layers.append(layer)
            layer_by_role[slot.role] = layer
        tally: dict[int, int] = {}
        for layer in layers:
            tally[layer] = tally.get(layer, 0) + 1
        return LineupShape(
            tuple(upstream), tuple(layers), max(tally.values()) if tally else 0, ()
        )

    # ---- reading -------------------------------------------------------
    def index_of_role(self, role: str) -> int | None:
        """Where `role` sits, or None if it is not seated.

        The index the UI lights while a seat runs. It has to come from the
        line-up itself: any second list of roles kept elsewhere agrees with
        this one only while every project has the same number of seats.
        """
        target = role.strip().lower()
        for index, slot in enumerate(self.slots):
            if slot.role == target:
                return index
        return None

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(slot.role for slot in self.slots)

    def for_role(self, role: str) -> AgentSlot | None:
        target = role.strip().lower()
        return next((slot for slot in self.slots if slot.role == target), None)

    def session_for_role(self, role: str) -> str:
        slot = self.for_role(role)
        return slot.session_id if slot else ""

    def first_for_provider(self, provider: str) -> AgentSlot | None:
        """The first slot filled by `provider`.

        The compatibility direction, and lossy on purpose: once two positions
        can be held by the same vendor, "the Claude one" stops identifying a
        single agent. Anything that needs to know *which* one has to ask by
        role.
        """
        target = provider.strip().lower()
        return next((slot for slot in self.slots if slot.provider == target), None)

    def session_for_provider(self, provider: str) -> str:
        slot = self.first_for_provider(provider)
        return slot.session_id if slot else ""

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(slot.provider for slot in self.slots)

    @property
    def is_same_provider(self) -> bool:
        """True when every position is filled by one vendor.

        Worth knowing at the point of composing a line-up: two sessions of the
        same model share training and blind spots, so a same-vendor pairing has
        to be given genuinely different context to be worth running at all -
        the same context under two different hats loses most of the effect
        (see OPEN_QUESTIONS.md, "Agent Pairing Model").
        """
        providers = set(self.providers)
        return len(providers) == 1 and bool(self.slots)
