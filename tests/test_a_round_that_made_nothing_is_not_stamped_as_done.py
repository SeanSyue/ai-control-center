"""一個檔案都沒改到的那一輪，不准蓋同意章、不准收成「工作流程已完成」。

## 改壞的樣子

拿掉 `app/core/steps/approval.py::_nothing_was_produced` 就是它原本的樣子：

`get_git_diff()` 回空字串 → 四個章的指紋全部是 `sha256("")` 的**同一個常數**
→ 版本判定說「完全一致」→ 核准那一格拿到的提示詞裡寫著「（這一輪沒有產出
可以比對。）」→ **然後它照樣蓋得出同意章**，整輪收成 `COMPLETED`。

`ApprovalChain.readiness` 那五道（自己驗自己／有紅的／沒得驗／版本分岔／
缺章）沒有一道問「產出是不是空的」，而 `Stamp` 的守衛只擋「指紋這一欄是
空白」——空字串的 sha256 不是空白。

## 使用者會看到什麼

首頁上是薄荷色的「PASS／工作流程已完成」，跟一次真的做完了的執行**一個像素
都不差**。所以他不會去看它——而那一輪其實什麼都沒做出來。

## 這份同時守「不准擋過頭」

一條會誤報的檢查比沒有更貴：它會訓練人忽略紅燈。所以下面有兩條案例釘著
**不准誤擋**：

* **只新增了檔案的那一輪**——`git diff` 看不到 untracked，diff 是空的，
  而它真的做出了東西。那一輪照樣要收成完成。
* **計畫序列**——它的產出是一份計畫書而不是檔案改動，而且它從頭到尾不經過
  拍板那一扇門。

## 它擋不住什麼

* **擋不住「改了東西，但改的是垃圾」**——那是核准那一格的工作。
* **擋不住 `get_git_diff()` 自己失敗**：那時候它回的是 `GIT_DIFF_ERROR:⋯`，
  不是空字串，所以這一道放行。那是另一個洞。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import outcomes  # noqa: E402
from app.core.lineup import (  # noqa: E402
    DUTY_REVIEW,
    DUTY_WORK,
    AgentSlot,
    Lineup,
)
from app.core.orchestrator import Orchestrator  # noqa: E402
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.approval_chain import (  # noqa: E402
    SUBJECT_WORK,
    StampKind,
    StampLog,
)
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    SETUP_STEPS,
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

PASS_REVIEW = (
    '{"status": "PASS", "summary": "看過了", "issues": [], '
    '"instructions": [], "question": ""}'
)

# 兩格：一格複查（同時是訂標準的那一格）、一格做事。沒有排核准，所以驗簽章
# 那一格由「最後一格」自動擔任——`lineup.chain_checker` 的規定。
WHOLE = [
    AgentSlot("複查", "codex", duty=DUTY_REVIEW),
    AgentSlot("實作", "claude", duty=DUTY_WORK),
]

HAPPY = [
    cli_result('{"result": "做完了"}'),
    cli_result("1 passed"),
    cli_result(PASS_REVIEW),
]

# 核准那一格的提示詞開場白。假的 CLI 就是靠它認出「這一次派的是拍板」。
APPROVAL_OPENING = "你是此專案的核准者"


def test_the_marker_this_file_watches_for_still_exists() -> None:
    """前提檢查：底下「有沒有派核准那一格」是靠這句開場白認的。

    它哪天改了字而這裡沒跟上，`test_the_approval_seat_is_never_dispatched`
    會**永遠通過**——一條比不到東西的檢查，讀起來跟一條全過的檢查一模一樣。
    """
    assert any(APPROVAL_OPENING in marker for marker, _answer in SETUP_STEPS)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 做一個錯題整理小工具\n", encoding="utf-8"
    )
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    return project


def produced(monkeypatch, diff: str, changed: list[str]) -> None:
    """這一輪做出了什麼。

    **換掉 `Orchestrator` 上那兩支，不是換掉某一個呼叫端**：`get_git_diff()`
    在引擎裡被叫了六次（蓋章、複查、拍板各一路），綁在呼叫端的替換在下一次
    搬家之後會安靜地什麼都沒換到，而測試照樣是綠的。

    也刻意**不去真的建一個 git repo**：這一輪要驗的是「產出是空的時候引擎
    怎麼收場」，不是 `git diff` 的參數對不對——而 `get_git_diff` 本身正在
    別人手上改。把它釘進這裡，會讓那一份改動撞紅一條跟它無關的測試。
    """
    monkeypatch.setattr(Orchestrator, "get_git_diff", lambda self: diff)
    monkeypatch.setattr(
        Orchestrator, "get_changed_files", lambda self: list(changed)
    )


def _run(project: Path, monkeypatch, script=None, settings=None):
    QApplication.instance() or QApplication([])
    runner_class = scripted_cli_runner_class(
        list(HAPPY if script is None else script)
    )
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)
    worker = WorkflowWorker(
        project,
        settings=settings
        or AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
            max_fix_attempts=1,
        ),
    )
    completed: list = []
    failed: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed, runner_class.calls


def _empty_handed_run(tmp_path: Path, monkeypatch) -> tuple[dict, list]:
    """一輪什麼都沒做出來的執行：diff 是空的，git 也數不出任何改動。"""
    project = _project(tmp_path)
    SessionRegistry(project).save_lineup(Lineup.of(WHOLE))
    produced(monkeypatch, diff="", changed=[])

    completed, failed, calls = _run(project, monkeypatch)
    assert failed == [], failed
    assert completed, "整輪執行連一份回報都沒有交出來"
    return completed[0], calls


# --- 這一輪什麼都沒做出來 ---------------------------------------------------


def test_a_round_that_changed_no_files_is_not_called_completed(
    tmp_path: Path, monkeypatch
) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是它原本的樣子：測試過了、複查同意了、拍板那一格對著一段
    空白說「可以」，於是 `workflow_status` 是 `COMPLETED`、首頁是薄荷色的。
    """
    result, _calls = _empty_handed_run(tmp_path, monkeypatch)

    assert result["workflow_status"] != "COMPLETED", (
        "這一輪一個檔案都沒改到，畫面照樣說「工作流程已完成」"
    )
    assert result["workflow_status"] == "NOTHING_TO_APPROVE"


def test_the_approval_seat_is_never_dispatched(
    tmp_path: Path, monkeypatch
) -> None:
    """沒有產出就不派它——**一次呼叫都不多**。

    跟隔壁「有紅章就不派它」同一個理由：它的工作是拿計畫書比對這一輪做出來
    的東西，而被比對的那一樣不存在。派它去換到的是一個對著空白發表的意見，
    代價是多等一輪、多吃一次額度。

    這一條也是那一道**真的排在派工之前**的證據：只斷言結局不是 `COMPLETED`
    的話，一個「先跑完再改結局」的寫法會一樣綠。
    """
    _result, calls = _empty_handed_run(tmp_path, monkeypatch)

    dispatched = [
        call
        for call in calls
        if APPROVAL_OPENING in (call.get("input_text") or "")
    ]
    assert not dispatched, (
        "沒有東西可以拍板，卻還是派了核准那一格去看一段空白"
    )


def test_no_approval_stamp_lands_on_the_empty_version(
    tmp_path: Path, monkeypatch
) -> None:
    """鏈子上不准出現一個蓋在 `sha256("")` 上的同意章。

    這一條跟上面那條不一樣：上面問「有沒有多花一次呼叫」，這一條問**紀錄
    裡有沒有多出一個假的憑據**。同意章一旦落地，事後回頭看那一輪，它讀起來
    跟一個真的看過產出的同意章一模一樣——而它看的是一段空白。
    """
    result, _calls = _empty_handed_run(tmp_path, monkeypatch)

    chain = StampLog(Path(result["project_path"])).read(result["run_id"])
    approver = "實作"  # 沒有排核准，所以是最後一格（`chain_checker` 的規定）
    approvals = [
        stamp
        for stamp in chain.stamps
        if stamp.kind is StampKind.REVIEW
        and stamp.subject == SUBJECT_WORK
        and stamp.role == approver
    ]
    assert not approvals, (
        "拍板那一格的同意章還是蓋上去了："
        + "、".join(s.reasoning[:40] for s in approvals)
    )


def test_it_is_not_painted_as_finished(tmp_path: Path, monkeypatch) -> None:
    """畫面上不准是「跑完了」那一組。

    那一組在首頁上是薄荷色、算進「今日完成 N 件」，而使用者不會再去看它。
    """
    result, _calls = _empty_handed_run(tmp_path, monkeypatch)
    outcome = outcomes.describe(result)

    assert outcome.style != "done"
    assert outcome.group != outcomes.GROUP_DONE
    assert "完成" not in outcome.stage


def test_it_is_not_painted_as_a_failure_either(
    tmp_path: Path, monkeypatch
) -> None:
    """也不准畫成紅的。

    測試真的過了、複查真的同意了。講成失敗，使用者會去修一個沒壞的東西，
    而且它會被算進首頁那句「今日錯誤 N 件」（`ERROR_STATUSES` 數的是
    `style == "error"`）。
    """
    result, _calls = _empty_handed_run(tmp_path, monkeypatch)
    outcome = outcomes.describe(result)

    assert outcome.style != "error"
    assert result["workflow_status"] not in {"CLAUDE_ERROR", "CODEX_ERROR"}


def test_the_screen_says_no_file_was_changed(
    tmp_path: Path, monkeypatch
) -> None:
    """畫面上那一句要講「一個檔案都沒改到」，不是留白也不是講「還缺一個章」。

    留白的話他看到的是一列沒有說明的「要你處理」；講成缺章的話他會去補一個
    章，而缺的是零個——真正缺的是產出。
    """
    result, _calls = _empty_handed_run(tmp_path, monkeypatch)
    outcome = outcomes.describe(result)

    assert "一個檔案都沒有改到" in outcome.stage, (
        f"畫面那一句沒講清楚為什麼停下來：{outcome.stage!r}"
    )
    assert "章" not in outcome.stage, (
        f"把它講成缺章，使用者會去補一個沒有人量過的章：{outcome.stage!r}"
    )


def test_the_ending_has_a_row_in_the_outcome_table(
    tmp_path: Path, monkeypatch
) -> None:
    """這種收場要查得到「畫面該說什麼」。

    `app/core/outcomes.py` 沒有預設列——查不到就丟 `UnknownOutcome`，而落地
    的那份紀錄裡 `outcome_group` 會是空字串。**空的分組在首頁上是一格沒有
    顏色、沒有標籤的東西**，比講錯還難查。
    """
    result, _calls = _empty_handed_run(tmp_path, monkeypatch)

    assert result.get("outcome_group"), (
        f"{result['workflow_status']!r} 這種收場在 outcomes.py 裡查不到"
    )
    assert result["workflow_status"] in outcomes.known_statuses()


def test_it_tells_him_the_one_thing_he_can_actually_do(
    tmp_path: Path, monkeypatch
) -> None:
    """要指路，而且指的不能是「再跑一次」。

    同一份交辦內容再跑一次，最可能的結果是再一次什麼都沒做出來——把它歸到
    「再跑一次就好」那一組，是叫人去按一顆會給出同樣答案的按鈕。
    """
    result, _calls = _empty_handed_run(tmp_path, monkeypatch)
    outcome = outcomes.describe(result)

    assert outcome.group == outcomes.GROUP_DECIDE, (
        f"被分到「{outcome.group_label}」那一組"
    )
    # 比的是 `outcome.stage`，**不是 `result["outcome_detail"]`**：後者是引擎
    # 寫進字典的原料，而畫面上那一格印的是前者。只斷言原料的話，那張表少了
    # `detail_key`（畫面改印罐頭句）這條測試還是綠的——引擎講了，沒有人讀。
    assert "交辦" in outcome.stage, (
        f"停下來了，但畫面上沒有告訴他該去看什麼：{outcome.stage!r}"
    )


def test_a_diff_that_is_only_whitespace_counts_as_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """全是空白的 diff 也是「沒有產出」。

    今天 `get_git_diff()` 自己會 `.strip()`，所以這一種進不來——**這一條守的
    是明天**：那一步哪天不見了（它正在別人手上改），一段只有換行的 diff 會被
    讀成「有產出」，而使用者看到的是薄荷色的「工作流程已完成」。
    """
    project = _project(tmp_path)
    SessionRegistry(project).save_lineup(Lineup.of(WHOLE))
    produced(monkeypatch, diff="   \n\n  ", changed=[])

    completed, failed, _calls = _run(project, monkeypatch)

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "NOTHING_TO_APPROVE"


# --- 不准擋過頭 -------------------------------------------------------------


def test_a_round_that_only_created_new_files_still_completes(
    tmp_path: Path, monkeypatch
) -> None:
    """**這一條比上面那幾條重要。**

    `git diff` 端不出來、而 `git status --short` 看得到的那一輪，**不准被
    這一道擋掉**——它真的做出了東西，而他打開資料夾看得到那個檔案。
    改壞的樣子是只照 diff 擋：那一輪會被告知「一個檔案都沒有改到」。
    一條會誤報的檢查比沒有更貴：它會訓練人忽略紅燈。

    **這個案例原本的名字說的是「新增檔案」，而那個理由 2026-08-25 當天就過期
    了**，兩次：同一天 `get_git_diff()` 先補上新檔案那一段報告（untracked 看
    得到了），再補上 `_diff_base()`（`git add` 過的也看得到了）。所以真 git
    今天很難再長出「diff 空、status 有東西」這個形狀——**這一條從「擋不擋得
    住現在的產品」變成「兩支問法哪天再分岔，往放行倒」**，而那個約束照樣要有
    人釘著：擋錯一次的代價是他去改一份沒有問題的交辦內容。

    **它同時釘住一件不要被讀成安全的事：**這一條放行之後，核准那一格拿到的
    `deliverable` 還是空字串，四個章還是蓋在 `sha256("")` 上，整輪還是
    COMPLETED。這一道守的是「沒有產出不准收成完成」，守不到「放行的那一輪，
    核准那一格看得到東西」——那只能在 `get_git_diff()` 那一側補。
    """
    project = _project(tmp_path)
    SessionRegistry(project).save_lineup(Lineup.of(WHOLE))
    produced(monkeypatch, diff="", changed=["docs/錯題整理說明.md"])

    completed, failed, _calls = _run(project, monkeypatch)

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED", (
        "只新增了檔案的那一輪被擋掉了——而它真的做出了東西："
        f"{completed[0].get('outcome_detail')!r}"
    )


def test_an_ordinary_round_with_a_diff_is_untouched(
    tmp_path: Path, monkeypatch
) -> None:
    """有產出的那一輪，這一道一個字都不說。

    一道新的關卡如果會擋掉本來就該過的執行，它造成的損害比它防的多。
    """
    project = _project(tmp_path)
    SessionRegistry(project).save_lineup(Lineup.of(WHOLE))
    produced(
        monkeypatch,
        diff="--- a/x.py\n+++ b/x.py\n+改了一行\n",
        changed=["x.py"],
    )

    completed, failed, _calls = _run(project, monkeypatch)

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"


def test_the_planning_sequence_is_not_blocked(
    tmp_path: Path, monkeypatch
) -> None:
    """計畫序列的產出是一份計畫書，不是檔案改動——不准被這一道擋掉。

    這條路（`workflow_worker._run_planning`）從頭到尾不經過拍板那一扇門，
    所以今天它安全。**寫下來是因為那件事看不出來**：哪天有人把計畫序列也接
    上驗簽章，這一條會當場紅，而不是讓每一次擬計畫都收成「沒有東西可以拍板」。
    """
    project = _project(tmp_path)
    SessionRegistry(project).save_lineup(Lineup.of(WHOLE))
    produced(monkeypatch, diff="", changed=[])

    completed, failed, _calls = _run(
        project,
        monkeypatch,
        script=[cli_result("最終計畫內容")],
        settings=AIExecutionSettings(
            workflow_mode="planning",
            planning_rounds=0,
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
        ),
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "PLANNING_COMPLETED"
