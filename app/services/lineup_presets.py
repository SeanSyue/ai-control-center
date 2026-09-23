"""工作線模式：一組陣容，存起來、取個名字、套到別的專案（ROADMAP P10 Stage E）。

使用者的原話是「我要分模式」，而「模式」聽起來像一個新功能。它不是。

一個模式就是**「幾格、每格誰、每格什麼身分」存成一筆資料**。前面四個階段
把這三件事都變成資料之後，模式就不需要任何新的程式分支——這正是當初把它拆成
A–E 的用意。反過來如果做成「大型專案模式」「思考模式」各一段 `if`，
第四種模式出現的時候它們就會開始互相打架，而那就是 Stage A 之前的那個坑。

**套用一個模式會換掉整個陣容，這是有代價的一件事**，所以這裡處理了三件：

* **session 綁定留在專案這邊。** 模式描述的是「誰做什麼」，不是「接哪一段對話」。
  套用時同名角色沿用該專案原本綁的 session；模式裡沒有的角色，綁定就跟著那一格
  一起消失（那是真的沒有了，不是被弄丟的）。
* **引擎跑不動的角色直接拒絕。** 一個存了 `finisher` 的模式套下去會存成一份
  畫得出來、跑不到的陣容，而那是這一整個 P10 反覆在防的東西。
* **內建的兩種不能被覆蓋。** 它們是「回到出廠」的那條路，蓋掉之後就沒有了。
"""
from __future__ import annotations

from app.i18n import t

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.core.lineup import (
    DUTY_APPROVE,
    DUTY_REVIEW,
    DUTY_WORK,
    AgentSlot,
    Lineup,
    LineupError,
    name_already_taken,
)

PRESETS_FILE = "lineup-presets.json"

# 出廠三張角色表格（2026-08-21 定案）：一人（小事）／兩人（一般）／三人
# （重要），預設名稱**執行／複查／核准**，使用者可改。
#
# **出廠只給三張**，要更多自己複製去改——選項超過三個會讓人不決定。畫面上
# 另外有一個「自訂」，那不是第四張表格，是「從頭自己排」。
#
# 內建而不是寫進使用者的檔案：寫進去的那一份會被改，然後就沒有路回到出廠的
# 樣子了。
#
# 每一格都**明著寫職責**。不寫的話它會從名字推——而「執行」「複查」這種中文
# 名字推不出來，會落到安全的那一邊（複查），於是一張叫「一人」的表格排出來
# 之後**沒有任何一格能寫檔案**，而畫面上完全看不出為什麼。
#
# `provider` 是起點不是規矩，編組的時候可以改。兩人與三人**出廠就配成不同
# 廠牌**（Q10 定案）：同廠牌會共用盲點，兩邊會一起同意同一個錯答案。
# **名字與說明存 key**（模組層取出來會在 import 的當下凍住）；
# 席位名在 `built_in_presets()` 裡才取，那是函式。
BUILT_IN: tuple[
    tuple[str, str, tuple[tuple[str, str, str], ...]], ...
] = (
    (
        "preset.one.name",
        "preset.one.description",
        (("role.worker", "claude", DUTY_WORK),),
    ),
    (
        "preset.two.name",
        "preset.two.description",
        (("role.worker", "claude", DUTY_WORK), ("role.reviewer", "codex", DUTY_REVIEW)),
    ),
    (
        "preset.three.name",
        "preset.three.description",
        (
            ("role.worker", "claude", DUTY_WORK),
            ("role.reviewer", "codex", DUTY_REVIEW),
            ("role.approver", "claude", DUTY_APPROVE),
        ),
    ),
)


# 磁碟格式的版本。跟 `SessionRegistry.VERSION` 是同一個東西的兩份檔案，
# 所以刻意用同一個數字：一個陣容存在專案裡叫 sessions.json，存成模式叫
# lineup-presets.json，形狀是一樣的。
FORMAT_VERSION = 1


class PresetFileTooNew(RuntimeError):
    """`lineup-presets.json` 是比這個版本新的 AICC 寫的。

    **看不懂的檔案不准蓋。**照這一版的形狀寫回去，新版多出來的欄位會消失，
    而使用者不會收到任何一個字。
    """


class PresetEntryUnreadable(RuntimeError):
    """`lineup-presets.json` 裡有**這一版讀不出來的模式**。

    跟 `PresetFileTooNew` 是同一條定案的兩半。那一個管的是「整份檔案是新版
    寫的」；這一個管的是**同一個版本、某一筆讀不出來**——多支援一個廠牌而
    `FORMAT_VERSION` 沒跳、或者使用者自己手改過一筆，就長這樣。

    為什麼要有這個東西：`delete()` 以前在這種情況回 `False`，而呼叫端會把
    它翻成「沒有那個模式」。那句話是假的——它很可能就在檔案裡，只是這一版
    讀不出它的形狀。回一個 `False` 等於用一句謊話回答一個誠實的問題。

    **訊息刻意是路徑而不是一句中文。**今天 repo 裡沒有任何一顆刪除模式的
    按鈕（`grep delete_preset` 全空），所以這一句還到不了任何人的畫面；
    接刪除鍵的人要先把給使用者看的那句話加進 `app/i18n.py`，再用 `t()` 取
    ——現在硬寫一句中文進來，只是多欠一段永遠沒被看過的介面文字。
    """


class PresetFileUnreadable(RuntimeError):
    """`lineup-presets.json` **整份讀不出來**——不是合法的 JSON，或者形狀不對。

    這是同一條定案（【看不懂的檔案不准蓋】）的第三半，前兩半是
    `PresetFileTooNew`（整份是新版寫的）與 `PresetEntryUnreadable`
    （同版本、某一筆讀不出來）。**它們三個沒有一個擋得住這一種**：

    * `_refuse_if_newer()` 問的是 `version` 這個欄位——而一份 `json.loads`
      就炸掉的檔案根本讀不到任何欄位，`stored_version()` 於是回 0，
      「0 > 1」是假的，門就開了。
    * `PresetEntryUnreadable` 數的是**讀不出來的那幾筆**——整份解析失敗的
      時候一筆都數不到，`lost` 是空的，那道門也開了。

    實測（2026-08-24）：一份被截斷的 `lineup-presets.json`（存檔存到一半
    斷電、或者使用者自己手改壞了一個括號）裡有一份叫「我的三人組」的模式，
    使用者存一份**別的**模式 → 回報「已儲存」→ 檔案被整份換掉，
    「我的三人組」一個字都不剩；接著想刪它，`delete()` 回 `False`，
    呼叫端把它翻成「找不到那個模式」。

    **訊息刻意是路徑加上解析器自己講的那一句，不是一句中文**，理由跟
    `PresetEntryUnreadable` 一樣：給使用者看的那句話住在 `app/i18n.py`
    （`_save_preset` 用 `t("file.unreadable")` 接在前面），而這裡要能講出
    **是哪一個檔案、第幾行壞掉**——那是他真的動得了手的唯一線索。
    """


class PresetError(ValueError):
    """A preset that could not be saved or applied, with a reason to show."""


@dataclass(frozen=True)
class LineupPreset:
    name: str
    description: str
    lineup: Lineup
    built_in: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            # Session ids are deliberately dropped. A preset is reused across
            # projects, and a conversation id from one project means nothing in
            # another - carrying it would point a seat at a conversation that
            # vendor has never heard of, in a project that never had it.
            "slots": [
                {
                    "role": slot.role,
                    "provider": slot.provider,
                    "identity": slot.identity,
                    # 職責一定要存。少了它，讀回來的時候會從名字推——而使用者
                    # 自己取的名字推不出來，會落到安全的那一邊（複查）。於是
                    # 他存好的表格套用之後**沒有任何一格能寫檔案**，而畫面上
                    # 完全看不出為什麼。
                    "duty": slot.duty,
                    # 寫檔開關也一定要存。使用者存這張表格的意思裡就包含
                    # 「這一格我不讓它動我的檔案」——少了這一欄，套用回去的
                    # 那一格會**變回可以寫**，而表格的名字看起來一模一樣。
                    "writes_files": slot.writes_files,
                    # 線也一定要存。【角色表格】那條定案寫著一張表格 =
                    # 一組角色**加上它們怎麼連**——少了這一欄，一張存好的
                    # 分支表格套用回去會**攤成一排**，而表格的名字、格數、
                    # 每一格的職責看起來一模一樣。
                    "parent": slot.parent,
                }
                for slot in self.lineup
            ],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "LineupPreset | None":
        if not isinstance(data, dict):
            return None
        name = str(data.get("name", "")).strip()
        if not name:
            return None
        raw_slots = data.get("slots")
        if not isinstance(raw_slots, list) or not raw_slots:
            return None
        try:
            slots = [
                AgentSlot(
                    role=str(item.get("role", "")),
                    provider=str(item.get("provider", "")),
                    identity=str(item.get("identity", "")),
                    # 舊檔沒有這一欄，空字串會照舊從名字推——那是相容，
                    # 不是預設值。
                    duty=str(item.get("duty", "")),
                    # 同上。空字串代表「存這張表的那一版還沒有這個開關」，
                    # 解讀寫在 `AgentSlot.writes_files`——**不要在這裡另外
                    # 給一個預設值**。
                    writes_files=str(item.get("writes_files", "")),
                    # 同上。空字串代表「接前面那一格」，也就是存這張表的
                    # 那一版還沒有分支——**不要在這裡另外給一個預設值**。
                    parent=str(item.get("parent", "")),
                )
                for item in raw_slots
                if isinstance(item, dict)
            ]
        except LineupError:
            return None
        if not slots:
            return None
        return cls(
            name=name,
            description=str(data.get("description", "")),
            lineup=Lineup.of(slots),
        )


def _with_unreadable_back(
    rows: list[dict[str, Any]], lost: list[tuple[int, Any]]
) -> list[Any]:
    """要寫回檔案的那一串，**把讀不出來的那幾筆原樣插回原本的位置**。

    原樣，不是照這一版的形狀重寫一份：讀不出來的原因很可能正是「它有一個
    這一版沒有欄位可以裝的東西」，重寫一次那個東西就沒了。

    插回原本的 index 而不是接在最後：清單是照存檔順序畫的，把一筆搬到隊尾
    是一個使用者沒有下過的指令。前面的筆數會因為換掉／新增而移動，所以位置
    是盡力而為——**但「還在不在」不是盡力而為，是一定要在。**
    """
    out: list[Any] = list(rows)
    for index, entry in lost:
        out.insert(max(0, min(index, len(out))), entry)
    return out


def built_in_presets() -> list[LineupPreset]:
    return [
        LineupPreset(
            name=t(name),
            description=t(description),
            lineup=Lineup.of(
                # 席位名也是 key。**在這裡取而不是在 `BUILT_IN` 裡**：
                # 那張表是模組層的，取出來的字會在 import 的當下凍住
                #（`tests/test_no_new_hardcoded_chinese.py` 有一條擋著，
                # 而它 2026-08-24 抓到我三次）。
                AgentSlot(t(role), provider, duty=duty)
                for role, provider, duty in slots
            ),
            built_in=True,
        )
        for name, description, slots in BUILT_IN
    ]


class LineupPresetStore:
    """Saved modes. Global, not per project - reuse is the point.

    Stored beside `projects.json` for the same reason that file is global:
    both answer 「這台機器上的我」 rather than 「這個專案」.
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        if config_dir is not None:
            self.config_dir = Path(config_dir)
        else:
            appdata = os.environ.get("APPDATA")
            self.config_dir = (
                Path(appdata) / "AIControlCenter"
                if appdata
                else Path.home() / ".aicontrolcenter"
            )
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.config_dir / PRESETS_FILE

    # ---- reading ---------------------------------------------------------
    def _document(self) -> dict[str, Any] | None:
        """磁碟上那一份的原文。**沒有檔案回 None；有檔案而讀不出來丟例外。**

        兩者要分得出來，因為它們該有的下場相反：**沒有檔案**代表這台機器還
        沒存過任何模式，第一次存檔就是要新建一份；**有檔案而讀不出來**代表
        那裡面很可能有他存過的東西，而這一版看不懂它——照這一版的形狀寫回去
        等於把它刪掉，而畫面上寫的是「已儲存」。

        形狀也算：`presets` 不是一個 list 的話，這一版沒有任何辦法從那份
        檔案裡撈出使用者存過的模式。那可能是更新之後改了鍵名（而 `version`
        忘了跳），也可能是別的東西剛好叫這個檔名——兩種都不該被蓋掉。
        """
        if not self.path.is_file():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PresetFileUnreadable(f"{self.path}: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("presets"), list):
            raise PresetFileUnreadable(
                f"{self.path}: no readable 'presets' list in this file"
            )
        return raw

    def _refuse_if_unreadable(self) -> None:
        """整份讀不出來就不要動它。**寫檔的那兩支各叫一次。**

        故意不放進 `_partition()`：讀給人看的那一條路（`saved()` → `all()`）
        在陣容頁一打開就會跑，在那裡丟例外的話，一份壞掉的模式檔會讓**整頁
        打不開**——而那是拿一個更大的故障去換一個資料損失，使用者連他的陣容
        都看不到了。看的那一半照舊只列讀得出來的；擋的是**寫回去**那一半。
        """
        self._document()

    def _partition(self) -> tuple[list[LineupPreset], list[tuple[int, Any]]]:
        """(讀得懂的模式, 讀不懂的原始 entry ＋ 它原本排第幾)。

        兩半一起回，因為它們**必須來自同一次讀檔**。分兩支各讀一次的話，
        中間有人改過檔案的那一次，寫回去的會是「這一次讀到的讀得懂的」加上
        「上一次讀到的讀不懂的」——一份誰都沒有存過的檔案。

        `saved()` 只要前面那一半；寫檔的那兩支兩半都要。
        """
        try:
            raw = self._document()
        except PresetFileUnreadable:
            # 這一支只負責「看」，所以整份讀不出來的時候回一份空的就好。
            # **擋住寫回去的是 `_refuse_if_unreadable()`**，不是這裡——見
            # 那一支的說明（在這裡丟例外會讓陣容頁整頁打不開）。
            return [], []
        if raw is None:
            return [], []
        entries = raw["presets"]
        presets: list[LineupPreset] = []
        lost: list[tuple[int, Any]] = []
        for index, item in enumerate(entries):
            preset = LineupPreset.from_dict(item)
            if preset is not None:
                presets.append(preset)
            elif isinstance(item, dict) and str(item.get("name", "")).strip():
                # 有名字＝使用者真的存過這一份，只是這一版讀不出它的形狀。
                # 沒名字的那種是垃圾，丟掉不會讓他失去他知道自己有的東西。
                lost.append((index, item))
        return presets, lost

    def saved(self) -> list[LineupPreset]:
        """Only the user's own. Unreadable entries cost themselves, not the file.

        「只是不顯示」這句話 2026-08-24 之前是假的：`save()` 拿這一支的結果
        **整份覆寫**檔案，所以讀不出來的那一筆付出的不是「不顯示」，是從硬碟
        上消失，而那一次存檔回報的是成功。現在寫檔的兩支自己去問
        `_partition()`，把那幾筆原樣接回去。
        """
        return self._partition()[0]

    def all(self) -> list[LineupPreset]:
        """Built-ins first, then the user's, with name collisions dropped.

        A saved preset can never shadow a built-in - that is what makes
        「回到出廠的那兩種」 a promise rather than a hope.
        """
        presets = built_in_presets()
        taken = {preset.name for preset in presets}
        for preset in self.saved():
            if preset.name not in taken:
                presets.append(preset)
                taken.add(preset.name)
        return presets

    def find(self, name: str) -> LineupPreset | None:
        target = name.strip()
        return next((p for p in self.all() if p.name == target), None)

    # ---- writing ---------------------------------------------------------
    def save(
        self, name: str, lineup: Lineup, description: str = ""
    ) -> LineupPreset:
        """Save (or replace) one of the user's own presets.

        Refuses a built-in's name rather than silently storing a shadow copy
        that `all()` would then never return - a save that reports success and
        changes nothing is worse than a refusal.
        """
        clean = name.strip()
        if not clean:
            raise PresetError(t("preset.needs_a_name"))
        if any(preset.name == clean for preset in built_in_presets()):
            raise PresetError(
                f"「{clean}」是內建模式，不能覆蓋。"
                "內建的那兩種是回到出廠的路，蓋掉就沒有了——請換一個名字。"
            )
        if not len(lineup):
            raise PresetError(t("preset.empty_lineup"))

        preset = LineupPreset(clean, description.strip(), lineup)
        stored, lost = self._partition()
        others = [p for p in stored if p.name != clean]
        # **順序是先「讀不出來」再「太新」。**版本號是從檔案裡讀出來的，
        # 而一份 `json.loads` 就炸掉的檔案讀不到版本號——`stored_version()`
        # 那時候回 0，於是「比這一版新」那道門對它是開的。
        self._refuse_if_unreadable()
        self._refuse_if_newer()
        payload = {
            "version": FORMAT_VERSION,
            # **讀不出來的那幾筆原樣接回去。**這裡本來是拿 `saved()` 的結果
            # 整份覆寫，於是「存一份新的模式」會順手把另一份從硬碟上刪掉，
            # 而畫面上寫的是「已儲存」。定案【看不懂的檔案不准蓋】的否決清單
            # 上第二項就是這個做法：「讀得懂的留、讀不懂的丟，就是無聲的資料
            # 損失」。
            "presets": _with_unreadable_back(
                [p.to_dict() for p in others] + [preset.to_dict()], lost
            ),
        }
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(self.path)
        return preset

    def stored_version(self) -> int:
        """磁碟上那份自稱是第幾版。讀不到就當 0。"""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return 0
        if not isinstance(raw, dict):
            return 0
        try:
            return int(raw.get("version", 0))
        except (TypeError, ValueError):
            return 0

    def _refuse_if_newer(self) -> None:
        version = self.stored_version()
        if version > FORMAT_VERSION:
            raise PresetFileTooNew(
                f"{self.path} 是第 {version} 版寫的，這個版本只認得到第 "
                f"{FORMAT_VERSION} 版。沒有動它——請更新 AICC。"
            )

    def delete(self, name: str) -> bool:
        # **先擋再看有沒有這個名字。**看不懂的檔案，「找不到那個模式」這句話
        # 本身就不可信——它可能在那份檔案裡，只是這一版讀不出它的形狀。
        # 回一個 False 等於告訴使用者「沒有那個東西」，而那是假的。
        #
        # 整份讀不出來的那一種要先擋：那時候 `_partition()` 回的是兩個空的
        # list，於是 `lost` 是空的、版本號是 0，**下面兩道門一道都不會關**，
        # 而「找不到那個模式」這句話正是在那種情況下最不可信。
        self._refuse_if_unreadable()
        self._refuse_if_newer()
        clean = name.strip()
        stored, lost = self._partition()
        if lost:
            # 2026-08-24 補上的第二半：版本號相同、但**某一筆**讀不出來的時候，
            # 上面那段註解講的謊話一樣成立，而 `_refuse_if_newer()` 擋不到它。
            raise PresetEntryUnreadable(
                f"{self.path}: {len(lost)} unreadable preset entry/entries"
            )
        remaining = [p for p in stored if p.name != clean]
        if len(remaining) == len(stored):
            return False
        payload = {
            "version": FORMAT_VERSION,
            "presets": [p.to_dict() for p in remaining],
        }
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(self.path)
        return True


def apply_preset(preset: LineupPreset, current: Lineup) -> Lineup:
    """The preset's shape, with this project's own session bindings kept.

    2026-08-22 之前這裡擋的是「引擎跑不動的角色名」，而那份清單只有三個名字
    ——於是「角色只能叫這三個」變成了實質規格，儘管【角色表格】那條定案的
    不變式寫著**角色名與連線都可改**。

    現在名字完全自由，要擋的變成另一件事：**會寫檔的位子必須配得到一個
    AICC 給得起寫入權的廠商**（2026-09-01 起兩家都是）。排得出來但引擎會拒絕的
    陣容，使用者要按下開始執行才會知道——而那一步是要花錢的。

    ## 套用一個模式，會不會撞到「這一版讀不出來的那一格」

    會，而且是**最容易撞到的一條路**。套用會換掉每一格讀得出來的位子，但
    讀不出來的那幾格留在檔案裡（2026-08-24 定案，丟掉是無聲的資料損失），
    於是：使用者的三人陣容裡「核准」那一格因為廠牌這一版不認得而畫不出來
    → 他看到兩格，以為自己排錯了 → 他重新套用【三人】模式 → 模式裡也有一格
    叫「核准」→ `.aicc/sessions.json` 裡出現**兩格叫核准**。

    所以這裡要問一次，而且問的是 `unreadable_names` 不是 `taken_names`：
    讀得出來的那幾格本來就會被整批換掉，拿它們比對會把每一次「重新套用同
    一個模式」都擋掉。

    **這條會擋掉一件使用者做得到的事**（那個模式他套不了，而他看不到為什麼）
    ——留下的另一半在 docs/WHY.md：畫面要能講出「有一格這一版讀不出來」，
    那需要頁面原始檔與 `app/i18n.py`。在那之前，寧可擋下來也不要寫一份
    「退給核准」有兩個答案的陣容回硬碟。
    """
    from app.core.lineup import session_for_new_provider

    invisible = set(current.unreadable_names)
    clash = next((s.role for s in preset.lineup if s.role in invisible), "")
    if clash:
        # 訊息借 `name_already_taken()`：同名為什麼不行，這個 repo 只該有一份
        # 答案。在這裡再寫一句，兩份說法會各自演化。
        raise name_already_taken(clash)

    # ⛔ 這裡 2026-09-01 之前擋著「模式檔裡有一格是 Codex 又排成執行」。
    # 那條規則從來不是老闆講的（見 `app/core/lineup.py` 那一段），
    # 填上 `-s workspace-write` 之後兩家都寫得了檔，這道閘門永遠不成立。
    # 走不到的分支要拿掉——留著的話它的破壞永遠是綠的。

    return Lineup.of(
        # **`replace()`，不要一欄一欄抄。**一欄一欄抄的版本 2026-08-22 在這裡
        # 漏掉 `duty`：套用之後每個位子的職責都退回「照名字推」，而使用者
        # 自訂的名字推不出來，一律變成複查——他排好的「粗剪要能寫檔」會在
        # 套用的當下靜靜消失。加一欄就要記得回來加一次的地方，遲早會漏一次。
        replace(
            slot,
            # Kept from the project, not from the preset: a conversation id
            # belongs to the project it happened in.
            #
            # **而它也屬於廠牌**，所以問的是 `session_for_new_provider()`，
            # 不是 `current.session_for_role()`。差別在模式把某一格的廠牌換掉
            # 的那一次：舊的寫法會讓那一格帶著**上一個廠牌的對話 id**被寫回
            # `.aicc/sessions.json`（實測：複查那一格從 Claude 換成 Codex，
            # `claude-conversation-abc` 原封不動留在上面），而畫面把它畫成
            # 「已指定對話」、執行列那顆按鈕照樣亮著——他要按下去才會知道。
            # 換廠牌那顆下拉一直都問這一句，這裡以前沒問。
            session_id=session_for_new_provider(
                current.for_role(slot.role), slot.provider
            ),
            credential="",
        )
        for slot in preset.lineup
    )
