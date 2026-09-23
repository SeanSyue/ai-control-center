"""Verbatim snapshot of every prompt the Orchestrator sends.

This exists to make ROADMAP P10 Stage A (moving the hardcoded
`prompt = f"…"` blocks out of `orchestrator.py` and into
`prompt_builder.py`) a refactor that can be *proved* to change nothing.
The refactor's whole claim is "the text is identical"; without a
byte-for-byte record of the text before it, that claim is unfalsifiable.

What the snapshot covers, and why each part is in it:

* **The prompt text**, verbatim including whitespace. A prompt differing
  by one line is a different instruction to the model, and nothing else
  in the suite would notice.
* **The dispatch parameters** (`role`, `event_type`, `read_only`,
  `title_prefix`). These decide *who* runs the step and *under what
  permission*. Moving the text out of the method is exactly the kind of
  edit where a `read_only=True` gets dropped, and a read-only step
  silently becoming writable is worse than a wrong prompt.

What it deliberately does **not** cover: the session-bootstrap wrapper
(`auto_session_bootstrap=False` below). That wrapper's content comes
from `ProjectKnowledgeStore.write_session_bootstrap`, which is not part
of Stage A, and pinning it here would make this file fail for reasons
that have nothing to do with the prompts.

Regenerating: set `AICC_UPDATE_GOLDEN=1`. Deliberately an explicit opt-in
- a snapshot test that rewrites itself when it fails is not a test.
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path

import pytest

from app.adapters.cli_runner import CliResult
from app.core.lineup import Lineup
from app.core.orchestrator import Orchestrator
from app.services.ai_settings import AIExecutionSettings

GOLDEN = Path(__file__).parent / "golden" / "orchestrator_prompts.txt"
SEPARATOR = "=" * 78


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 任務\n把設定頁的儲存列修好。\n",
        encoding="utf-8",
    )
    return project


def make_orchestrator(
    tmp_path: Path, lineup=None
) -> tuple[Orchestrator, list[dict]]:
    """An orchestrator whose every step records its prompt instead of running.

    `run_id` is fixed so nothing in the captured text carries a timestamp.
    """
    settings = AIExecutionSettings(
        # Off so the bootstrap wrapper does not enter the snapshot - see the
        # module docstring.
        auto_session_bootstrap=False,
        claude_verbosity="concise",
        codex_verbosity="low",
        context_profile="minimal",
    )
    orchestrator = Orchestrator(
        make_project(tmp_path),
        run_id="golden-run",
        settings=settings,
        lineup=lineup,
    )
    captured: list[dict] = []

    def fake_dispatch(
        role: str,
        prompt: str,
        event_type: str,
        metadata: dict | None = None,
        *,
        read_only: bool = False,
        title_prefix: str = "Codex",
    ) -> CliResult:
        captured.append(
            {
                "role": role,
                "event_type": event_type,
                "read_only": read_only,
                "title_prefix": title_prefix,
                "prompt": prompt,
            }
        )
        return CliResult(
            command="fake",
            success=True,
            return_code=0,
            stdout="",
            stderr="",
        )

    orchestrator._run_role_prompt = fake_dispatch  # type: ignore[method-assign]
    return orchestrator, captured


def exercise_every_step(orchestrator: Orchestrator) -> None:
    """Call every method that builds a prompt.

    Branch variants are included where the template has conditional text:
    a planning round 0 with nothing to carry forward renders the
    「（無，請先建立初稿）」 fallbacks, a later final round renders the real
    values and the 「最終統整」 phase word. A snapshot that only ever saw one
    side of those branches would let the other side be rewritten freely.
    """
    ok = CliResult(
        command="claude",
        success=True,
        return_code=0,
        stdout="已完成修改。",
        stderr="",
    )
    tests_ok = CliResult(
        command="pytest",
        success=True,
        return_code=0,
        stdout="12 passed",
        stderr="",
    )
    review = CliResult(
        command="codex",
        success=True,
        return_code=0,
        stdout='{"status": "PASS"}',
        stderr="",
    )

    # 需求確認：兩種分支都要進快照——第一輪（還沒問過任何東西）與後續輪
    # （帶著問答歷史）渲染出來的文字不一樣，只看到一邊的話，另一邊可以被
    # 自由改寫而沒有人發現。
    orchestrator.run_intake("reviewer")
    orchestrator.run_intake(
        "reviewer",
        rounds=(
            ("資料要存在哪裡？建議答案：本機 JSON，不進雲端。", "就本機。"),
        ),
    )
    # 第三種分支：標準被執行端退回過。那一段的文字是執行端唯一看得到的
    # 「退回不是失敗」的說明，沒有進快照的話它可以被改掉而沒有東西變紅。
    orchestrator.record_criteria_objection(
        "author", "第 2 條要求跑 pytest，這個專案沒有 pytest。"
    )
    orchestrator.run_intake("reviewer")
    # 做事的那一格自己交的那一份做法。跟 `work_plan` 用同一份本體、不同的
    # 開場白——沒有進快照的話，那句「動手之前先說清楚」可以被改掉而沒有東西
    # 變紅，而它是那一格唯一知道「這一步還不動手」的地方。
    orchestrator.run_work_intent("author")
    orchestrator.run_claude(own_intent="我打算先改 CSS。")
    orchestrator.run_claude()
    orchestrator.run_claude_with_user_answer(
        question="要不要一併改資料庫結構？",
        answer="不要，這次只改畫面。",
        answered_by="user",
    )
    orchestrator.run_codex_decision_gate(
        question="要不要刪掉舊的匯出檔？",
        source="claude",
    )
    orchestrator.run_claude_fix(
        {
            "summary": "測試未通過。",
            "issues": ["儲存列會蓋住最後一行"],
            "instructions": ["把負邊距拿掉"],
        },
        2,
    )
    orchestrator.run_codex_review(
        claude_result=ok,
        test_result=tests_ok,
        git_diff="--- a/x\n+++ b/x\n",
        changed_files=["app/ui/web/settings_v1.html"],
    )
    # 討論與核准 2026-08-24 之前**都不在快照裡**。
    #
    # 核准那一格是老闆五節點流程的最後一句（NODE5 審閱所有簽章），而它的
    # 提示詞改了不會有任何東西叫——一份沒有快照的提示詞，跟一份沒有人在看
    # 的提示詞是同一件事。
    orchestrator.run_consult(
        role="reviewer",
        asked_by="author",
        question="計畫書說要支援匯出，那要不要一起做匯入？",
        context="我看到 export() 旁邊有一個 TODO。",
    )
    # 先存一份計畫書再派核准：**快照只看過 fallback 的話，真的那一支
    # 可以被自由改寫而沒有人發現**（同一份檔案上面那段對分支的說明講的
    # 就是這件事）。而「產出與計畫書比對」正是老闆給 NODE5 的定義。
    orchestrator.save_project_brief("目標：三分鐘內把錯題整理成一份可以複習的清單。")
    orchestrator.run_approval(
        # 出廠的核准位子叫 finisher。派給 reviewer 也跑得動（角色名是使用者
        # 的），但那樣快照講的就不是出廠那一張表的樣子。
        role="finisher",
        chain="【實作】的執行章（機器）：claude 離開碼 0",
        readiness="缺 1 個章\n還缺：【複查】的成品章",
        deliverable="--- a/x\n+++ b/x\n",
    )
    orchestrator.run_codex_memory_update(
        claude_result=ok,
        codex_review=review,
        git_diff="--- a/x\n+++ b/x\n",
        changed_files=["app/ui/web/settings_v1.html"],
    )
    orchestrator.run_claude_memory_review("## PROJECT_BRIEF.md\n草案內容\n")
    orchestrator.run_codex_plan(
        task="把設定頁的儲存列修好。",
        round_number=0,
    )
    orchestrator.run_codex_plan(
        task="把設定頁的儲存列修好。",
        previous_plan="第一版計畫內容",
        claude_feedback="可行，但要注意 sticky。",
        round_number=2,
        final_round=True,
    )
    orchestrator.run_claude_plan_review(
        task="把設定頁的儲存列修好。",
        codex_plan="第一版計畫內容",
        round_number=1,
    )


def render(captured: list[dict]) -> str:
    blocks = []
    for index, step in enumerate(captured):
        blocks.append(
            f"{SEPARATOR}\n"
            f"# [{index}] role={step['role']} "
            f"event_type={step['event_type']} "
            f"read_only={step['read_only']} "
            f"title_prefix={step['title_prefix']}\n"
            f"{SEPARATOR}\n"
            f"{step['prompt']}"
        )
    return "\n".join(blocks)


def exercise_the_planner_seat(orchestrator: Orchestrator) -> None:
    """The two prompts that only a three-seat line-up produces.

    Appended after the two-seat pass rather than replacing it, so the first ten
    blocks of the snapshot stay byte-identical. That is the evidence that
    adding an optional seat changed nothing for the projects that do not use
    one - a claim the diff can be read for, instead of taken on trust.
    """
    orchestrator.run_work_plan()
    orchestrator.run_claude(work_plan="先改 CSS，不要動 Python。")
    # 驗收標準訂下來之後，那一段會出現在擬做法與派工兩份提示詞裡——包含
    # 「做不到就退回去」那條路的開頭。沒有進快照的話，那段文字可以被改掉
    # 而沒有任何東西變紅，而它是執行端唯一知道怎麼駁回的地方。
    orchestrator.save_acceptance_criteria(
        "1. 完成狀態：設定頁存得起來。\n2. 怎麼驗：跑宣告的驗證指令。"
    )
    orchestrator.run_work_plan()
    orchestrator.run_claude(work_plan="先改 CSS，不要動 Python。")


def capture(tmp_path: Path) -> str:
    orchestrator, captured = make_orchestrator(tmp_path)
    exercise_every_step(orchestrator)

    planned, planned_captured = make_orchestrator(
        tmp_path / "planned", Lineup.classic().with_planner()
    )
    exercise_the_planner_seat(planned)

    return render(captured + planned_captured)


def test_every_prompt_matches_the_golden_snapshot(tmp_path: Path) -> None:
    actual = capture(tmp_path)

    if os.environ.get("AICC_UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(actual, encoding="utf-8")
        pytest.skip("AICC_UPDATE_GOLDEN=1：已重寫快照，這一輪不算通過")

    assert GOLDEN.is_file(), (
        f"找不到金標快照 {GOLDEN}。"
        "第一次建立請跑 AICC_UPDATE_GOLDEN=1 pytest tests/test_prompt_golden.py"
    )
    expected = GOLDEN.read_text(encoding="utf-8")
    if actual != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile="金標",
                tofile="現在",
                lineterm="",
            )
        )
        pytest.fail(
            "送給 AI 的 prompt 變了。如果這是刻意的，"
            "請用 AICC_UPDATE_GOLDEN=1 重建快照並在 commit 說明改了什麼：\n"
            + diff
        )


def test_the_snapshot_covers_every_prompt_building_method(
    tmp_path: Path,
) -> None:
    """A step added later must be added to the snapshot too.

    Without this, the golden silently stops being complete: a ninth
    prompt-building method would go unsnapshotted and nobody would know,
    because the eight that are covered still pass.
    """
    orchestrator, captured = make_orchestrator(tmp_path)
    exercise_every_step(orchestrator)
    planned, planned_captured = make_orchestrator(
        tmp_path / "planned", Lineup.classic().with_planner()
    )
    exercise_the_planner_seat(planned)
    covered = {
        step["event_type"] for step in (captured + planned_captured)
    }

    # **這份清單自己漏過兩個。**2026-08-24 之前它沒有 `consult` 與 `approval`
    # ——而 `approval` 正是老闆五節點流程的最後一句（NODE5 審閱所有簽章）。
    #
    # 一份寫死的「應該有哪些」清單，只擋得住「刪掉一個」，擋不住「新增一個
    # 卻忘了列」——而後者正好是這兩個發生的事。所以底下多一條：
    # `prompt_builder` 裡每一個 `build_*_prompt` 都要有人跑到。
    expected_event_types = {
        "intake",
        "work_intent",
        "work_plan",
        "task",
        "user_answer",
        "decision_gate",
        "fix",
        "review",
        "consult",
        "approval",
        "memory_update",
        "memory_review",
        "planning",
        "planning_final",
        "planning_review",
    }
    assert covered == expected_event_types, (
        "有步驟沒有進金標快照，或者快照裡有已經不存在的步驟。"
        f"缺少：{expected_event_types - covered}；多出：{covered - expected_event_types}"
    )


def test_every_prompt_builder_has_a_snapshot(tmp_path: Path) -> None:
    """`prompt_builder` 裡每一個 builder 產出的東西都要在快照裡。

    上面那條比的是一份**寫死的**清單，所以它擋得住「刪掉一個步驟」，擋不住
    「新增一個步驟卻忘了列進去」——而 2026-08-24 查到的正是後者：`consult`
    與 `approval` 兩個都在跑、都沒有快照、**而且沒有任何東西在叫**。
    其中 `approval` 正是老闆五節點流程的最後一句（NODE5 審閱所有簽章）。

    這一條問的是**模組自己**：每個 builder 用 `resolve_persona("<步驟>")`
    宣告自己是哪一步，而那個步驟名必須出現在被捕捉到的事件裡。
    不是人維護的名單，是程式自己講的。
    """
    import ast
    import inspect

    from app.core import prompt_builder

    declared: dict[str, str] = {}
    tree = ast.parse(inspect.getsource(prompt_builder))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not (node.name.startswith("build_") and node.name.endswith("_prompt")):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "resolve_persona"
                and inner.args
                and isinstance(inner.args[0], ast.Constant)
            ):
                declared[node.name] = str(inner.args[0].value)
    assert len(declared) >= 10, f"抓不到足夠的 builder，這條會空轉：{declared}"

    orchestrator, captured = make_orchestrator(tmp_path)
    exercise_every_step(orchestrator)
    planned, planned_captured = make_orchestrator(
        tmp_path / "planned", Lineup.classic().with_planner()
    )
    exercise_the_planner_seat(planned)
    seen = {entry["event_type"] for entry in captured + planned_captured}

    missing = sorted(
        f"{name}（步驟 {step}）"
        for name, step in declared.items()
        if step not in seen
    )
    assert not missing, (
        "這幾個 builder 產出的東西不在快照裡——它們改了不會有任何東西叫："
        + "、".join(missing)
    )
