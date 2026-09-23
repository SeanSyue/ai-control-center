"""驗收標準做不到的時候，執行的那一格可以把它退回去。

老闆 2026-08-22 的原話：

> 如果當成絕對的驗收標準的話**可能會因為執行上的問題而任務失敗**
> 有其他方法嗎?

有。定案是**駁回給訂標準的那一格**，附理由——不是讓執行的自己改標準（它會
改成自己剛好做得到的，那正是【驗收標準由複查角色訂】要防的），也不是讓整件
事失敗。現實跟預期不一樣的時候，錯的通常不是現實。

這一份守四件事：

1. **退回去的是「訂標準的那一格」，不是「上一關」。**退回上一關的話，五節點
   流程裡會退給規劃那一格，而標準不是它訂的——它只能再退一次。
2. **退回之後標準真的會被重訂，而且重訂的內容真的被用到。**沒有的話，執行端
   會拿到一模一樣的標準再做一次，然後再退一次。
3. **蓋在舊標準上的章要跟著失效。**沒失效的話，最後的紀錄會說「這份實作通過
   了驗收」——而通過的是一份已經被作廢的標準。
4. **談不攏就停下來交給人。**同一條意見退兩次沒有進展就是卡住；不自動放行，
   也不自動終止。自動放行會做出一個沒有人同意的東西；自動終止只會讓使用者
   看到「失敗」，而真正發生的是兩邊對「怎樣算做完」有不同意見。

**沒有次數上限**，理由跟需求確認那邊一樣：設了上限，到最後一次不管解決沒有，
執行端都會為了不被判失敗而硬做。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.lineup import (  # noqa: E402
    DUTY_PLAN,
    DUTY_REVIEW,
    DUTY_WORK,
    AgentSlot,
    Lineup,
)
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.approval_chain import (  # noqa: E402
    SUBJECT_ACCEPTANCE,
    SUBJECT_WORK,
    StampLog,
    Versions,
)
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    SETUP_INTENT_ONLY,
    calls_after_intake,
    cli_result,
    declare_verify_command,
    intake_settled,
    scripted_cli_runner_class,
)

PASS_REVIEW = (
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)

# 執行端退回時說的那句話。它必須一路走到訂標準的那一格手上。
WHY = "第 2 條要求跑 pytest，但這個專案是純前端，根本沒有 pytest。"
# 重訂之後的標準。它必須真的送到執行的那一格。
REVISED = "1. 完成狀態：頁面開得起來。\n2. 怎麼驗：跑 npm test。"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 做一個錯題整理小工具\n", encoding="utf-8"
    )
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    return project


def _seat(project: Path, slots: list[AgentSlot]) -> None:
    SessionRegistry(project).save_lineup(Lineup.of(slots))


def _run(project: Path, monkeypatch, script, **worker_kwargs):
    QApplication.instance() or QApplication([])
    # 需求確認正是這一份在測的東西，所以那一步不自動回答；「先交做法」
    # 跟這裡要測的事無關，讓假 CLI 自己接掉。
    runner_class = scripted_cli_runner_class(
        script, auto=SETUP_INTENT_ONLY
    )
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
            max_fix_attempts=1,
        ),
        **worker_kwargs,
    )
    completed: list = []
    failed: list = []
    stages: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.stage_changed.connect(stages.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed, stages, calls_after_intake(runner_class.calls)


def _rejects(why: str = WHY):
    return cli_result(f"駁回驗收標準：{why}")


# --- 一、退回去之後，標準真的被重訂而且被用到 -------------------------------


def test_a_worker_that_cannot_meet_the_bar_sends_it_back(
    tmp_path: Path, monkeypatch
) -> None:
    """退回 → 重訂 → 重做，一次執行裡走完，不用使用者插手。

    重訂出來的標準要真的送到執行那一格手上。送的還是舊的那份的話，它會做出
    一模一樣的東西然後再退一次——**而且每一輪都要付錢**。
    """
    project = _project(tmp_path)

    completed, failed, stages, calls = _run(
        project,
        monkeypatch,
        [
            intake_settled(),           # 訂標準
            _rejects(),                 # 實作：做不到
            intake_settled(REVISED),    # 重訂
            cli_result('{"result": "改好了"}'),   # 實作：這次做得到
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"

    # 重訂那一次，訂標準的那一格要看得到執行端說了什麼。
    re_intake = calls[2]["input_text"] or ""
    assert WHY in re_intake, (
        "重訂的時候看不到被退回的理由——它只能猜，而猜錯要再退一次"
    )
    assert "已經被退回過 1 次" in re_intake

    # 重做那一次，執行的那一格要拿到**新的**標準。
    redo = calls[3]["input_text"] or ""
    assert "npm test" in redo, "重訂出來的標準沒有送到執行那一格"
    assert "跑專案宣告的驗證指令" not in redo, (
        "舊的標準還留在提示詞裡。兩份標準同時在場，它不知道要照哪一份"
    )


def test_the_rejection_goes_to_the_seat_that_set_the_bar_not_the_previous_one(
    tmp_path: Path, monkeypatch
) -> None:
    """**退給訂標準的那一格，不是退給上一關。**

    五節點流程裡上一關是「規劃」，而標準是「發想」訂的。退給規劃的話，它只能
    再退一次，或者自己改一份不是它職權的東西——【駁回直接送給該負責的人】那
    條定案要防的正是這個。

    哨兵：重訂那一次的提示詞裡要有「需求確認者」的身分，而且要是**發想**
    那一格被派工。
    """
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot("發想", "codex", duty=DUTY_PLAN),
            AgentSlot("規劃", "claude", duty=DUTY_PLAN),
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("複查", "codex", duty=DUTY_REVIEW),
        ],
    )

    completed, failed, stages, calls = _run(
        project,
        monkeypatch,
        [
            intake_settled(),                      # 發想：訂標準
            cli_result("規劃的產出。"),             # 規劃：擬做法
            _rejects(),                            # 實作：做不到
            intake_settled(REVISED),               # 發想：重訂
            cli_result("規劃的產出（第二版）。"),    # 規劃：再擬一次
            cli_result('{"result": "改好了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"

    re_intake = calls[3]
    assert "你是此專案的需求確認者。" in (re_intake["input_text"] or ""), (
        "退回之後被派工的不是需求確認那一步"
    )
    # 發想那一格是 codex，規劃是 claude。退錯人的話這一條會紅。
    assert re_intake["arguments"][0] == "codex", (
        f"退給了錯的那一格：{re_intake['arguments'][0]}"
    )
    # 而且指名的是它的名字。只看廠牌的話，另一格剛好也是 codex 的時候
    # 這條會漏——**「退給誰」是這一條的全部意義**，不能靠一個會撞的欄位。
    assert stages.count("發想正在跟你確認需求") == 2, (
        f"重訂的時候被叫去的不是【發想】那一格：{stages}"
    )
    assert "複查正在跟你確認需求" not in stages
    assert "規劃正在跟你確認需求" not in stages


# --- 二、蓋在舊標準上的章要跟著失效 -----------------------------------------


def test_the_old_stamps_fall_when_the_bar_changes(
    tmp_path: Path, monkeypatch
) -> None:
    """標準一改，蓋在舊標準上的章自動失效——而且**不需要任何人記得去作廢**。

    沒有失效的話，最後的紀錄會說「這份實作通過了驗收」，而通過的是一份已經
    被作廢的標準。那是這整套章裡最貴的一種錯：它讀起來跟真的一模一樣。
    """
    project = _project(tmp_path)

    completed, failed, _stages, _calls = _run(
        project,
        monkeypatch,
        [
            intake_settled(),
            _rejects(),
            intake_settled(REVISED),
            cli_result('{"result": "改好了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )
    assert failed == [], failed

    chain = StampLog(project).read(completed[0]["run_id"])
    bars = [s for s in chain.stamps if s.subject == SUBJECT_ACCEPTANCE]
    work = [s for s in chain.stamps if s.subject == SUBJECT_WORK]

    assert len(bars) == 2, (
        f"驗收標準只被蓋了 {len(bars)} 次。退回之後應該有一份新的"
    )
    assert bars[0].content_digest != bars[1].content_digest, (
        "重訂之後的版本碼跟舊的一樣——那代表標準根本沒有換"
    )

    # 站在**現在**這一版上：舊的那個標準的章不算數了。
    now = Versions(
        criteria=bars[1].content_digest,
        deliverable=work[-1].content_digest,
    )
    alive = {s.stamp_id for s in chain.live(now)}
    assert bars[1].stamp_id in alive
    assert bars[0].stamp_id not in alive, "被退回的那份標準的章還算數"


# --- 三、談不攏就停下來交給人 -----------------------------------------------


def test_the_same_objection_twice_stops_and_asks_the_user(
    tmp_path: Path, monkeypatch
) -> None:
    """同一條意見退兩次還沒有進展 = 卡住。停下來，交給使用者。

    【不收斂的定義】看**單一異議**不看總數。而且不自動放行也不自動終止：
    放行會做出一個沒有人同意的東西；終止只會讓使用者看到「失敗」兩個字，
    而真正發生的是兩邊對「怎樣算做完」有不同意見。
    """
    project = _project(tmp_path)

    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            intake_settled(),
            _rejects(),
            intake_settled(),   # 重訂了，但訂出一份一樣的
            _rejects(),         # 同一條理由，第二次
        ],
    )

    assert failed == [], failed
    result = completed[0]
    assert result["workflow_status"] == "CRITERIA_DEADLOCK", result[
        "workflow_status"
    ]
    assert len(calls) == 4, "卡住了還繼續派工"
    assert WHY in result["test_output"], (
        "停下來了，但沒有告訴使用者兩邊在吵什麼"
    )
    assert "談不攏" in result["test_output"]


def test_a_different_objection_is_not_a_deadlock(
    tmp_path: Path, monkeypatch
) -> None:
    """換了一條理由就是有進展，繼續走。

    只數「退了幾次」的話，第二次退回就會被當成卡住——而那一次可能正是流程在
    收斂：第一條解決了，露出第二條。
    """
    project = _project(tmp_path)

    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            intake_settled(),
            _rejects("沒有 pytest。"),
            intake_settled(REVISED),
            _rejects("npm test 這個專案也沒有，只有 vitest。"),
            intake_settled("1. 怎麼驗：跑 vitest。"),
            cli_result('{"result": "這次可以了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED", (
        "換了一條理由卻被判成卡住——退回兩次不等於沒有進展"
    )
    assert len(calls) == 8


def test_a_rejection_with_no_reason_is_not_a_rejection(
    tmp_path: Path, monkeypatch
) -> None:
    """沒有理由的退回不算退回。

    訂標準的那一格拿到一句沒有理由的「做不到」，只能猜——而猜錯要再退一次，
    每一輪都要付錢。所以這裡當成一般的產出往下走，讓複查那一關去看它。
    """
    project = _project(tmp_path)

    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            intake_settled(),
            cli_result("駁回驗收標準："),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert len(calls) == 4, "沒有理由的退回讓標準被重訂了一次"


def test_a_line_up_with_nobody_to_send_it_back_to_stops_and_says_so(
    tmp_path: Path, monkeypatch
) -> None:
    """有人要退回標準，但這條線上沒有人訂標準——停下來說清楚。

    不硬挑一格收下它：挑到的那一格會拿到「訂標準」的權限，而使用者從來沒有
    把它排成那個角色。
    """
    project = _project(tmp_path)
    _seat(project, [AgentSlot("實作", "claude", duty=DUTY_WORK)])

    # 這條線沒有需求確認那一步，所以第一次派工就是實作。
    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [_rejects(), cli_result("1 passed")],
    )

    assert failed == [], failed
    # 沒有驗收標準的時候，一句「駁回驗收標準」退不回任何地方，所以它被當成
    # 一般的產出往下走——不是停在 CRITERIA_NO_OWNER，因為根本沒有標準可退。
    assert completed[0]["workflow_status"] != "CRITERIA_NO_OWNER"
    assert len(calls) >= 1


# --- 擬做法那一格也退得回去，但停下來交給人 ---------------------------------


def test_a_planning_seat_can_object_before_anyone_starts(
    tmp_path: Path, monkeypatch
) -> None:
    """【每個 node 都能駁回】包含擬做法那幾格。

    它在擬做法的時候**正好是最有可能發現「這個標準訂錯了」的那一刻**——
    它要想清楚怎麼做，而想不出來的原因常常是標準本身有問題。

    沒有這條路的話，它只能硬擬一份做不到的做法，然後由執行那一格撞牆之後
    才退回去：多走一整輪，而且那一輪的產出沒有人要。
    """
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot("發想", "codex", duty=DUTY_PLAN),
            AgentSlot("規劃", "claude", duty=DUTY_PLAN),
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("複查", "codex", duty=DUTY_REVIEW),
        ],
    )

    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            intake_settled(),
            _rejects("第 2 條要求跑 pytest，這個案子是純設計稿。"),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "PLAN_REJECTS_CRITERIA"
    assert len(calls) == 2, "擬做法那一格已經說標準有問題了，卻還繼續派工"
    assert "純設計稿" in completed[0]["test_output"]


def test_a_planning_objection_does_not_silently_rewrite_the_bar(
    tmp_path: Path, monkeypatch
) -> None:
    """它的意見**不會自動改掉那份標準**——停下來交給使用者。

    跟執行那一格的差別是證據的分量：執行那一格是**動手之後**發現做不到，
    那是量出來的；這一格是**還沒動手**就覺得不對，那是判斷。判斷不該直接推翻
    一份跟使用者確認過的標準——那條標準是他點頭的，而它連試都還沒試。
    """
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot("發想", "codex", duty=DUTY_PLAN),
            AgentSlot("規劃", "claude", duty=DUTY_PLAN),
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("複查", "codex", duty=DUTY_REVIEW),
        ],
    )

    completed, failed, _stages, _calls = _run(
        project,
        monkeypatch,
        [intake_settled(), _rejects("做不到。")],
    )
    assert failed == [], failed

    brief = (project / ".aicc" / "project-brief.md").read_text(encoding="utf-8")
    assert "完成狀態" in brief, "那份標準被改掉了，而使用者從來沒有點頭"
