r"""計畫序列排不到「複查」那一格的時候，要**講出真正的原因**，不是叫他去改名字。

## 這一條是老闆的五節點測試撞出來的（2026-08-26 實機量到）

他排的五格：發想／規劃／ui設計（職責都是「擬做法」）、執行（「執行」）、
審閱（「核准」）。**一格複查都沒有**——而那份陣容一點問題都沒有。

他這台機器上兩個設定檔都不存在，所以出廠值就是生效值——而 2026-08-26 那天
出廠值是 `workflow_mode="planning"`，他什麼都沒選就被放上了計畫序列。

> **2026-08-27 起出廠值改成 `development`**（老闆拍板；守它的是
> `tests/test_a_brand_new_machine_runs_the_sequence_that_dispatches_his_five.py`）。
> 所以底下這一整支測的**不再是「他什麼都不改會走到的那條路」**，而是
> 「他自己去設定頁把序列切成計畫序列之後會走到的那條路」——那條路一步都
> 沒有變，這裡每一條斷言也一樣有效。所以底下那個 `workflow_mode="planning"`
> 現在是**測試自己挑的值**，不是出廠值；它是這份檔案的主題，不是背景。

計畫序列的第一步
（`orchestrator.run_codex_plan`）找的是「複查」那一格，找不到就退回寫死的
`reviewer`，而下游只看得到那個名字：

    NotImplementedError: 這個專案的陣容裡沒有「reviewer」這一格，但工作流
    要派工給它。目前排的是：node1、node2、node3、node4、node5。
    請到陣容頁重新編組。

**改壞的樣子就是它 2026-08-26 之前的樣子**：整輪在按下開始的第一秒收成
`failed` 加一坨 traceback，而畫面上那句話**把他送去改一個沒有壞的東西**
——他會去陣容頁改名字，而壞的不是名字。

## 使用者現在會看到什麼

一個 `partial` 的結局（不是紅燈、不算進「今日錯誤 N 件」），加上這一句：

> 這一輪跑的是「計畫序列」，而它每一步都派給職責是「複查」的那一格。
> 這份陣容的 5 格（發想、規劃、ui設計、執行、審閱）裡一格都不是，
> 所以一次派工都沒有發生——AICC 不替你挑一格代打。
> 兩條路：到陣容頁把其中一格的職責改成「複查」，
> 或到「AI 執行設定」把序列換成「開發序列」。

一次派工都沒有發生，所以**一毛額度都沒有花**。

## 順帶守著「同一件事只有一個名字」

那句話裡的「計畫序列」跟陣容頁徽章上的是**同一個 key**（`sequence.planning`）。
2026-08-26 之前設定面板叫它「計畫發想」、陣容頁叫它「計畫序列」，
而他要在那兩個畫面之間對照：跑哪一條在設定面板選，那一條下每一格做什麼
在陣容頁看。兩個名字，他沒有辦法知道那是同一個東西。
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import outcomes  # noqa: E402
from app.core.lineup import (  # noqa: E402
    DUTY_APPROVE,
    DUTY_PLAN,
    DUTY_REVIEW,
    DUTY_WORK,
    VALID_DUTIES,
    AgentSlot,
    Lineup,
)
from app.core.orchestrator import Orchestrator  # noqa: E402
from app.core.workflow_worker import (  # noqa: E402
    NO_SEAT_TEXT_KEYS,
    WorkflowWorker,
    why_this_sequence_cannot_run,
)
from app.i18n import STRINGS, t  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

# 老闆真的排的那五格（名字是他取的，職責是他從下拉選的）。
# **一格複查都沒有**，而這份陣容沒有任何一格是錯的。
HIS_FIVE = [
    AgentSlot("發想", "codex", duty=DUTY_PLAN),
    AgentSlot("規劃", "claude", duty=DUTY_PLAN),
    AgentSlot("ui設計", "claude", duty=DUTY_PLAN),
    AgentSlot("執行", "claude", duty=DUTY_WORK),
    AgentSlot("審閱", "codex", duty=DUTY_APPROVE),
]

# 有人複查、但**沒有人執行**。計畫序列的第一步派得出去，
# 第二步（審計畫）派不出去——用來確認「選哪一句」是真的在選。
NOBODY_TO_BUILD = [
    AgentSlot("node1", "claude", duty=DUTY_PLAN),
    AgentSlot("node2", "codex", duty=DUTY_REVIEW),
    AgentSlot("node3", "claude", duty=DUTY_APPROVE),
]

A_PLAN = cli_result("最終計畫內容")


def _run(tmp_path: Path, monkeypatch, slots, script=None, rounds: int = 0):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 做一件事\n", encoding="utf-8")
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    SessionRegistry(project).save_lineup(Lineup.of(slots))

    runner_class = scripted_cli_runner_class(list(script or []))
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            # **他自己選的那一條**（2026-08-27 起出廠值是開發序列，見檔頭）。
            # 這一支的主題就是這條序列，所以這個值寫在這裡是對的——
            # 它不再宣稱「這是他什麼都不改會跑的那一條」。
            workflow_mode="planning",
            planning_rounds=rounds,
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
        ),
    )
    completed: list = []
    failed: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed, runner_class.calls


def test_it_stops_instead_of_sending_him_to_fix_a_seat_name(
    tmp_path, monkeypatch
) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：`NotImplementedError: 這個專案的陣容裡沒有「reviewer」這一格`
    ——一坨 traceback，而他照著它去陣容頁改名字。
    """
    completed, failed, _ = _run(tmp_path, monkeypatch, HIS_FIVE)
    assert not failed, (
        "按下開始的第一秒丟了 traceback，而不是收在一個講得出理由的結局：\n"
        + "\n".join(str(f)[:400] for f in failed)
    )
    assert completed, "既沒有失敗也沒有完成——那一輪不知道去哪裡了"


def test_the_ending_is_the_one_that_was_written_for_this(
    tmp_path, monkeypatch
) -> None:
    """收的是 `NO_SEAT_FOR_DUTY`。

    只斷言「沒有丟例外」的話，收成 `PLANNING_COMPLETED` 也會通過——而那是
    這個專案最在意的那一種假訊號：一輪一次派工都沒發生的執行，
    被記成計畫做完了。
    """
    completed, _, _ = _run(tmp_path, monkeypatch, HIS_FIVE)
    assert completed[-1]["workflow_status"] == "NO_SEAT_FOR_DUTY", completed[-1]


def test_the_ending_has_a_row_in_the_outcome_table(
    tmp_path, monkeypatch
) -> None:
    """`app/core/outcomes.py` 那張表要有它的列。

    沒有的話 `_complete` 把 `outcome_group` 寫成空字串，而首頁上是**一格
    沒有顏色也沒有標籤的東西**。而且它不准歸「跑完了」（薄荷色、算進
    「今日完成 N 件」，他不會再去看它），也不准歸「再跑一次就好」
    （同一份陣容再跑一次會在同一秒停在同一個地方）。
    """
    completed, _, _ = _run(tmp_path, monkeypatch, HIS_FIVE)
    result = completed[-1]
    assert result.get("outcome_group") == outcomes.GROUP_SETTING, result

    described = outcomes.describe(result)
    assert described.style == "partial", described
    assert described.status in outcomes.known_statuses()


def test_it_says_the_duty_is_missing_not_a_seat_he_never_had(
    tmp_path, monkeypatch
) -> None:
    """畫面上那句話要講**真正的原因**，而且要講得出下一步。

    真正的原因是「這份陣容沒有一格的職責是複查」，不是
    「陣容裡沒有 reviewer 這一格」——後者叫他去修一個他從來沒有排過、
    也不需要排的名字。
    """
    completed, _, _ = _run(tmp_path, monkeypatch, HIS_FIVE)
    said = completed[-1].get("verification_problem", "")

    assert said == why_this_sequence_cannot_run(
        DUTY_REVIEW, tuple(s.role for s in HIS_FIVE), "planning"
    ), said
    # **而且它要真的到得了畫面。**收場表那一列少了 `detail_key` 的話，
    # 這一句會被那張表的罐頭句子蓋掉，而上面那個 `==` 照樣是綠的。
    assert outcomes.describe(completed[-1]).stage == said
    # 一個他的陣容裡沒有的出廠名字，不准出現在他要讀的那句話裡。
    assert "reviewer" not in said, said
    # 講得出**回哪一頁、改什麼**。「執行失敗」四個字寫進紀錄等於沒寫。
    assert "陣容頁" in said, said
    assert "複查" in said, said
    # 他排的那五格要被念出來——講不出名字的說明等於沒有說明。
    for seat in ("發想", "規劃", "ui設計", "執行", "審閱"):
        assert seat in said, (seat, said)


def test_the_sentence_calls_the_sequence_by_its_one_name(
    tmp_path, monkeypatch
) -> None:
    """那句話裡的序列名字，跟陣容頁徽章上的是**同一份**。

    他要在兩個畫面之間對照，而這句話是引擎唯一一次主動點名那條序列。
    在這裡再抄一次那個詞，等於替第三個名字先開好一扇門。
    """
    completed, _, _ = _run(tmp_path, monkeypatch, HIS_FIVE)
    said = completed[-1].get("verification_problem", "")
    badge = Orchestrator.sequence_for("planning", duties=(DUTY_REVIEW,))
    assert badge["label"] in said, (badge["label"], said)


def test_it_picks_the_sentence_for_the_duty_that_is_missing(
    tmp_path, monkeypatch
) -> None:
    """**測的是「選哪一句」，不是「那句話長什麼樣」。**

    缺複查跟缺執行的出路不一樣：前者可以換序列，後者可以把討論回合設成 0。
    挑錯那一句的時候，四句話都還是好好的——所以要有一個案例缺的是**別的**
    職責，而它拿到的是**別的**那一句。
    """
    completed, failed, _ = _run(
        tmp_path, monkeypatch, NOBODY_TO_BUILD, script=[A_PLAN], rounds=1
    )
    assert not failed, failed
    said = completed[-1].get("verification_problem", "")

    seats = tuple(s.role for s in NOBODY_TO_BUILD)
    assert said == why_this_sequence_cannot_run(DUTY_WORK, seats, "planning")
    assert said != why_this_sequence_cannot_run(DUTY_REVIEW, seats, "planning")
    # 缺執行的那條出路：把討論回合設成 0，計畫序列就不需要有人審計畫。
    assert "討論回合" in said, said


def test_it_does_not_pick_one_to_stand_in(tmp_path, monkeypatch) -> None:
    """**不准挑一格代打。**

    挑到的那一格會拿到使用者從來沒有授權給它的工作——他排的「審閱」是
    核准，不是複查，而複查那一步會讀到整份產出並且替他訂驗收標準。

    量的是最硬的那一項：**一次 CLI 都沒有被叫起來。**代打的話會有一次。
    """
    completed, _, calls = _run(tmp_path, monkeypatch, HIS_FIVE)
    assert calls == [], f"派了 {len(calls)} 次工，有一格被挑去代打了：{calls}"
    assert completed[-1].get("claude_success") is False
    assert not completed[-1].get("claude_output")
    assert not completed[-1].get("codex_output")


def test_a_line_with_a_reviewing_seat_is_not_stopped(
    tmp_path, monkeypatch
) -> None:
    """**不准擋過頭。**

    一條什麼都拒絕的門，跟一個永遠說可以的門一樣沒用。這一條確認上面
    那幾條擋的是「真的沒有一格複查」，不是「陣容裡有它不認得的名字」。
    """
    with_reviewer = list(HIS_FIVE) + [
        AgentSlot("複查那一格", "codex", duty=DUTY_REVIEW)
    ]
    completed, failed, calls = _run(
        tmp_path, monkeypatch, with_reviewer, script=[A_PLAN]
    )
    assert not failed, failed
    assert completed[-1]["workflow_status"] != "NO_SEAT_FOR_DUTY", completed[-1]
    assert len(calls) == 1, calls


def test_every_duty_has_a_sentence() -> None:
    """四種職責每一種都要有自己的一句話。

    少一種的話，那一種缺席的時候 `NO_SEAT_TEXT_KEYS[duty]` 會丟 KeyError
    ——而那是在**使用者按下開始的那一刻**丟，收在 `except NoSeatWithDuty`
    裡面，沒有任何一個 except 接得住它。這一條讓它在這裡紅。
    """
    assert set(NO_SEAT_TEXT_KEYS) == set(VALID_DUTIES), NO_SEAT_TEXT_KEYS
    for duty, key in NO_SEAT_TEXT_KEYS.items():
        assert key in STRINGS, (duty, key)
        said = why_this_sequence_cannot_run(duty, ("a", "b"), "planning")
        # 每一句都要塞得進狀態列。超過的話 `_last_words()` 切的是**前面**，
        # 而前面那半句正是「為什麼沒跑」。
        assert len(said) <= outcomes.STAGE_MAX_CHARS, (duty, len(said))
        assert "陣容頁" in said, (duty, said)


# ---------------------------------------------------------------------------
# 同一件事只有一個名字（交辦事項二）
# ---------------------------------------------------------------------------


def _combo_key(mode: str) -> str:
    """AI 執行設定面板上，那個下拉的 `{mode}` 那一項用的是哪一個 key。

    讀原始碼而不是把面板建起來：這條要問的是「**那兩個畫面用的是不是同
    一份字**」，而答案在那一行寫的是哪一個 key。建面板量出來的字相同時，
    兩個 key 各存一份一樣的中文也會過——而那正是它 2026-08-26 之前的樣子。
    """
    tree = ast.parse(
        Path("app/ui/ai_settings_panel.py").read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) != 2:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "addItem":
            continue
        target = node.args[1]
        if not isinstance(target, ast.Constant) or target.value != mode:
            continue
        label = node.args[0]
        assert isinstance(label, ast.Call) and label.args, ast.dump(label)
        first = label.args[0]
        assert isinstance(first, ast.Constant), ast.dump(label)
        return first.value
    raise AssertionError(f"面板上找不到 {mode!r} 那一項")


@pytest.mark.parametrize(
    "mode,key", [("planning", "sequence.planning"), ("development", "sequence.development")]
)
def test_both_screens_call_the_sequence_by_the_same_name(
    mode: str, key: str
) -> None:
    """設定面板的下拉與陣容頁的徽章，用的是**同一個 key**。

    2026-08-26 之前是兩個 key、兩個名字：面板寫「計畫發想／正式開發」，
    徽章寫「計畫序列／開發序列」。他要在那兩個畫面之間對照——跑哪一條在
    面板選，那一條下每一格這一輪做什麼在陣容頁看——**而他沒有辦法知道
    那是同一個東西**。

    比 key 不比中文：兩個 key 各存一份一樣的字也會讓「比中文」那種寫法
    過關，而那種東西會在下一次有人改其中一邊的時候安靜地漂開。
    """
    assert _combo_key(mode) == key
    badge = Orchestrator.sequence_for(mode, duties=(DUTY_REVIEW,))
    assert badge["label"] == t(key)


def test_the_retired_name_did_not_come_back() -> None:
    """「計畫發想／正式開發」不准再出現在介面文字裡。

    留一份在別的 key 底下的話，第三個畫面會撿它去用，而這一輪做的事就白做了。

    **只掃 `app/i18n.py`**（介面文字），不掃 `prompt_builder.py`——那裡的
    「計畫發想階段」是**寫給 AI 看的提示詞**，不是他讀的畫面，而改它會改
    到一次付費執行真正送出去的內容。那件事另外問，見交接文件。
    """
    stale = {
        key: value
        for key, value in STRINGS.items()
        if "計畫發想" in value or "正式開發" in value
    }
    assert not stale, (
        "介面文字裡又出現了那條序列的第二個名字：\n  "
        + "\n  ".join(f"{k}：{v[:40]}⋯" for k, v in stale.items())
        + "\n\n序列的名字只有一份：`sequence.planning`／`sequence.development`。"
    )
