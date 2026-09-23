"""Line-up composer — renders lineup_v1.html (ROADMAP P10).

Replaces the conversation library's pairing stage. The old page's shape - two
columns and a "pair" in the middle - assumed exactly two agents from two
vendors, which is the assumption P10 removed. The conversation lists stay:
they are the source you assign from.

Two numbers on this page are facts read out of the engine, not layout choices,
and both were fabricated once before being given a source:

* **How many seats may be composed** is `Orchestrator.EXECUTED_ROLES`. The data
  model holds a line-up of any length, but only two seats are dispatched, so a
  third would be stored, drawn and never run. The design session had picked 4
  because four fitted the layout - "版面的方便決定了一個關於行為的宣稱".
* **What each seat does** is `Orchestrator.sequence_for()`, because the duties
  swap between the development and planning sequences while the seats do not.
  A copy of those strings in the page would drift, and the page is the more
  convincing of the two.

Seats beyond the executed count are still *shown* when the stored line-up has
them. Hiding data that exists is the same lie pointing the other way, and the
harder one to notice - the page marks them 不會被執行 instead.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Signal

from app.i18n import t

from app.core import prompt_builder
from app.core.lineup import (
    DUTY_WORK,
    IDENTITY_MAX_CHARS,
    LineupError,
    LineupShape,
    ROLE_PLANNER,
    VALID_PROVIDERS,
    AgentSlot,
    Lineup,
    session_for_new_provider,
)
from app.core.orchestrator import Orchestrator
from app.core.lineup import UnreadableSlot
from app.services.lineup_presets import (
    LineupPresetStore,
    PresetError,
    PresetFileTooNew,
    PresetFileUnreadable,
    apply_preset,
)
from app.ui.conversation_web_panel import ConversationWebPanel

# Names for seats as they are added. Past the third there is no established
# vocabulary, so they are numbered rather than invented - and nothing dispatches
# them anyway.
class LineupWebPanel(ConversationWebPanel):
    PAGE = "lineup_v1.html"

    # 他在某一格上按了回覆並送出。`(那一格的名字, 他打的字)`——
    # **名字要帶著**，因為他按得下一顆「暗的」（沒有人在等的那一格）：
    # 老闆 2026-08-27「如果使用者一定要對其他 node 講話的話也可以，
    # 因為專案內容隨時可能會去更改」。收的那一端要知道他講給誰。
    answer_sent = Signal(str, str)
    run_requested = Signal()
    open_documents_requested = Signal()
    # A message the user must see. Applying a mode replaces every seat, so a
    # silent failure here is indistinguishable from 「套用了，只是看起來一樣」
    # - and the next thing that happens is somebody starts a paid run believing
    # a line-up that is not there.

    def __init__(self, project_path: Path | None = None, parent=None) -> None:
        self._lineup = Lineup.classic()
        # 打到一半沒送出去的字，一格一份。**記憶體，不落硬碟**——
        # 落硬碟就要有一個家（`app/services/settings_homes.py` 管的那件事），
        # 而「草稿要不要活過關掉 App」是另一個決定，還沒有人拍過板。
        # 在 `super().__init__()` 之前就要有值：那一行會走到 `set_project()`。
        self._drafts: dict[str, str] = {}
        # 最上面那個框打的字（**搜專案**）。跟下面兩欄搜對話的那個字
        # **分開存**——共用一個的話，他在上面打字會把下面也濾掉。
        self._project_query = ""
        # **一欄一個字。** 老闆 2026-09-01：「兩個框」——他貼的示意圖本來就是
        # 兩欄各一塊，而他要找一段對話的時候是先想「那是我跟 Claude 還是跟
        # Codex 談的」。共用一個字的話，他在左邊打字右邊也跟著少，
        # 而畫面上看不出是誰濾的。
        self._queries: dict[str, str] = {"claude": "", "codex": ""}
        self._running_role = ""
        self._waiting_role = ""
        self._waiting_question = ""
        # 這個專案的檔案裡**有、而這一版讀不出來**的那幾格。見 `_persist()`。
        # 在 `super().__init__()` 之前就要有值：那一行會走到 `set_project()`
        # → `_load_lineup()`，而那支會寫這個欄位。
        self._unreadable: tuple[UnreadableSlot, ...] = ()
        self._presets = LineupPresetStore()
        super().__init__(project_path, parent)

    def set_project(self, project_path: Path) -> None:
        super().set_project(project_path)
        self._lineup = self._load_lineup()
        self.push_state()

    # ---- line-up state --------------------------------------------------
    def _load_lineup(self) -> Lineup:
        if self.registry is None:
            lineup = Lineup.classic()
        else:
            stored = self.registry.load_lineup()
            lineup = stored if stored else Lineup.classic()
        # **讀不出來的那幾格只在這裡讀一次**，之後由 `_persist()` 接回去。
        # 換專案的時候要跟著換——留著上一個專案的那幾格，會把別的專案的位子
        # 寫進這一個專案的檔案。
        self._unreadable = lineup.unreadable
        return lineup

    def _persist(self) -> None:
        """把現在這份陣容寫回硬碟。**讀不出來的那幾格在這裡接回去。**

        以前這件事是每一支改陣容的動作自己記得做的（有一支
        `_keeping_unreadable()` 給人叫）。那個形狀 2026-08-24 當場漏掉兩支：
        `_set_slot_session()` 與 `_set_slot_provider()` 都是
        `Lineup.of(slots)` 從零重建一份，於是**綁一段對話**——陣容頁上最常
        按、五節點測試第一步就要按的那個動作——會把使用者排過、只是這一版
        讀不出來的那一格從 `.aicc/sessions.json` 裡刪掉，而畫面上什麼都不會
        說，也不會有任何一支測試變紅。

        所以改成在**唯一一個寫檔的地方**做。這不是「比較整齊」：叫得到的
        helper 攔不住「下一支忘了叫」，而這個檔案自己的註解已經寫過一次同一
        句話（`lineup.py` 的 `_SEAT_LABEL_KEYS`：「註解攔不住抄一份，只有
        『另一個地方沒有這張表』攔得住」）。

        **它擋不住**「有人不經過 `_persist()` 直接叫 `registry.save_lineup()`」
        ——今天這個面板裡沒有那種呼叫，而那條路走出去的話這裡看不到。
        """
        self._lineup = Lineup(self._lineup.slots, self._unreadable)
        if self.registry is not None:
            self.registry.save_lineup(self._lineup)
        # The execution bar shows a read-only summary of this, so it has to be
        # told - it is the last thing seen before the button that starts
        # spending money.
        self.sessions_bound.emit()
        self.push_state()

    def _hit_for(self, provider: str, session_id: str):
        if not session_id:
            return None
        for hit in self._hits.get(provider, []):
            if hit.session_id == session_id:
                return hit
        return None

    def collect_state(self) -> dict:
        state = super().collect_state()
        state.pop("selected", None)
        # 上面那個 `query` 是「兩欄同一個字」，這一頁沒有那種東西。
        # 留著的話畫面會多一個沒有人讀的欄位，而**一個沒人讀的欄位跟一個
        # 忘了接的欄位在推值上長得一模一樣**。
        state.pop("query", None)
        state["queries"] = dict(self._queries)
        # **送職責，不是送名字。**2026-08-25 之前送的是 `roles`，而那張表
        # 用出廠名字當鍵——使用者一改名字，`jobs` 變成空的，這一頁把**每一格**
        # 都畫成虛線紅框加「不會被執行」，而引擎照跑。
        sequence = Orchestrator.sequence_for(
            self._workflow_mode(),
            duties=tuple(slot.duty for slot in self._lineup),
        )
        state["sequence"] = sequence
        shape = self._lineup.shape
        state["lineup"] = {
            "providers": list(VALID_PROVIDERS),
            # 框的高度＝**最多那一層有幾格**（老闆 2026-08-22 的原話）。
            #
            # 送算好的數字，不讓頁面自己數。頁面自己數的話，「同一層有幾格」
            # 會變成它那一份推論——而這一頁已經為同一個形狀壞過兩次
            #（座位上限、`gate_floor`）。今天這個數字是 **1**：每一層剛好
            # 一格，也就是現在那一排。
            "rows": shape.rows,
            # 接不上的那幾條線，**一格一句，指名道姓**。空陣列＝形狀解得開。
            #
            # 非空的時候，每一格的 `layer` 與 `upstream` 已經退回執行順序
            # 那一排（見 `LineupShape`）——**頁面不要拿接得上的那幾條自己拼
            # 一張圖**，那張圖看起來會像使用者畫的。
            "shape_problems": [
                {"role": bad.role, "parent": bad.parent, "reason": bad.reason}
                for bad in shape.problems
            ],
            # How many seats the engine can seat at all - the same number
            # `_add_slot` refuses to go past, so the page's 「＋ 加一格」 and
            # the backend agree by construction.
            #
            # The page used to derive its cap from `sequence.executed_slots`
            # instead, on the reasoning that a cap should be a fact rather
            # than a setting. True, but that is the wrong fact: it is how many
            # seats *this sequence* has work for, and in the development
            # sequence every seat has work - so `slots >= executed` was always
            # true and the add button was permanently disabled. The third seat
            # Stage B built could not be reached from this page at all.
            "max_slots": self._max_slots(),
            # **「插在這兩格中間」這條路今天走得通嗎。**頁面拿它決定要不要在
            # 每一道縫上畫那顆 ⊕（它自己的註解：「所以這裡**不畫一顆說謊的
            # 按鈕**」）。
            #
            # 送的是**跑出來的答案，不是一個寫死的 True**——理由見
            # `_insert_at_reaches_the_engine`：寫死的那一個會在下一個人把
            # `at` 從這條路上拆掉的那天繼續說是，而那正是這顆按鈕在防的謊。
            "supports_insert_at": self._insert_at_reaches_the_engine(),
            "slots": [
                self._slot_entry(index, slot, shape)
                for index, slot in enumerate(self._lineup)
            ],
            # **這裡刻意還沒有 `unreadable`。**
            #
            # 讀不出來的那幾格已經不會被抹掉了（`Lineup.unreadable`／
            # `_persist()`），但**畫面上還是少一格而沒有一句話說
            # 為什麼**。要補那句話得改三個不屬於這一組的東西：頁面原始檔
            # `AICC-視覺概念/lineup_v1.html`、給使用者看的那句話（`app/i18n.py`），
            # 以及 `tests/test_no_field_is_pushed_into_the_void.py` 的待接清單。
            #
            # 先推一個欄位出去等頁面接，是這個 repo 花過代價的做法：那條
            # 守衛 2026-08-24 當場擋下這一版，而它擋對了——推出去沒人讀的
            # 欄位，跟沒推一模一樣，只是多一個看起來已經做完的假象。
            # Saved modes (ROADMAP P10 Stage E). `active` names the one this
            # project currently matches, or "" - matched on shape rather than
            # remembered, because a remembered name would keep saying 「三格模式」
            # after somebody removed a seat by hand.
            "presets": [
                {
                    "name": preset.name,
                    "description": preset.description,
                    "built_in": preset.built_in,
                    "roles": list(preset.lineup.roles),
                }
                for preset in self._presets.all()
            ],
            "active_preset": self._matching_preset_name(),
            # 老闆 2026-08-27：「把選 claude 和 codex 的地方和 **AI 執行設定的
            # 地方**，像是網站一樣和工作線放在一個頁面，然後滾輪往下就可以
            # 往下看到其他設定之類的呢」。
            #
            # **任務跟設定分開送。**它們節奏差很多：任務是他**每一次執行**
            # 都要打的字，設定是他偶爾調一次的。合成一包的話，畫面那一側
            # 只能把它們放在一起，而那正是它們今天在原生面板裡的問題——
            # 他每次都要用的東西，跟他偶爾調一次的東西，在同一個抽屜裡，
            # 而那個抽屜預設是關的。
            # 現在哪一格在跑（呼吸特效那一格）。**只亮自己，不暗別人**——
            # `.slot.dead`（這次序列跑不到的格子）已經是半透明了，
            # 其他格再變暗的話，兩件完全不同的事會長得一模一樣：
            # 一種是永久的（要去改陣容），一種是幾秒鐘後就換人。
            "running": getattr(self, "_running_role", ""),
            # 在等你回話的那一格。**空的就是沒有人在等**——
            # 畫面不准拿「有沒有 question」自己推一次。
            "waiting": self._who_is_waiting(),
            # 「這一輪」那個下拉的選項。**每一格都一樣**（同一條序列），
            # 所以送在這裡而不是每一格重複一份。
            "duty_choices": self._duty_choices(),
            # 最上面那個框：**搜尋專案**。下面兩欄各自搜對話——
            # 那是兩件事，而它們 2026-08-27 之前擠在同一個框裡。
            "projects": self._projects_entry(),
            # 打到一半沒送出去的那幾段字。**關掉 App 會沒有**——
            # 畫面要說出這件事，不然他會以為存住了。
            "drafts": dict(getattr(self, "_drafts", {})),
            "run_settings": self._run_settings_entry(),
            "task": self._task_entry(),
        }
        return state

    def set_run_state(
        self, *, running: str = "", waiting_role: str = "", question: str = ""
    ) -> None:
        """現在**哪一格在跑**、**哪一格在等你回話**。

        老闆 2026-08-27 兩件：

        > 我希望正在執行的 node 有呼吸特效，然後其他 node 要做出差別
        > 應該要有回覆 XXX ⋯⋯也要能夠讓使用者在該回覆的 node 上面有一個
        > 按鍵去回覆，然後同時其他 node 上的那個按鈕要暗掉

        這兩件陣容頁今天**完全不知道**——執行狀態長在主視窗那一側，
        而這個面板從來沒有被告知過。所以那顆呼吸、那顆亮起來的回覆鈕，
        今天畫不出來不是畫面偷懶，是它手上沒有這個事實。

        **算好的答案送過去，不送半成品。**`waiting_role` 是空字串就是
        「沒有人在等」——畫面不准拿「有沒有 question」自己推一次，
        那會是同一件事的第二個答案。
        """
        self._running_role = running
        self._waiting_role = waiting_role
        self._waiting_question = question
        self.push_state()

    def _who_is_waiting(self) -> dict:
        """在等你回話的那一格。

        **答不出是誰就留空，不准猜。**空的時候畫面那顆鈕寫「回覆」，
        不是編一個名字出來——一個編出來的名字，跟一個對的名字，
        在畫面上長得一模一樣。
        """
        return {
            # **叫 `who` 不叫 `role`**：`slots[].role` 已經有這個名字，
            # 而那一個畫面真的有讀。撞名之後 `test_no_field_is_pushed_into_the_void`
            # 就不認字串比對（那是對的：兩個叫 `role` 的不是同一件事），
            # 於是一個本來好好的欄位被要求逐路徑指認——**那筆帳是下一個人付的**。
            "who": getattr(self, "_waiting_role", ""),
            "question": getattr(self, "_waiting_question", ""),
        }

    def _answer_seat(self, role: str, text: str) -> None:
        """他在某一格上回話。

        ## 空的不送

        跟任務那一邊同一個理由：一句空話送出去，那一格拿到的是一份
        沒有內容的回覆，而它會**照樣往下跑**。

        ## 講給一個沒有在等的格子，是允許的

        老闆 2026-08-27：

        > 如果使用者一定要對其他 node 講話的話也可以，因為專案內容隨時
        > 可能會去更改，但要跳出通知提醒使用者，如果有等待回覆的 node 的話

        **提醒那一句在畫面上**（他按下去、還沒送出之前），不在這裡——
        這一支收到的時候他已經決定了。這裡只做一件事：把名字帶著送上去。
        """
        body = text.strip()
        if not body:
            self.reject(t("answer.empty"))
            return
        self._drafts.pop(role, None)
        self.answer_sent.emit(role, body)
        self.push_state()

    def _projects_entry(self) -> dict:
        """最上面那個框：**搜尋專案**，不是搜尋對話。

        老闆 2026-08-27：

        > 最上面的對話框，我希望做的是搜尋專案，邏輯上比較接近
        > AICC、codex、claude 的專案資料夾。下面選對話的是在搜尋對話框，
        > 這個概念比較不一樣。

        ## 他抓到的是真的：那個框今天同時回答兩個問題

        `conversation_search.score_session()` 比對**兩樣**——對話標題
        （`compact_text(session.title)`）與**專案路徑**（`str(session.project_path)`）
        ——而那個框的提示字就寫著「搜尋對話名稱或專案路徑…」。

        > **一個框回答兩個問題。**而他是從使用者那一端看出來的：
        > 「我要找哪個專案」跟「我要接哪個對話」在他腦中是分開的兩件事。

        所以拆開：**最上面搜專案**，**下面那兩欄各自搜對話**。

        ## 比什麼

        專案的**名字**與**路徑**，不分大小寫。比不到就回空清單——
        **不准回全部**：一個「查不到就給你全部」的搜尋框，
        跟一個壞掉的搜尋框長得一模一樣。
        """
        from app.services.project_registry import ProjectRegistry

        try:
            entries, _ = ProjectRegistry().load()
        except Exception:
            # 讀不到就說沒有，不要猜。清單型的東西**假的一筆比少一筆貴**。
            return {"typed": self._project_query, "found": []}

        needle = self._project_query.strip().casefold()
        found = []
        for entry in entries:
            path = str(getattr(entry, "path", "") or "")
            name = str(getattr(entry, "name", "") or "") or Path(path).name
            if needle and needle not in name.casefold() and needle not in path.casefold():
                continue
            found.append({
                "name": name,
                "path": path,
                # 這一個是不是他現在打開的那個。畫面靠它標，
                # **不要拿名字自己比**——同名的資料夾是存在的。
                "current": bool(self._project and Path(path) == self._project),
            })
        return {"typed": self._project_query, "found": found}

    def _duty_choices(self) -> list[dict]:
        """「這一輪」那個下拉裡有哪幾項。

        老闆 2026-08-27：

        > 能不能把這一輪 XX 的 XX 用下拉式選單就好?

        在這之前那張卡片上是兩列：`職責 [執行▾]`（改得動）與
        `這一輪：動手做`（改不動，而它就是上面那一格的結果）。
        **兩列、同一件事、一個能改一個不能改。**合成一列。

        ## 選項**跟著序列走**，這是刻意的

        計畫序列裡引擎只跑兩種工作（`SEQUENCE_JOBS["planning"]["jobs"]` 只有兩項），
        所以那個下拉只會有兩項。今天他在計畫序列裡選得到「拍板」，
        而 `_run_planning` **一次都不會派工給它**。

        > **一個可以選、而選了不會發生的選項，比沒有那個選項貴。**
        > 沒有他會去問；有他不會。

        `id` 是送回引擎的值（`set_slot_duty` 收的那個），`text` 是畫面上的字
        （就是「這一輪：⋯」那一句）。
        """
        jobs = Orchestrator.SEQUENCE_JOBS[self._workflow_mode()]["jobs"]
        return [{"id": duty, "text": t(job_key)} for duty, job_key in jobs.items()]

    def _run_settings_entry(self) -> dict:
        """這一次要怎麼跑：那 22 個控制項的**現值**與**可選項**。

        ## 為什麼可選項要一起送

        2026-08-27 交接寫著：

        > **每一個控制項的可選項一定要一起推。** 這一頁已經為同一個形狀壞過
        > 兩次（座位上限、`gate_floor` 那張手抄的表）——畫面自己寫一份選項
        > 清單，引擎改了之後畫面會繼續提供一個已經不存在的選擇。

        選項宣告在 `app/services/run_settings_options.py`，而
        `tests/test_the_web_and_the_native_panel_offer_the_same_choices.py`
        把它跟**真的開起來的原生面板**釘在一起。

        ## 那一行摘要不在這裡拼

        `app/services/run_summary.py::summary_line` 是唯一的來源，原生面板
        讀的是同一支。畫面自己拼的話，那句話會在引擎多一個欄位的那天少講
        一件事，而**它看起來完全正常**。
        """
        from app.services.ai_settings import (
            PROFILE_CUSTOM,
            AISettingsStore,
            profile_id,
        )
        from app.services.run_summary import summary_line
        from app.services import run_settings_options as opts
        from app.services import ui_preferences

        settings = AISettingsStore(self._project).load_project()
        values = settings.to_dict()

        # **一格一筆，不是三棵平行的子樹。**
        #
        # 第一版推的是 `values` / `choices` / `spins` / `toggles` 四棵，
        # 於是同一個設定名（`claude_model` ⋯⋯）在三棵裡各出現一次——
        # 80 個葉子有 65 個撞名，而 `test_no_field_is_pushed_into_the_void`
        # 遇到撞名就不認字串比對（那是對的：兩個叫 `label` 的東西不是同一件事）。
        #
        # 它連帶讓一個**畫面真的有讀**的欄位（`sequence.label`）被判成孤兒。
        # 那不是守衛太嚴，是我的形狀讓「這個名字指誰」變得答不出來。
        #
        # 一格一筆之後，畫面那一側也好接：跑一次迴圈就畫得完，
        # 不必自己去三棵樹裡把同一格的三件事湊起來。
        # **每一筆帶同樣的鍵**，用不到的給空值。
        # `test_no_field_is_pushed_into_the_void` 只取陣列的**第一筆**當樣本
        # （它自己的 `_leaf_paths` 寫著 `value[0]`），所以形狀不一致的話，
        # 後面那幾種控制項獨有的欄位它一輩子看不到——**而看不到就等於
        # 沒有人守著它們有沒有被接上**。
        def _control(key: str, kind: str, now: object, **extra) -> dict:
            entry = {
                "key": key,
                # 這一格**自己的名字**。畫面只拿得到 `key`，而 `claude_model`
                # 不是一句人話——沒有這個欄位，那 22 格會變成一排原始鍵名。
                #
                # **不讓畫面自己抄一份**：`run_settings_options.LABELS` 是唯一
                # 一份，原生面板也讀它（`label_for()`）。兩個地方各有一份的
                # 那天它們會不一樣，而畫面上看起來完全正常——這一頁已經為
                # 手抄的表壞過兩次（座位上限、`gate_floor`）。
                "label": opts.label_for(key),
                "kind": kind,
                "now": now,
                "options": [],
                "note": "",
                "min": 0,
                "max": 0,
                "suffix": "",
            }
            entry.update(extra)
            return entry

        controls: list[dict] = []
        for key in opts.CHOICES:
            controls.append(_control(
                key, "choice", values.get(key),
                options=[
                    {"id": value, "text": label}
                    for value, label in opts.labelled(key)
                ],
            ))
        for key, (lo, hi, suffix_key) in opts.SPINS.items():
            controls.append(_control(
                key, "number", values.get(key),
                min=lo, max=hi, suffix=t(suffix_key) if suffix_key else "",
            ))
        for key, label_key in opts.TOGGLES.items():
            controls.append(_control(
                key, "toggle", bool(values.get(key)),
                # **整句都要**：括號裡那半句講的是代價，而代價是他決定
                # 勾不勾的依據。縮寫掉他就看不到那件事要花什麼。
                note=t(label_key),
            ))

        return {
            "summary": summary_line(settings),
            # 展開狀態跟原生面板**同一個鍵**——他在原生那邊收起來、
            # 切到網頁又是開的，那是同一個設定的第二個答案。
            "expanded": bool(
                ui_preferences.store().value(
                    "ai_settings_panel/expanded", False, type=bool
                )
            ),
            "profiles": opts.profiles(),
            # **現在這一組是哪一張**（代號；三張都不是就是空的）。
            # 空的時候畫面寫「這一組跟三張都不一樣」，**不是寫「自訂」**——
            # 「自訂」那五個字誤導過老闆一次（陣容頁的模式那一格，
            # 讀起來像一道要先過的門）。
            "profile": (
                profile_id(settings.profile_name)
                if profile_id(settings.profile_name) != PROFILE_CUSTOM
                else ""
            ),
            "controls": controls,
        }

    def _task_entry(self) -> dict:
        """他這一次要交辦的那段字，以及**它會被寫進哪一個檔**。

        計畫序列寫 `planning-request.md`，開發序列寫 `task.md`。
        **檔名要送過去**：畫面上看不出現在會寫哪一個的話，他會以為
        自己改的是同一份（2026-08-27 交接 §四第 2 條）。

        存檔仍然是**明著按一下**（`set_task`），不是打字就存。改成打字就存
        是行為改變，而那件事沒有人拍過板。
        """
        from app.services.ai_settings import AISettingsStore

        try:
            planning = AISettingsStore(self._project).load_project().workflow_mode == "planning"
        except Exception:
            planning = False
        name = "planning-request.md" if planning else "task.md"
        path = self._project / ".aicc" / name
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            text = ""
        return {"text": text, "file": name}

    def _matching_preset_name(self) -> str:
        """Which mode this project's line-up currently *is*, if any.

        Derived, never stored. A stored name would be a second answer to
        「這個專案是什麼模式」, and it would be the wrong one the moment anyone
        edits a seat - the page would still say 「三格模式」 over two seats.

        ## 有一格讀不出來的時候，這裡不報任何模式

        比對的材料是 `self._lineup.roles`，而它**只算讀得出來的那幾格**。
        使用者排的三人陣容裡「核准」那一格因為廠牌這一版不認得而畫不出來，
        剩下的兩格剛好就是出廠【兩人】的形狀——於是模式下拉選單會停在
        「兩人：執行 → 複查」，**斬釘截鐵地告訴他這個專案是兩人模式**。
        那是一句假話，而且它比「少畫一格」更難發現：少一格他可能會覺得
        自己記錯，而下拉選單上的那個名字看起來是系統算出來的事實。

        回空字串的話，頁面會顯示「都不符合」那一項——那是誠實的：這條線
        確實不符合任何一個存好的模式，它有一格哪個模式都沒有。

        **這一條擋不住**「畫面上沒有一句話說少了一格」。要講出那句話得動
        頁面原始檔（`AICC-視覺概念/lineup_v1.html`）與 `app/i18n.py`。
        """
        if self._lineup.has_unreadable:
            return ""
        for preset in self._presets.all():
            if preset.lineup.roles != self._lineup.roles:
                continue
            if [s.provider for s in preset.lineup] != [
                s.provider for s in self._lineup
            ]:
                continue
            return preset.name
        return ""

    def _workflow_mode(self) -> str:
        """Which sequence is configured, so the seat duties match it."""
        try:
            from app.services.ai_settings import AISettingsStore

            if self._project is None:
                return "development"
            return AISettingsStore(self._project).load_project().workflow_mode
        except Exception:
            return "development"

    def _slot_entry(self, index: int, slot: AgentSlot, shape: LineupShape) -> dict:
        hit = self._hit_for(slot.provider, slot.session_id)
        entry = self._entry(hit) if hit is not None else {}
        return {
            # 這一格在**第幾層**（左右），以及它**接在哪一格後面**（框裡的
            # 那條線）。老闆 2026-08-22：「有分支或沒分支無所謂，應該要是在
            # 一個方框內，有需要才往下增加分支」。
            #
            # **送算好的答案，不送 `slot.parent` 那個字面值。**字面值的空字串
            # 是「還沒有人被問過這一題」，而「那到底接誰」會變成頁面自己那
            # 一份推論——這一頁已經為同一個形狀壞過兩次。這裡的 `upstream`
            # 是解出來的：空字串**只有**在「這一格就是第一層」的時候才是空的。
            #
            # 同一層的往下堆，順序就是 `slots` 這個陣列的順序——**沒有另外
            # 送一個「第幾列」**，那會是同一件事的第二個答案。
            "layer": shape.layers[index],
            "upstream": shape.upstream[index],
            "role": slot.role,
            "provider": slot.provider,
            "session_id": slot.session_id,
            # 這一格做哪一種事。**畫面要推得到它才畫得出那個下拉**——
            # 2026-08-22 之前這裡沒有這一欄，於是引擎收得到職責、頁面卻連
            # 現在是什麼職責都不知道，那個控制項做不出來。
            "duty": slot.duty,
            "title": entry.get("title", ""),
            "updated_rel": entry.get("updated_rel", ""),
            # Locks the provider dropdown: a seat that writes can only be one
            # AICC can grant write access to, which today is Claude alone.
            "can_write": slot.duty == DUTY_WORK,
            # 這一格這一輪**動不動得了他的檔案**（老闆 2026-08-25：「一般
            # 使用者像我的話可以自己選擇要不要寫入文件」）。
            #
            # 送**算好的答案**，不送 `slot.writes_files` 那三個字面值。
            # 送字面值的話「空字串是什麼意思」會變成頁面自己那一份推論，
            # 而這一頁已經因為同一個形狀壞過兩次（座位上限、`gate_floor`）。
            # 頁面要畫的是一個勾選框，它需要的就是勾或不勾。
            #
            # 跟上面那一欄不是同一件事：`can_write` 說「這一格**准不准**動
            # 檔案」（職責決定，鎖的是廠牌下拉），這一欄說「這一輪**會不會**
            # 動」。職責是 work 而開關關著的時候，兩者一個 True 一個 False。
            "writes_files": slot.may_write_files,
            # Whether 移除 does anything on this seat. Only the optional
            # planner can go: dropping the author or the reviewer would leave
            # a line-up the engine has no shape for, so the backend refuses
            # it - and a button that is offered and then refused is worse than
            # one that is visibly unavailable. The page still owns whether to
            # disable it; this is the fact it needs to.
            # 2026-08-22：每一格都刪得掉，只剩「不准刪到零格」。
            #
            # 以前是「只有選配的規劃者能刪，而且至少留兩格」。那條下限擋掉了
            # 【出廠三張】明著給的一人表格——使用者連一人模式都排不出來。
            #
            # 這一格必須跟 `_remove_slot` 講同一句話：畫面說得動、後端卻拒絕，
            # 比一顆明顯按不動的按鈕更糟。
            "can_remove": len(self._lineup) > 1,
            # 使用者寫的「你是⋯」。跟上面的 `duty` 同一個理由：改得了但
            # 看不到現值，等於改不了——畫面要收得到現值，才畫得出那個輸入框。
            #
            # 2026-08-27：這一欄以前在這份 dict 裡出現**兩次**（`duty` 底下
            # 一份、這裡一份），兩份送同一個值，所以 payload 一直是對的——
            # 而後面這一份會安靜地蓋掉前面那一份。**改前面那一份不會有任何
            # 一條測試變紅。**
            #
            # 那不是理論。`mutations_seat_name_and_duty.json` 的 P7 標籤寫著
            # 「職責與身分沒有推給頁面」，而它砍的正是前面那一份：測試會紅，
            # 紅的是 `duty`，`identity` 那一半**從來沒有被守到過**。量過：
            # 只砍前面那一份，24 passed，連 UI payload 指紋都一個位元沒動。
            # 所以那條配方在這一輪拆成兩條，一條守一欄。
            #
            # ROADMAP P10 Stage D. `identity` is what the user typed ("" means
            # they have not); `shipped_identities` is what an empty box falls
            # back to.
            #
            # A **list**, because one seat runs several steps and they do not
            # share a job title - the author is 開發工程師 while writing and
            # 共同記憶審查者 while checking a memory draft. A box showing only
            # the first would be a promise the engine does not keep, so the
            # page is given all of them and can say how many it replaces.
            "identity": slot.identity,
            "shipped_identities": list(
                prompt_builder.shipped_identities_for_role(slot.role)
            ),
            "identity_max": IDENTITY_MAX_CHARS,
            # The second axis of the independence reading. Vendor alone is a
            # coarse proxy - two Claudes on different material can be more
            # independent than two vendors reading the same repo.
            "in_project": bool(entry.get("in_project", False)),
        }

    # ---- actions --------------------------------------------------------
    def handle_action(self, kind: str, arg: str) -> None:
        if kind == "run":
            self.run_requested.emit()
            return
        if kind == "open_documents":
            self.open_documents_requested.emit()
            return
        if kind == "add_slot":
            # **`arg` 要帶進去。**這一行 2026-08-27 之前寫的是 `self._add_slot()`
            # ——頁面每一道縫上那顆 ⊕ 送的「插在第幾格前面」在這裡整個掉了，
            # 於是「插在這兩格中間」跟「加在最後」是同一件事，而畫面上看起來
            # 完全正常（新的那一格出現了，只是出現在別的地方）。
            self._add_slot(arg)
            return
        if kind == "remove_slot":
            self._remove_slot(self._index(arg))
            return
        if kind == "set_slot_identity":
            index, value = self._split(arg)
            self._set_slot_identity(index, value)
            return
        if kind == "set_slot_role":
            index, value = self._split(arg)
            self._set_slot_role(index, value)
            return
        if kind == "set_slot_duty":
            index, value = self._split(arg)
            self._set_slot_duty(index, value)
            return
        if kind == "set_slot_writes":
            index, value = self._split(arg)
            self._set_slot_writes(index, value)
            return
        if kind == "set_slot_parent":
            index, value = self._split(arg)
            self._set_slot_parent(index, value)
            return
        if kind == "apply_preset":
            self._apply_preset(str(arg).strip())
            return
        if kind == "save_preset":
            self._save_preset(str(arg).strip())
            return
        if kind == "set_slot_provider":
            index, value = self._split(arg)
            self._set_slot_provider(index, value)
            return
        if kind == "set_query":
            # 這一頁是**兩個框**，但父類別那個「兩欄同一個字」的動作不准變成
            # 一個設了沒人讀的欄位——**一個什麼都不做的動作，跟一個做對了的
            # 動作，在畫面上長得一模一樣**（這一頁今天才為那個形狀誤判過一次：
            # 我以為 `set_query` 沒人收，而它一直是活的）。
            # 收到就兩欄一起設。
            text = str(arg)
            if all(v == text for v in self._queries.values()):
                return
            self._queries = {name: text for name in self._queries}
            self.rescan()
            return
        if kind == "set_provider_query":
            self._set_provider_query(str(arg))
            return
        if kind == "find_projects":
            # **只換這一個字，不重掃對話。**兩個框各自的字要分開存：
            # 共用一個的話，他在上面打「AICC」會把下面那兩欄也濾掉，
            # 而畫面上看不出是誰濾的。
            self._project_query = str(arg)
            self.push_state()
            return
        if kind == "set_slot_session":
            index, value = self._split(arg)
            self._set_slot_session(index, value)
            return
        if kind == "move_slot":
            index, value = self._split(arg)
            self._move_slot(index, 1 if value.lstrip("+") == "1" else -1)
            return
        if kind == "set_run_setting":
            key, _, value = arg.partition(":")
            self._set_run_setting(key.strip(), value)
            return
        if kind == "apply_run_profile":
            self._apply_run_profile(str(arg))
            return
        if kind == "save_run_defaults":
            self._save_run_defaults(arg.strip())
            return
        if kind == "set_task":
            self._set_task(arg)
            return
        if kind == "answer_seat":
            role, _, text = arg.partition(":")
            self._answer_seat(role.strip(), text)
            return
        if kind == "set_draft":
            # **暫存不送**（老闆 2026-08-27 加的）。打到一半想去看別的，
            # 回來那段字還在。存在記憶體裡，不落硬碟——落硬碟就要有一個家
            # （`settings_homes.py`），而那是另一個決定。**關掉 App 會沒有，
            # 這件事畫面要說出來**，不然他會以為存住了。
            slot, _, text = arg.partition(":")
            self._drafts[slot.strip()] = text
            self.push_state()
            return
        if kind == "toggle_run_settings":
            from app.services import ui_preferences

            # **同一個鍵**，原生面板讀的就是它。分兩個鍵的話，他在原生
            # 那邊收起來、切到網頁又是開的——同一個設定的第二個答案。
            ui_preferences.store().setValue(
                "ai_settings_panel/expanded", arg.strip() == "1"
            )
            self.push_state()
            return
        super().handle_action(kind, arg)

    def _set_run_setting(self, key: str, raw: str) -> None:
        """改這一次要怎麼跑的其中一格。

        ## 認不得的鍵或值一律擋，**不准掉進 else**

        這是搬家最容易出的事：畫面提供一個引擎不認得的選擇，他選了它，
        送回來沒有人接，**然後什麼都沒發生**——而畫面上那一格看起來已經
        改好了。所以這裡兩件都查：鍵在不在、值在不在那份選項裡。

        選項來自 `app/services/run_settings_options.py`，也就是畫面收到的
        同一份。兩邊漂開會被
        `tests/test_the_web_and_the_native_panel_offer_the_same_choices.py` 擋。
        """
        from dataclasses import replace as _replace

        from app.services.ai_settings import AISettingsStore
        from app.services import run_settings_options as opts

        field = opts.SETTING_FIELD.get(key, key)
        store = AISettingsStore(self._project)
        settings = store.load_project()
        if not hasattr(settings, field):
            self.reject(t("run.unknown_setting", key=key))
            return

        if field in opts.CHOICES:
            allowed = {str(v): v for v, _label in opts.CHOICES[field]}
            if raw not in allowed:
                self.reject(
                    t("run.no_such_choice", key=key, value=raw)
                )
                return
            value: object = allowed[raw]
        elif field in opts.SPINS:
            lo, hi, _suffix = opts.SPINS[field]
            try:
                number = int(raw)
            except ValueError:
                self.reject(t("run.not_a_number", key=key, value=raw))
                return
            if not lo <= number <= hi:
                self.reject(t("run.out_of_range", key=key, lo=lo, hi=hi, got=number))
                return
            value = number
        elif field in opts.TOGGLES:
            value = raw.strip() == "1"
        else:
            self.reject(t("run.unknown_setting", key=key))
            return

        store.save_project(_replace(settings, **{field: value}))
        self.push_state()

    def _query_for_scan(self) -> dict:
        """陣容頁是**兩個框**，所以一欄一個字。

        回 dict，`ConversationScanWorker` 認得——**比對還是 Python 做的**，
        `score_session()` 連對話內容都比得到，而畫面自己濾只能比標題。
        """
        return dict(self._queries)

    def _set_provider_query(self, raw: str) -> None:
        """某一欄的搜尋字：`"<claude|codex>:<他打的字>"`。

        ## 為什麼不沿用 `set_query`

        那一個是「兩欄同一個字」，對話頁還在用。這一頁多一個動作，
        而不是把同一個名字拿去講兩件事——**兩個叫同一個名字的東西不是同一
        件事**，這個 repo 的守衛自己就是這樣講的。

        認不得的廠牌一律擋，**不准掉進 else**：一個引擎不認得的值送回來
        沒有人接，然後什麼都沒發生，而畫面上那個框看起來已經在搜了。
        """
        provider, _, text = raw.partition(":")
        provider = provider.strip()
        if provider not in self._queries:
            self.reject(t("run.unknown_provider", value=provider))
            return
        if self._queries[provider] == text:
            # 同一個字重掃一次要 0.7 秒，而畫面不會有任何不同。
            return
        self._queries[provider] = text
        self.rescan()

    def _apply_run_profile(self, wanted: str) -> None:
        """套用一張出廠表格（省額度／標準／深入）。

        ## 收的是代號，不是那三個中文字

        `AISettingsStore.profile()` 本來就吃代號。收中文的話，英文版上線
        那天三張表一張都對不上——**而畫面看起來完全正常**。
        認不得的一律擋，不准掉進 `else`：一個引擎不認得的值送回來沒有人接，
        然後什麼都沒發生，而畫面上那一格看起來已經改好了。

        ## ⚠️ **不准動權限模式**

        原生面板那一支（`ai_settings_panel._on_profile_changed`）的註解寫著
        為什麼，一個字都不能少：

        > 在設定頁把某個客戶專案設成「手動授權」，接著在這個面板碰一下
        > 組合下拉選單，它**無聲地變回「安全自動」**。

        三張表是**額度**預設（模型、推理強度、輪數、上下文），
        安全姿態跟著使用者走，不跟著額度預設走。
        網頁這一段是同一份設定的第二個入口——**第二個入口把第一個入口的
        安全設定悄悄改掉，是這個產品最不能出的那一種事。**
        """
        from dataclasses import replace as _replace

        from app.services.ai_settings import (
            PROFILE_CUSTOM,
            AISettingsStore,
            profile_id,
        )

        wanted_id = profile_id(wanted)
        if wanted_id == PROFILE_CUSTOM or wanted_id != (wanted or "").strip():
            # `profile_id()` 認不得的東西會被它折成 `custom`，而 `custom`
            # 不是一張表——**折過的值不算收到**，所以這裡比對原字串。
            self.reject(t("run.unknown_profile", value=wanted))
            return

        store = AISettingsStore(self._project)
        current = store.load_project()
        preset = AISettingsStore.profile(wanted_id)
        # 安全姿態原封不動搬過去。**不是 `or`**：一個空字串的權限模式
        # 會被 `or` 換成表格那一份，而那正是這一段在擋的事。
        preset = _replace(
            preset,
            claude_permission_mode=current.claude_permission_mode,
            workflow_mode=current.workflow_mode,
            profile_name=wanted_id,
        )
        store.save_project(preset)
        self.push_state()

    def _save_run_defaults(self, where: str) -> None:
        """把這一次這一組存成預設。

        `project` 存這個專案的、`global` 存所有專案的。
        **第三個字一律擋**——存錯地方在畫面上看不出來，
        而下一個專案打開的時候他會以為自己沒設定過。
        """
        from app.services.ai_settings import AISettingsStore

        store = AISettingsStore(self._project)
        settings = store.load_project()
        try:
            if where == "project":
                store.save_project(settings)
            elif where == "global":
                store.save_global(settings)
            else:
                self.reject(t("run.unknown_target", where=where))
                return
        except (OSError, ValueError) as exc:
            self.reject(str(exc))
            return
        self.push_state()

    def _set_task(self, text: str) -> None:
        """把他這一次交辦的字寫進硬碟。

        ## 兩件刻意沒改的

        1. **明著按一下才存**，不是打字就存。改成打字就存是行為改變，
           而那件事沒有人拍過板（2026-08-27 交接 §四第 1 條）。
        2. **空的擋下來。**原生面板現在會跳一個警告框；這裡收成一句話。
           空的任務送出去，AI 拿到的是一份沒有題目的考卷。

        寫哪一個檔由序列決定（計畫序列 `planning-request.md`、開發序列
        `task.md`）——檔名跟著 `task` 一起推給畫面，不然他會以為
        自己改的是同一份。
        """
        body = text.strip()
        if not body:
            self.reject(t("task.empty.web"))
            return
        name = self._task_entry()["file"]
        path = self._project / ".aicc" / name
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body + chr(10), encoding="utf-8")
        except OSError as exc:
            self.reject(t("task.write_failed", name=name, error=exc))
            return
        self.push_state()

    @staticmethod
    def _index(arg: str) -> int:
        try:
            return int(str(arg).strip())
        except (TypeError, ValueError):
            return -1

    @classmethod
    def _split(cls, arg: str) -> tuple[int, str]:
        raw, _, value = str(arg).partition(":")
        return cls._index(raw), value.strip()

    def _slots(self) -> list[AgentSlot]:
        return list(self._lineup)

    # 頁面用這個值決定「＋ 加一格」要不要變灰。0 = 沒有上限。
    #
    # 2026-08-22 之前這裡回 `len(Orchestrator.DISPATCHABLE_ROLES)`，也就是 3
    # ——而那三個名字是引擎內部的常數，不是產品的規格。使用者的原話：
    # 「AICC 最多只能放 3 格這她媽在放屁」。他是對的：
    # 【資料結構現在就做成 node 圖】的不變式寫著兩角色版必須是 node 圖的一個
    # 特例，**不得另立一套結構**，而一份寫死的三人清單就是另立的那一套。
    NO_SEAT_LIMIT = 0

    @staticmethod
    def _max_slots() -> int:
        """引擎沒有位子數上限。見 `NO_SEAT_LIMIT`。"""
        return LineupWebPanel.NO_SEAT_LIMIT

    # 「加在最後」在引擎那一側就是 `at=None`（`Lineup.with_node` 的預設）。
    # 給它一個名字，是因為底下有三個地方要講同一句話。
    APPEND_AT_END: int | None = None

    @classmethod
    def _insert_at(cls, arg: str, seated: int) -> int | None:
        """頁面送過來的「插在第幾格前面」。**空字串與 `"0"` 不是同一件事。**

        * `""`（右上角那顆「＋ 加一格」）→ `None`＝加在最後。
        * `"0"`（最前面那道縫的 ⊕）→ `0`＝插在最前面。

        頁面那一側已經為這件事寫過一次註解（`btn.dataset.add !== undefined`，
        不是 `if(btn.dataset.add)`），理由是 `''` 在 JS 裡是假的。**Python 這
        一端是同一個坑的另一半**：`int(arg or 0)`、`if not arg` 這種寫法會把
        「插在最前面」靜靜地變成「加在最後」——而那兩件事在畫面上長得一模
        一樣（都多出一格，只是位置不同），使用者只會覺得自己按錯了那道縫。

        ## 讀不出來的值一律擋下來，不猜

        `Lineup.with_node` 自己是**夾**的（`max(0, min(at, len))`），所以
        `-1` 會變成 0、`99` 會變成最後。那對引擎是對的（它不能因為一個爛
        索引就爆掉），但對這一層不是：這一層是**使用者按下去的那一顆按鈕**，
        而夾過的結果是「他按了第三道縫，東西插在最前面」，畫面上沒有一個字
        說發生過這件事。所以這裡先擋，擋下來走 `reject()`——說一句話，
        再把真正的狀態推回去。

        `seated` 是**畫得出來的那幾格**（`len(self._lineup)`），跟頁面畫出來
        的縫數同一個來源：N 格有 N+1 道縫，所以 `at == seated` 是合法的
        （最後那一道），`at > seated` 不是。
        """
        raw = str(arg).strip()
        if not raw:
            return cls.APPEND_AT_END
        try:
            at = int(raw)
        except (TypeError, ValueError):
            raise LineupError(t("lineup.insert_position_unreadable", value=raw))
        if not (0 <= at <= seated):
            raise LineupError(
                t("lineup.insert_position_off_the_line", value=at, seated=seated)
            )
        return at

    @staticmethod
    def _fresh_slot_name(lineup: Lineup) -> str:
        """下一格叫什麼。

        ## 這個編號是**名牌，不是座號**

        插在中間之後，畫面上的順序會是 node1 → node3 → node2。看起來怪，
        而唯一「不怪」的做法是按位置重新編號——也就是**替使用者改掉他既有
        那幾格的名字**。這一頁不做那件事，理由 `_add_slot` 的說明裡已經寫過
        一次（早期版本按位置改名，「加一格」變成「每個人的工作往後挪一格」），
        而名字在這條線上是**指人的方式**：派工、駁回、蓋章、討論指名全都用
        它。重新編號等於把「退給 node2」這句話指到另一個人身上。

        所以編號只保證一件事：**不撞名**。撞名 `with_node` 會當場丟例外，
        而使用者收到的會是一句「已經有一個位子叫 node2 了」。

        **`taken_names` 而不是畫得出來的那幾格**：檔案裡這一版讀不出來的那
        一格也佔著一個名字。只看畫得出來的那幾格，這裡會挑到一個已經有人用
        的編號，而畫面上一格那個名字都沒有。
        """
        used = set(lineup.taken_names)
        n = len(used) + 1
        while f"node{n}" in used:
            n += 1
        return f"node{n}"

    @classmethod
    def _insert_at_reaches_the_engine(cls) -> bool:
        """⊕ 那顆按鈕**今天真的插得進去嗎**——跑一次，不是回一個寫死的 True。

        這個答案會被推成 `lineup.supports_insert_at`，而頁面拿它決定要不要
        畫每一道縫上那顆 ⊕。頁面那一側的註解寫著它為什麼要問：

        > 所以這裡**不畫一顆說謊的按鈕**。

        一個寫死的 `True` 會在下一個人把 `at` 從這條路上拆掉的那天**繼續說
        是**——而那正是那顆按鈕在防的謊。所以這裡真的做一次插入：用同一支
        解析器（`_insert_at`）、同一支取名（`_fresh_slot_name`）、同一支引擎
        （`Lineup.with_node`），然後看那一格**有沒有落在第 0 格**。`at` 被忽
        略（接在最後）的話，這裡就是 False，⊕ 不會被畫出來，使用者退回右上
        角那顆「＋ 加一格」——功能少一半，但畫面上沒有一句謊話。

        **它守不到的那一段**：`handle_action` 有沒有把 `arg` 交出去。那一行
        是這條路上唯一沒有辦法在這裡跑一次的東西（跑它會真的動到陣容並寫
        檔），由 `tests/test_a_seat_inserted_between_two_others_lands_there.py`
        與 `tools/mutations/mutations_insert_between_two_seats.json` 的 T1 守。

        算不出來一律回 False（fail closed）：這個旗標唯一的用途是決定要不要
        畫一顆按鈕，而「不確定」的正確畫法是不要畫。
        """
        try:
            if cls._insert_at("", 2) is not cls.APPEND_AT_END:
                return False
            if cls._insert_at("0", 2) != 0:
                return False
            probe = Lineup.classic()
            fresh = cls._fresh_slot_name(probe)
            landed = probe.with_node(fresh, "codex", at=0)
            # **兩個半邊都要驗。**這一支同時決定兩顆按鈕畫不畫：
            # 橫的 ⊕（插在這裡）與線上那顆 ↓（往下分一行）。
            # ↓ 承諾的是**插一格＋接一條線**，只驗前半的話，
            # `"<at>@<parent>"` 那個 `@` 被誰拆掉的那天，
            # 旗標還是說是，而那顆按鈕還是被畫出來。
            wired = probe.with_node(fresh, "codex", at=1).with_parent(
                fresh, probe.roles[0]
            )
        except Exception:
            return False
        if wired.slots[1].parent != probe.roles[0]:
            return False
        # 新的那一格在最前面，原本那幾格**原封不動地跟在後面**。
        # `at` 被忽略的話 `roles[1:]` 會是 ("reviewer", "node3")。
        return landed.roles[1:] == probe.roles

    def _add_slot(self, at: str = "") -> None:
        """加一格（ROADMAP P10 Stage B 起）。

        Seats that already exist keep their role, provider and session. An
        earlier version renamed seats by position, which turned "add a seat"
        into "everyone moves up one job".

        （這裡以前寫著「The third seat goes **in front**, not on the end」，
        理由是第三格一定是規劃者、而規劃者一定跑第一個。那件事 2026-08-22
        隨著「格數沒有上限、職責與名字分家」一起不成立了——第三格可以是任何
        東西——而**這段散文比程式碼晚了五天還在講一個不存在的行為**：那一天
        起 `with_node` 收到的 `at` 一直是 `None`，也就是接在最後。留著它的話，
        下一個人讀完會以為新的那一格會出現在最前面。）

        ## `at`：**插在第幾格前面**（2026-08-27）

        老闆打開 App 之後的原話：

        > 那這樣子新增一個往哪裡新增？⋯⋯這個加號應該在格子邊邊，那我先按加號
        > 才可以在這兩個 node 之間插入一個新的 node 才對。

        引擎與頁面兩端本來就做得到，斷的是這一支：它沒有這個參數，
        `handle_action` 那一行也沒有把 `arg` 交出來。兩個入口**並存**——
        右上角那顆「＋ 加一格」送空字串（加在最後），每一道縫上那顆 ⊕ 送
        它自己的格號。空字串與 `"0"` 怎麼分見 `_insert_at`。
        """
        # **`"<插在第幾格>@<接在誰後面>"`**（2026-08-27，老闆定案的 ⊕／↓ 語意）：
        #
        # > 我覺得 ＋ 號是在兩個 node 中間加一個 node，
        # > 但往下一行的話，是要兩個 node 中間**往下拉一行**出來才對。
        #
        # 也就是**分支的起點在那條線上，不是某一格**——他不必先在腦中想像
        # 那條線會怎麼接，按方向就好。`@` 後面那一半是「上一格是誰」，
        # 只有那顆「↓」會帶；橫的那顆 ⊕ 送空的。
        at, _, parent = at.partition("@")

        # 沒有上限了。名字自動編號，使用者再自己改成他要的
        # （「發想」「粗剪」「上字幕」都行）。見 `_fresh_slot_name`。
        try:
            index = self._insert_at(at, len(self._lineup))
        except LineupError as exc:
            # **擋下來要講得出下一步，而且那句話要真的到得了他面前。**
            # 只在這裡 `return` 的話，他按的那顆 ⊕ 什麼都不會發生，
            # 而「按了沒反應」跟「插好了」在畫面上長得一模一樣。
            self.reject(str(exc))
            return
        name = self._fresh_slot_name(self._lineup)
        try:
            grown = self._lineup.with_node(name, "codex", at=index)
            if parent:
                # **接線失敗就整個不算數。**先加好再接線，接線被拒的話
                # `self._lineup` 已經多了一格——他按「往下分一行」，
                # 拿到一句錯誤訊息**外加一格他沒要的**，而那一格會被存進硬碟。
                # 所以改在暫存那一份上做完兩步，兩步都成才換過去。
                grown = grown.with_parent(name, parent)
        except LineupError as exc:
            self.reject(str(exc))
            return
        self._lineup = grown
        self._persist()

    def _remove_slot(self, index: int) -> None:
        slots = self._slots()
        # 任何一格都刪得掉，而且沒有「至少兩格」。
        #
        # 那條下限以前寫在這裡，理由是「一個人審自己不算審查」——理由是對的，
        # 但它是**角色表格**該表達的事（一人表格由使用者本人訂驗收標準），
        # 不是這一層該擋的。擋在這裡的結果是使用者連一人模式都排不出來。
        #
        # 只剩一條：不准把最後一格也刪掉，那會留下一個按下去什麼都不會發生
        # 的專案。
        if not (0 <= index < len(slots)) or len(slots) <= 1:
            return
        try:
            # 拿掉一格會把它下面那一層接到它的上一格（＝把線接起來，也就是
            # 今天那一排的行為）。第一層那一格底下明著接了東西的時候接不
            # 起來，`Lineup` 會丟例外並指名是哪幾格——**擋下來不講跟沒擋
            # 一樣**：他按了〔移除〕，卡片還在，而他會以為是按鈕壞了。
            self._lineup = self._lineup.without_node(slots[index].role)
        except LineupError as exc:
            self.reject(str(exc))
            return
        self._persist()

    def _set_slot_parent(self, index: int, parent: str) -> None:
        """把這一格接到另一格後面。**空字串＝接回前面那一格。**

        這是分支唯一的入口（老闆 2026-08-22：「有需要才往下增加分支」）。
        送過來的是**上一格的名字**，不是它的序號——線是用名字拉的（派工、
        駁回、蓋章都是），而序號會在任何一次新增／移除之後指到別人身上。

        接不到的理由由 `Lineup` 判並且**送到畫面上**，理由同改名字那一支：
        按了沒反應跟接好了長得一模一樣，而他接下來會照著一個沒有發生的
        改動排事情。
        """
        slots = self._slots()
        if not (0 <= index < len(slots)):
            return
        try:
            self._lineup = self._lineup.with_parent(slots[index].role, parent)
        except LineupError as exc:
            self.reject(str(exc))
            return
        self._persist()

    def _set_slot_provider(self, index: int, provider: str) -> None:
        slots = self._slots()
        if not (0 <= index < len(slots)) or provider not in VALID_PROVIDERS:
            return
        current = slots[index]
        # ⛔ 這裡 2026-09-01 之前擋著「把執行那一格換成 Codex」。同上，
        # 那條規則從來不是他講的，而兩家都寫得了檔之後它永遠不成立。
        # The session belonged to the old provider - keep it only if the
        # provider is unchanged, or the seat would point at another vendor's
        # conversation id.
        #
        # **判準借 `session_for_new_provider()`，這裡不自己寫一次。**問同一件事
        # 的第二個入口是套用模式，而它 2026-08-26 之前自己留了一份「照舊帶著」
        # ——把一格從 Claude 換成 Codex 的模式，套用之後那一格帶著 Claude 的
        # 對話 id 被寫回硬碟，一句話都沒有。
        session_id = session_for_new_provider(current, provider)
        # **`replace()`，不是重新組一個。**這裡以前傳五個位置參數，於是
        # 第六格 `duty` 靜靜地掉了——而掉了之後 `AgentSlot.__post_init__` 會
        # 照舊角色名回推，`author` 剛好推回 `work`，**所以掉了跟沒掉長得
        # 一模一樣**；使用者改過名的那一格（`node1`）推不出來，一律變 `review`。
        #
        # 同一個病 2026-08-22 在 `workflow_worker.py` 修過一次，那裡的註解
        # 寫著同一句話（「用具名參數，不要用位置參數」）——而這三個地方漏了。
        # 用 `replace()` 是為了讓下一個加欄位的人**不必記得這件事**：
        # 沒有列出來的欄位一律照抄，漏不掉。
        #
        # identity 在換廠商時要留著：它描述的是這一格的工作，不是廠牌。
        # 因為換了一個下拉就把人打的字清掉，是那種一直到執行講錯話才會
        # 有人發現的損失。
        slots[index] = replace(
            current,
            provider=provider,
            session_id=session_id,
            credential=current.credential if provider == current.provider else "",
        )
        self._lineup = Lineup.of(slots)
        self._persist()

    def _set_slot_session(self, index: int, session_id: str) -> None:
        slots = self._slots()
        if not (0 <= index < len(slots)):
            return
        current = slots[index]
        # **這一步是使用者一定會按的**（沒指定對話畫面直接擋著不讓開始），
        # 所以它掉 `duty` 的代價是滿的：加一格 → 改名 → 設職責 → 挑對話，
        # 按完最後一步那一格就從「執行」變成「複查」，畫面上一個字都不說。
        # 然後 `steps/building.py` 找不到任何 `duty == work` 的位子，
        # 整輪收在「沒有人做事」。理由見上面那一段。
        slots[index] = replace(current, session_id=session_id)
        self._lineup = Lineup.of(slots)
        self._persist()

    def _set_slot_identity(self, index: int, identity: str) -> None:
        """Replace this seat's 「你是⋯⋯」 line (ROADMAP P10 Stage D).

        Blank clears it, and clearing is the whole of "restore the default" -
        there is no separate reset flag that could disagree with the text.

        The safety sentences are not reachable from here. They live in
        `prompt_builder.StepPersona.rails` and are re-attached after whatever
        this sets, so 「不得修改任何檔案」 cannot be typed away.
        """
        slots = self._slots()
        if not (0 <= index < len(slots)):
            return
        self._lineup = self._lineup.with_identity(slots[index].role, identity)
        self._persist()

    def _set_slot_role(self, index: int, name: str) -> None:
        """改這一格的名字。

        名字是使用者取的、引擎不看的那一半——但它**是**整條線指人的方式
        （派工、駁回、蓋章、討論指名都用它）。所以重複與空白由 `Lineup`
        擋下來，而**擋下來的理由要送到畫面上**：按了沒反應跟改好了長得
        一模一樣，而使用者接下來會照著一個沒有發生的改動做事。
        """
        slots = self._slots()
        if not (0 <= index < len(slots)):
            return
        try:
            self._lineup = self._lineup.renamed(slots[index].role, name)
        except LineupError as exc:
            self.reject(str(exc))
            return
        self._persist()

    def _set_slot_duty(self, index: int, duty: str) -> None:
        """改這一格的職責。**引擎只看這個。**

        名字自由、職責封閉，是【角色表格】那條定案唯一能落地的形狀：角色名
        要可改，但引擎必須知道要不要給它寫入權限。

        不認得的值由 `Lineup` 拒絕，不退回預設值——退回預設值的話，使用者
        選了一個東西、按下去沒有錯誤訊息、而那一格變成別的職責，他不會發現。

        ## 三條路的第二條（2026-08-25 量到的）

        把一格 Codex 的職責改成「執行」，這一支以前**一句話都沒說**就存了
        下去——它只擋「不認得的職責」，不擋廠牌。存下去之後：卡片變玫瑰色
        外框、下拉只剩 CLAUDE 一個選項而且選著，而硬碟上是 Codex；上方
        執行列那顆「開始執行」還亮著，按下去真的開跑、真的花掉模型呼叫，
        最後停在 `Orchestrator._run_as()` 的例外上。

        **擋下來，而且要講得出下一步。** 這一格他選的是職責，被拒絕的理由
        卻在廠牌那一欄——不指出那件事的話，他會再選一次同一個值。
        """
        slots = self._slots()
        if not (0 <= index < len(slots)):
            return
        try:
            updated = self._lineup.with_duty(slots[index].role, duty)
        except LineupError as exc:
            self.reject(str(exc))
            return
        # 問的是**改完之後那一格長什麼樣**，不是「他選的字串是不是 work」：
        # 職責的正規化（去空白、轉小寫）住在 `AgentSlot.__post_init__`，
        # 在這裡自己比一次字串等於抄一份判準，而 `" Work "` 這種值會從
        # 抄的那一份底下鑽過去。
        #
        # ⛔ 這裡 2026-09-01 之前擋著「這一格是 Codex，職責不准設成執行」，
        # 而老闆那天說「哪個智障說只有 claude 可以寫的，我之前明明就說都可以」
        # ——查完他是對的，那條規則沒有人問過他（見 `app/core/lineup.py`）。
        # 兩家都寫得了檔之後這道閘門永遠不成立，拿掉。
        self._lineup = updated
        self._persist()

    def _set_slot_writes(self, index: int, writes: str) -> None:
        """開或關這一格的寫檔開關。**職責一個字都不動。**

        這是老闆 2026-08-25 要的那顆開關（「一般使用者像我的話可以自己選擇
        要不要寫入文件」）。在這之前唯一做得到的是把職責從「執行」改掉，
        而那不是「照跑但不寫檔」——`steps/building.py` 挑人只挑職責是執行的
        那幾格，改掉之後那一格**整輪不開口**，後面每一格也就收不到它本來
        會說的話。

        不認得的值由 `Lineup`／`AgentSlot` 拒絕，理由跟職責那一支一樣：
        退回預設值的話，使用者按了一下、沒有錯誤訊息、而那一格變成另一個
        答案——**而這一欄的答案是「它動不動得了我的檔案」。**
        """
        slots = self._slots()
        if not (0 <= index < len(slots)):
            return
        try:
            self._lineup = self._lineup.with_writes_files(
                slots[index].role, writes
            )
        except LineupError as exc:
            self.reject(str(exc))
            return
        self._persist()

    def _apply_preset(self, name: str) -> None:
        """Swap the whole line-up for a saved mode (ROADMAP P10 Stage E).

        Failures are surfaced, not swallowed: applying a mode replaces every
        seat, so 「按了沒反應」 here is indistinguishable from 「套用了但看起來
        一樣」, and the user would go on to run a workflow believing something
        that is not true.

        `LineupError` 也要接。`apply_preset` 2026-08-25 起會在「模式裡有一格
        跟檔案裡讀不出來的那一格同名」時丟它——不接的話那個例外會從頁面橋
        直接掉出去，而**擋對了卻沒有人講，跟沒擋一樣**。
        """
        preset = self._presets.find(name)
        if preset is None:
            self.reject(f"找不到模式「{name}」。")
            return
        try:
            # 從零組一份新的（`apply_preset` 不看舊陣容的那幾格），
            # 讀不出來的那幾格由 `_persist()` 接回去。
            self._lineup = apply_preset(preset, self._lineup)
        except (PresetError, LineupError) as exc:
            self.reject(str(exc))
            return
        self._persist()

    def _save_preset(self, name: str) -> None:
        """存成一份模式。

        `PresetFileTooNew` 也要接：它是 `RuntimeError`，不是 `PresetError`，
        2026-08-24 之前**全 repo 沒有人接**，於是它會直接穿出 `handle_action`
        ——使用者按下「存成模式」，得到的不是一句「沒有動它，請更新 AICC」，
        是一個從頁面橋掉出去的例外。**它擋對了，只是沒有人把它講出來。**

        `PresetFileUnreadable` 同理，而且它比前者更容易遇到：前者要有一台跑
        新版 AICC 的機器寫過那個檔案，後者只要那份檔案曾經被寫壞——而它是
        `%APPDATA%` 全機器共用的一份。**擋下來卻不講，跟沒擋一樣**：使用者
        會以為存進去了。

        訊息前面接一句 `t("file.unreadable")`，跟稽核頁遇到讀不出來的決策
        紀錄時走的是同一句（`audit_web_panel._refuse`）。例外自己帶的是路徑
        與解析器指出的位置——**那是他真的動得了手的唯一線索**。
        """
        try:
            self._presets.save(name, self._lineup)
        except (PresetError, PresetFileTooNew) as exc:
            self.reject(str(exc))
            return
        except PresetFileUnreadable as exc:
            self.reject(f"{t('file.unreadable')}\n\n{exc}")
            return
        self.push_state()

    def _move_slot(self, index: int, direction: int) -> None:
        """上下移動一格。**搬的是整個 node，一格都不留在原位。**

        老闆 2026-08-27 的原話：

        > **我覺得箭頭要的東西是把整個 node 往後移或往前移，包含設定與 prompt**

        在這之前這一支換的只有**廠牌、對話 id 與憑證**：名字、身分、職責、
        寫檔開關、線全部釘在位置上，所以他按下〔後移 ▶〕之後**那張卡片看
        起來沒有動**——node1 還叫 node1，只有底下用哪個 AI 換過去了。那一半
        當時是明著標成「還沒有被決定」的產品問題（`docs/WHY.md`「箭頭搬的是
        整個 node」那一節），今天他一句話結掉了。

        現在搬的是 `AgentSlot` 這個整體，**八個欄位一起走**：名字、廠牌、
        對話 id、憑證、身分、職責、寫檔開關、線。這一層不再自己組一份
        `AgentSlot`——順序那件事由 `Lineup.moved()` 一支回答，這裡只把「他按
        的是哪一格、要去哪裡」翻譯過去，並且把擋下來的話送到他面前。

        ## 當初「每一樣都釘在位置上」的四條理由，逐條驗過

        | 當初的理由 | 整個 node 一起搬之後 |
        |---|---|
        | 職責留位置＋廠牌跟人，湊得出「Codex ＋ 執行」 | **坑沒了。**兩欄現在同進同出，移動不可能造出新的錯配——見下面「拿掉的那條拒絕路徑」|
        | 身分留位置（「你是很嚴格的審查員」是複查那一格的工作描述）| 他明說 **prompt 要跟著走**，所以身分跟著人。工作描述現在是「這個 node 的」，不是「第幾格的」|
        | 寫檔開關留位置，不然跟著人走會**無聲打開權限** | **坑沒了，但理由換了。**權限是職責與開關兩欄合起來算的（`AgentSlot.may_write_files`），而兩欄現在鎖在同一格裡一起搬——他親手關掉的那個 `off` 跟著它自己的職責走，翻不回來 |
        | 線留位置，因為線是用**名字**拉的而名字留在原位 | **名字現在跟著走了**，所以那條線指的還是同一格。反而多出一個新的失敗模式，見下面 |

        四條各有一條斷言單獨守著，在
        `tests/test_the_arrow_moves_the_whole_node.py`。

        ## 新的失敗模式：搬動會把線弄斷

        線只准往前指（`Lineup.with_parent` 的 `parent_runs_later`），而搬動改
        的正是「誰在誰前面」。把一格搬到它接的那一格前面，那條線就變成指向
        一個還沒跑的位子。

        **擋下來，不自動改線**——理由同這一頁另外三處（`_set_slot_duty`、
        `AgentSlot.writes_files`、`_insert_at`）：自動改的話他排一次順序，AICC
        把他畫的分支接到另一格身上，而改完的那張圖看起來就像他畫的。

        判準只有一份，在 `Lineup.moved()` 裡；這一層**不再抄一份**，只負責
        `except LineupError` → `reject()`。少了那個 `except`，他按一下拿到的
        是一坨從頁面橋掉出去的 traceback——這一支 2026-08-27 之前沒有它，
        因為它以前不呼叫任何會丟例外的東西。

        ## 拿掉的那條拒絕路徑：`vendor_cannot_write`（那一支 2026-09-01 也整個拿掉了）

        2026-08-26 這裡有一條「這兩格對調之後會不會把 Codex 放上執行那一格」
        的拒絕。**它現在永遠走不到**：搬動不再拆開任何一格，`(廠牌, 職責)`
        這一對是移動的**不變量**，所以移動造不出新的違規。

        留著它比拿掉更糟，兩個理由：

        1. **修好之後走不到的分支，它的破壞會永遠是綠的**——一條看起來在守
           東西、實際上守不到東西的檢查，而它的綠燈跟對的綠燈長得一模一樣。
        2. 它會**多擋**：唯一還能讓它成立的情況是那一格**本來就**違規（手改
           過的 `sessions.json`、或別台機器來的模式檔），而那時候它擋掉的是一
           個跟那個違規無關的動作——「檔案裡本來就有的一格違規讓他連別處都移
           不動」，正是這一支當初刻意避開的形狀。

        那條路今天由**不變量**守著，不是由一道閘門守著：
        `test_the_arrow_moves_the_whole_node.py` 的
        `test_an_arrow_cannot_invent_a_pairing_that_was_not_there` 枚舉兩格與
        三格的每一種廠牌／職責組合、每一個方向各按一次，比對 `(廠牌, 職責)`
        的多重集合一個都沒有變。職責哪一天又被留在位置上，那一條會紅。
        """
        slots = self._slots()
        target = index + direction
        if not (0 <= index < len(slots)) or not (0 <= target < len(slots)):
            return
        # `moved()` 收的是**名字**，不是序號——線是用名字拉的，而這一支送出去
        # 的正是「那一格」本身。
        #
        # **擋下來要講得出下一步，而且那句話要真的到得了他面前。**只 `return`
        # 的話他按了沒反應，而「按了沒反應」跟「移好了」在畫面上長得一模一樣
        # （網頁那一側是樂觀更新的）。
        try:
            self._lineup = self._lineup.moved(slots[index].role, target)
        except LineupError as exc:
            self.reject(str(exc))
            return
        self._persist()

    # ---- for the execution bar ------------------------------------------
    def provider_sequence(self) -> list[str]:
        """`["claude", "codex"]` - what the shell's read-only summary shows."""
        return [slot.provider for slot in self._lineup]

    def slot_index(self, role: str) -> int | None:
        """Where `role` sits in the drawn line-up, or None if it is not seated.

        The shell lights a seat by index, and this is the only source that is
        guaranteed to agree with `provider_sequence()` - they read the same
        object. A second list of roles kept anywhere else agrees only while
        every project seats the same ones.
        """
        return self._lineup.index_of_role(role)
