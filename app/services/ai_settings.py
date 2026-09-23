from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

# The Decision Gate's ASK_USER categories. Taken verbatim from the sentence
# that used to be hard-coded in prompt_builder, so the default behaviour is
# unchanged by making them configurable - a project that never touches this
# setting gets exactly the gate it had before.
# **存 key 不存字。**這幾個標籤同時進 Codex 的提示詞**與設定頁的畫面**，
# 而判準是「同時給人看又給 AI 看的，跟著人走」——AI 讀得懂兩種語言，
# 人只讀得懂一種。
#
# 那表示介面翻成英文的那一天，**提示詞會跟著變成英文**。那件事也許是對的
#（英文提示詞通常比較準），但它必須是一個**被決定的事，不是副作用**——
# 而擋著它的機制已經在了：`tests/test_prompt_golden.py` 那份快照會在那一刻
# 變紅，逼那個人明著說「我知道提示詞的語言也跟著換了」。
# 那四個深淺的**穩定代號**。
#
# 2026-08-24 之前它們是中文字串（「省額度」「標準」「深入」「自訂」），
# 而那個值**同時是存進設定檔的東西、也是 `profile()` 裡的比對字串**——
# 介面翻成英文的那一刻，既有使用者的設定檔會對不上任何一個分支，
# **安靜地退回最省的那一張**。他選過「深入」，付過那個代價，
# 然後某一次更新之後它變回省額度，而畫面上完全正常。
#
# 這是【拿中文當查詢的鍵】那個病的第四個入口，而且是唯一一個
# **會影響到已經存在硬碟上的資料**的。
PROFILE_THRIFTY = "thrifty"
PROFILE_STANDARD = "standard"
PROFILE_DEEP = "deep"
PROFILE_CUSTOM = "custom"

PROFILE_IDS = (PROFILE_THRIFTY, PROFILE_STANDARD, PROFILE_DEEP, PROFILE_CUSTOM)

PROFILE_LABEL_KEYS = {
    PROFILE_THRIFTY: "profile.thrifty",
    PROFILE_STANDARD: "profile.standard",
    PROFILE_DEEP: "profile.deep",
    PROFILE_CUSTOM: "profile.custom",
}

# 既有設定檔裡存的舊值。**留著是因為使用者的硬碟上有它們**——
# 拿掉的那一天，每一個 2026-08-24 之前設定過的專案都會退回省額度。
_LEGACY_PROFILE_NAMES = {
    "省額度": PROFILE_THRIFTY,
    "標準": PROFILE_STANDARD,
    "深入": PROFILE_DEEP,
    "自訂": PROFILE_CUSTOM,
}


def profile_id(name: str) -> str:
    """把「存進檔案的那個值」正規化成代號。**認得舊的中文值。**"""
    text = (name or "").strip()
    if text in PROFILE_IDS:
        return text
    return _LEGACY_PROFILE_NAMES.get(text, PROFILE_CUSTOM)


def profile_label(name: str) -> str:
    """那個深淺在畫面上叫什麼。"""
    from app.i18n import t

    return t(PROFILE_LABEL_KEYS[profile_id(name)])


GATE_CATEGORY_KEYS = {
    "direction": "gate.category.direction",
    "delete": "gate.category.delete",
    "schema": "gate.category.schema",
    "permission": "gate.category.permission",
    "security": "gate.category.security",
    "external": "gate.category.external",
    "install": "gate.category.install",
    "paid": "gate.category.paid",
}
DEFAULT_GATE_CATEGORIES = tuple(GATE_CATEGORY_KEYS)


def gate_categories() -> dict[str, str]:
    """代號 → 給人看的字。**用到才取**，不在 import 的當下凍住。"""
    from app.i18n import t

    return {key: t(name) for key, name in GATE_CATEGORY_KEYS.items()}

# The clauses that are NOT configurable, in the order the prompt states them.
#
# These live here rather than only inside the prompt string so the settings
# page can show them without keeping its own copy. A hard-coded list in the
# HTML would drift: the page would claim "these always ask" while the prompt
# said something else, and the screen would be the more convincing of the two.
#
# Why they are not settings at all: a checkbox that could switch off "ask
# before rewriting git history" is a footgun sold as a feature. The eight
# categories above are judgement calls about a project's workflow; these four
# are the floor that makes the gate worth having.
#
# 2026-08-11: the paragraph above described an intention, not the code. The
# field reached the page and the page never read it - it drew its own
# hard-coded four, exactly the drift the comment says this exists to prevent.
# Each clause now carries the sentence shown under it, because a bare label
# list would have made the page keep its own notes anyway, and a clause whose
# note went missing would render as a promise with no explanation.
#
# 「一句沒有人讀的註解，跟沒有那句註解，中間的差別是它會讓下一個人以為
#   這件事已經處理好了。」
_GATE_FLOOR_KEYS = (
    ("gate.floor.outside.label", "gate.floor.outside.note"),
    ("gate.floor.secrets.label", "gate.floor.secrets.note"),
    ("gate.floor.git.label", "gate.floor.git.note"),
    ("gate.floor.unsure.label", "gate.floor.unsure.note"),
)


def gate_floor_clauses() -> tuple[tuple[str, str], ...]:
    """那四條不可關掉的，以及它們底下那一句說明。"""
    from app.i18n import t

    return tuple((t(label), t(note)) for label, note in _GATE_FLOOR_KEYS)


def gate_floor() -> tuple[str, ...]:
    """提示詞裡那一份，值跟畫面上完全一樣。

    刻意共用：一份寫在畫面、一份寫在提示詞的話，畫面會宣稱「這幾件一定會問」
    而提示詞說的是別的——**而畫面是比較有說服力的那一個**。
    """
    return tuple(label for label, _ in gate_floor_clauses())


@dataclass
class AIExecutionSettings:
    # 出廠跑**開發序列**。**老闆 2026-08-26 拍板**（`DECISIONS.md`
    #「2026-08-26 拍板三件」那一節），程式 2026-08-27 換過來。
    # 拍板的日子跟改程式的日子不是同一天，而寫錯的那一個會讓下一個人
    # 去 08-27 的紀錄裡找一條不在那裡的定案。
    #
    # **出廠值就是生效值**：他那台機器兩個 `ai-settings.json` 都不存在，
    # 所以他按下開始跑的就是這一行——不是「他還沒去設定的那個值」。
    # 執行／複查／駁回／拍板四件事全都在開發序列這條路上；計畫序列只有
    # 出計畫與審計畫兩步，而他排的五格裡有一格職責是「執行」，
    # 那一格在計畫序列裡只會被叫去讀計畫。
    #
    # **這個值只寫在這裡一次。**底下 `AISettingsStore.profile()` 那三張
    # 出廠表格**刻意不寫 `workflow_mode`**——它們各寫一份的那段日子裡，
    # 改這一行等於沒改：使用者挑一次深淺就換回去了。
    workflow_mode: str = "development"
    # 出廠 0，而且這一輪**沒有動它**。讀它的只有兩處：
    # `workflow_worker._run_planning`，與底下 `expected_model_calls` 的
    # 計畫分支。**開發序列從頭到尾一次都不看。**
    #（`ai_settings_panel` 與 `settings_web_panel` 也讀，但那是把值畫出來給人
    # 改，不是拿它決定要跑什麼。Qt 那一格在不是計畫序列的時候是灰的；
    # 網頁設定頁那一格**還沒有跟著灰**——見 2026-08-27 交給 PM 的那一條。）
    # 0 在開發序列裡不代表「少跑幾輪」，它代表「這個欄位這條路用不到」。
    planning_rounds: int = 0
    claude_model: str = "sonnet"
    claude_effort: str = "medium"
    claude_speed: str = "standard"
    claude_permission_mode: str = "auto"
    claude_timeout_minutes: int = 30
    claude_max_turns: int = 4
    claude_verbosity: str = "concise"
    codex_model: str = "gpt-5.5"
    codex_effort: str = "medium"
    codex_speed: str = "model_default"
    codex_verbosity: str = "low"
    max_fix_attempts: int = 1
    # 出廠沿用同一段對話，不是每次任務都開新的。
    #
    # 老闆 2026-08-21 的原話：「要像影片中的**使用同一個對話框** 不然記憶中斷
    # 加超浪費 TOKEN **也沒辦法訓練自己的 ai**」。原本出廠是 True，於是他每
    # 交辦一件新事情，兩個角色都是完全陌生地重來——他想要的累積每次歸零。
    #
    # 代價要講清楚：沿用代表那段對話會一直長，長到最後是**廠商那一側**幫它
    # 壓縮，而那次壓縮我們管不到。所以這格是使用者改得動的，需要重來的時候
    # 打開它就好。
    force_new_sessions: bool = False
    auto_session_bootstrap: bool = True
    auto_memory_maintenance: bool = True
    context_profile: str = "minimal"
    profile_name: str = PROFILE_THRIFTY
    # Which categories force ASK_USER at the Decision Gate. These used to be a
    # fixed sentence inside the prompt; making them a setting is only
    # meaningful because the prompt is built from this tuple - a toggle that
    # did not change what Codex is told would be a lie on the settings page.
    #
    # A tuple, not a list: dataclass defaults must be immutable, and every
    # instance sharing one mutable list is the classic way a "per-project"
    # setting quietly becomes global.
    gate_on: tuple[str, ...] = DEFAULT_GATE_CATEGORIES
    # Whether a run writes a milestone plan up front. Was unconditional.
    long_task_mode: bool = True

    VALID_WORKFLOW_MODES = {"planning", "development"}
    VALID_CLAUDE_EFFORTS = {
        "auto",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultracode",
    }
    VALID_CLAUDE_PERMISSION_MODES = {
        "auto",
        "acceptEdits",
        "default",
        "plan",
    }
    VALID_CODEX_EFFORTS = {"low", "medium", "high", "xhigh"}
    VALID_VERBOSITY = {"concise", "standard", "detailed"}
    VALID_CODEX_VERBOSITY = {"low", "medium", "high"}
    VALID_CONTEXT_PROFILES = {"minimal", "balanced", "strict"}

    def normalized(self) -> "AIExecutionSettings":
        workflow_mode = (
            self.workflow_mode
            if self.workflow_mode in self.VALID_WORKFLOW_MODES
            else "planning"
        )
        claude_effort = (
            self.claude_effort
            if self.claude_effort in self.VALID_CLAUDE_EFFORTS
            else "medium"
        )
        claude_permission_mode = (
            self.claude_permission_mode
            if self.claude_permission_mode
            in self.VALID_CLAUDE_PERMISSION_MODES
            # 認不得的值倒向最嚴格的那一邊，不是最寬鬆的。這裡本來是 "auto"，
            # 而 "auto" 是這四個裡最寬鬆的一個——**一個打錯的字會靜靜地升權**。
            # 設定頁那張壞掉的對照表就是靠這條把三個選項全部變成 auto 的：
            # 錯誤本身沒有被擋下來，是被翻譯成了「全部放行」。
            else "default"
        )
        codex_effort = (
            self.codex_effort
            if self.codex_effort in self.VALID_CODEX_EFFORTS
            else "medium"
        )
        claude_verbosity = (
            self.claude_verbosity
            if self.claude_verbosity in self.VALID_VERBOSITY
            else "concise"
        )
        codex_verbosity = (
            self.codex_verbosity
            if self.codex_verbosity in self.VALID_CODEX_VERBOSITY
            else "low"
        )

        timeout_minutes = int(self.claude_timeout_minutes)
        if timeout_minutes != 0:
            timeout_minutes = max(5, min(timeout_minutes, 720))

        max_turns = int(self.claude_max_turns)
        if max_turns != 0:
            max_turns = max(1, min(max_turns, 200))

        return replace(
            self,
            workflow_mode=workflow_mode,
            planning_rounds=max(0, min(int(self.planning_rounds), 5)),
            claude_model=(self.claude_model.strip() or "sonnet"),
            claude_effort=claude_effort,
            claude_speed="standard",
            claude_permission_mode=claude_permission_mode,
            claude_timeout_minutes=timeout_minutes,
            claude_max_turns=max_turns,
            claude_verbosity=claude_verbosity,
            codex_model=(self.codex_model.strip() or "gpt-5.5"),
            codex_effort=codex_effort,
            codex_speed="model_default",
            codex_verbosity=codex_verbosity,
            max_fix_attempts=max(0, min(int(self.max_fix_attempts), 2)),
            force_new_sessions=bool(self.force_new_sessions),
            auto_session_bootstrap=bool(self.auto_session_bootstrap),
            auto_memory_maintenance=bool(self.auto_memory_maintenance),
            context_profile=(
                self.context_profile
                if self.context_profile in self.VALID_CONTEXT_PROFILES
                else "minimal"
            ),
            profile_name=profile_id(self.profile_name),
            # Order follows GATE_CATEGORY_KEYS rather than however it arrived, so
            # the prompt reads the same regardless of the order the UI sent the
            # toggles in, and two settings files with the same categories
            # compare equal. Unknown keys are dropped; JSON round-trips a tuple
            # back as a list, hence the rebuild rather than a cast.
            gate_on=tuple(
                key for key in GATE_CATEGORY_KEYS if key in set(self.gate_on or ())
            ),
            long_task_mode=bool(self.long_task_mode),
        )

    @property
    def expected_model_calls(self) -> int:
        if self.workflow_mode == "planning":
            return 1 + (2 * self.planning_rounds)
        memory_calls = 2 if self.auto_memory_maintenance else 0
        return 2 + (2 * self.max_fix_attempts) + memory_calls

    @property
    def claude_timeout_seconds(self) -> int | None:
        if self.claude_timeout_minutes == 0:
            return None
        return self.claude_timeout_minutes * 60

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "AIExecutionSettings":
        if not isinstance(raw, dict):
            return cls()

        allowed = {
            field_name
            for field_name in cls.__dataclass_fields__
            if not field_name.startswith("VALID_")
        }
        payload = {
            key: value
            for key, value in raw.items()
            if key in allowed
        }
        try:
            return cls(**payload).normalized()
        except (TypeError, ValueError):
            return cls()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalized())


class AISettingsStore:
    FILE_NAME = "ai-settings.json"

    def __init__(self, project_path: Path | None = None) -> None:
        appdata = os.environ.get("APPDATA")
        if appdata:
            self.global_dir = Path(appdata) / "AIControlCenter"
        else:
            self.global_dir = Path.home() / ".aicontrolcenter"
        self.global_dir.mkdir(parents=True, exist_ok=True)
        self.global_path = self.global_dir / self.FILE_NAME
        self.project_path = (
            project_path.expanduser().resolve()
            if project_path is not None
            else None
        )

    @staticmethod
    def profile(name: str) -> AIExecutionSettings:
        """一張出廠表格。**深淺是深淺，序列是序列。**

        這三張表格 2026-08-27 之前各自寫死一份 `workflow_mode="planning"`，
        而那是「同一件事寫三份」最貴的那個形狀：出廠值改在 dataclass 上，
        使用者**挑一次深淺就換回去了**，而畫面上完全正常。

        現在三張都**不寫 `workflow_mode`**——不是忘了，是這一格不歸這張表管，
        所以它拿 `AIExecutionSettings` 的出廠值。要換出廠序列的人只改那一行。
        守著這件事的是
        `tests/test_a_brand_new_machine_runs_the_sequence_that_dispatches_his_five.py`
        ::test_the_three_factory_tables_do_not_drift_from_the_dataclass。

        `planning_rounds` 留在表格裡：那是「計畫序列要來回幾輪」，是深淺
        真正管得到的東西，而開發序列一次都不讀它。
        """
        # **認得舊的中文值**，不然既有設定檔會全部落到最省的那一張。
        normalized_name = profile_id(name)

        if normalized_name == PROFILE_DEEP:
            return AIExecutionSettings(
                planning_rounds=2,
                claude_model="sonnet",
                claude_effort="high",
                claude_permission_mode="auto",
                claude_timeout_minutes=360,
                claude_max_turns=50,
                claude_verbosity="standard",
                codex_model="gpt-5.5",
                codex_effort="high",
                codex_verbosity="medium",
                max_fix_attempts=2,
                force_new_sessions=False,
                auto_session_bootstrap=True,
                auto_memory_maintenance=True,
                context_profile="strict",
                profile_name=PROFILE_DEEP,
            )

        if normalized_name == PROFILE_STANDARD:
            return AIExecutionSettings(
                planning_rounds=1,
                claude_model="sonnet",
                claude_effort="medium",
                claude_permission_mode="auto",
                claude_timeout_minutes=180,
                claude_max_turns=20,
                claude_verbosity="concise",
                codex_model="gpt-5.5",
                codex_effort="medium",
                codex_verbosity="low",
                max_fix_attempts=1,
                force_new_sessions=False,
                auto_session_bootstrap=True,
                auto_memory_maintenance=True,
                context_profile="balanced",
                profile_name=PROFILE_STANDARD,
            )

        return AIExecutionSettings(
            planning_rounds=0,
            claude_model="sonnet",
            claude_effort="medium",
            claude_permission_mode="auto",
            claude_timeout_minutes=30,
            claude_max_turns=4,
            claude_verbosity="concise",
            codex_model="gpt-5.5",
            codex_effort="medium",
            codex_verbosity="low",
            max_fix_attempts=1,
            force_new_sessions=False,
            auto_session_bootstrap=True,
            auto_memory_maintenance=True,
            context_profile="minimal",
            profile_name=PROFILE_THRIFTY,
        )

    def set_project(self, project_path: Path) -> None:
        self.project_path = project_path.expanduser().resolve()

    def load_global(self) -> AIExecutionSettings:
        return self._load(self.global_path, self.profile(PROFILE_THRIFTY))

    def load_project(self) -> AIExecutionSettings:
        global_settings = self.load_global()
        project_path = self.project_settings_path
        if project_path is None or not project_path.is_file():
            return global_settings
        return self._load(project_path, global_settings)

    def save_global(self, settings: AIExecutionSettings) -> None:
        self._save(self.global_path, settings)

    def save_project(self, settings: AIExecutionSettings) -> None:
        project_path = self.project_settings_path
        if project_path is None:
            raise ValueError("Project path is not configured.")
        self._save(project_path, settings)

    @property
    def project_settings_path(self) -> Path | None:
        if self.project_path is None:
            return None
        path = self.project_path / ".aicc" / self.FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _load(
        path: Path,
        fallback: AIExecutionSettings,
    ) -> AIExecutionSettings:
        if not path.is_file():
            return fallback.normalized()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return fallback.normalized()
        return AIExecutionSettings.from_dict(raw)

    @staticmethod
    def _save(path: Path, settings: AIExecutionSettings) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(
                settings.to_dict(),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temp_path.replace(path)
