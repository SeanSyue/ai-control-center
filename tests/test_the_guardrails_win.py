"""附加指示牴觸安全護欄的時候，**護欄贏，而且畫面要說出被擋下來的是哪一句**。

2026-08-27 拍板（`OPEN_QUESTIONS.md` ❓15）：

> **護欄贏，而且畫面要明說哪一句被擋下來。**

情境：使用者在交辦欄多寫一句「請先 commit」，而護欄寫著「不建立 Git commit」。

## 這份檔案守的兩件事

**一、護欄贏，而且是用講的。**

在這之前「贏」只是**位置上的**：三份提示詞都把任務寫在前面、規則寫在後面，
而**沒有任何一句話說過後面那幾條贏**。位置在人讀起來像規則，在模型那裡只是
順序。今天它多半會照護欄做，但那是運氣，不是規則。

**二、被擋下來的那一句要看得到，而且是他寫的原話。**

一句「有指示被忽略」等於叫他回頭自己猜是哪一句——而那一句機器手上就有。

## 為什麼這不是「猜使用者的意圖」

被否決過的做法是寫關鍵字去猜（`if "commit" in task`）：沒有樣本、猜錯會很有
自信地說錯話，而「不要 commit」跟「請先 commit」在關鍵字眼裡一模一樣。

這裡反過來走——**AI 講，引擎驗**：提示詞規定一個固定格式，引擎照那個格式讀
（跟 `ASK_USER:` 同一種東西），然後兩件事都要逐字對得上，缺一整組丟掉：

1. 他寫的那一句要**真的出現在任務原文裡**。
2. 擋它的那一條要**真的是我們自己寫的護欄**。

編出來的過不了。**漏掉比說錯好**：沒對上就不顯示。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.core import orchestrator_parsing, prompt_builder, prompt_markers
from app.core.prompt_builder import GUARDRAILS, RULES_WIN


def _task_prompt(task: str) -> str:
    return prompt_builder.build_task_prompt(
        task=task,
        knowledge_context="（示意）",
        safety_instruction=prompt_builder.claude_safety_instruction(),
        output_instruction=prompt_builder.output_instruction("concise"),
    )


def _user_answer_prompt(task: str) -> str:
    return prompt_builder.build_user_answer_prompt(
        task=task,
        question="要不要 commit？",
        answer="要。",
        safety_instruction=prompt_builder.claude_safety_instruction(),
        output_instruction=prompt_builder.output_instruction("concise"),
    )


def _fix_prompt(task: str) -> str:
    return prompt_builder.build_fix_prompt(
        task=task,
        attempt_number=1,
        summary="有幾個問題。",
        issues=["一"],
        instructions=["二"],
        safety_instruction=prompt_builder.claude_safety_instruction(),
        output_instruction=prompt_builder.output_instruction("concise"),
    )


ALL_PROMPTS = (_task_prompt, _user_answer_prompt, _fix_prompt)


# ---------------------------------------------------------------------------
# 一、護欄贏
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("build", ALL_PROMPTS)
def test_every_prompt_says_out_loud_that_the_rules_win(build) -> None:
    """**這一條是這份檔案存在的理由。**

    三份都要說，而且要說的是同一句——寫三次的下場是改到兩份，
    剩下那一份靜靜地不一樣。
    """
    prompt = build("把設定頁的儲存列修好。請先 commit 再開始。")
    assert RULES_WIN in prompt, (
        "這一份提示詞沒有說「護欄贏」。位置在人讀起來像規則，"
        "在模型那裡只是順序。"
    )


@pytest.mark.parametrize("build", ALL_PROMPTS)
def test_the_rules_still_come_after_the_task(build) -> None:
    """位置那一半也要留著。**兩個一起才算數**——

    只有位置沒有話，是今天的樣子（靠運氣）；
    只有話沒有位置，是把一句「以上這幾條」放在它指的東西前面。
    """
    task = "把設定頁的儲存列修好。請先 commit 再開始。"
    prompt = build(task)
    assert prompt.index(task) < prompt.index(RULES_WIN)
    assert prompt.index("不建立 Git commit。") < prompt.index(RULES_WIN), (
        "「以上這幾條」出現在那幾條**前面**——它指不到任何東西。"
    )


# 提示詞規則列裡**不是護欄**的那幾行，一行一個理由。
#
# 這一份跟 `GUARDRAILS` 是互補的兩半：底下那條檢查會把三份提示詞的規則列
# 逐行拆出來，每一行不是在 `GUARDRAILS` 裡就是在這裡。**加一條規則而兩邊
# 都沒登記，測試就紅**——沒有這個，新加的護欄會靜靜地變成「AI 講了我們也
# 認不得」，而畫面上看起來只是那一列沒有出現。
NOT_A_GUARDRAIL: dict[str, str] = {
    "完成後簡短說明修改內容。": "這是**義務**，不是禁令——沒有「被它擋下來」這種事。",
}


def _rule_lines(prompt: str) -> list[str]:
    """規則列裡**一行講完**的那幾條。

    切在安全規則那一段之前：它是整段塞進去的（第 6 條），而它**自己也用
    1. 2. 3. 編號**——不切的話，會把它的子條當成護欄候選，而那幾條沒有一條
    是「一句話的禁令」。輸出規則同理，它排在安全規則後面，一起被切掉。
    """
    body = prompt.split(RULES_WIN)[0].split(
        prompt_builder.claude_safety_instruction()
    )[0]
    return [
        line.strip()
        for line in re.findall(r"^\d+\. (\S[^\r\n]*)$", body, re.MULTILINE)
    ]


@pytest.mark.parametrize("build", ALL_PROMPTS)
def test_every_rule_line_is_either_a_guardrail_or_written_down_as_not_one(
    build,
) -> None:
    """規則列裡的每一行都要有人替它分類過。

    **這一條是 `GUARDRAILS` 那份清單的看門人。**手寫的清單不會自己跟著程式
    走，所以要有人問它——沒有它，新加一條規則，`guardrail_for()` 認不得，
    而 AI 照格式報上來的那一組會被丟掉，畫面上那一列直接不見。
    """
    prompt = build("把設定頁的儲存列修好。")
    lines = _rule_lines(prompt)
    assert lines, "一行規則都沒抓到——這條檢查是恆真的"
    unclassified = [
        line
        for line in lines
        if prompt_builder.guardrail_for(line) is None
        and line not in NOT_A_GUARDRAIL
    ]
    assert not unclassified, (
        f"這幾行規則沒有人替它分類過：{unclassified}\n"
        "護欄請加進 `prompt_builder.GUARDRAILS`（**原文一個字都不能差**），"
        "不是護欄的加進這份檔案的 `NOT_A_GUARDRAIL` 並寫下理由。"
    )


def test_every_guardrail_text_really_appears_in_a_prompt() -> None:
    """反過來：清單上的每一句，提示詞裡真的要有。

    對不上的那一句是**死的**——AI 永遠不會抄到它，所以它永遠驗不出東西，
    而這件事從畫面上看起來只是「從來沒有被擋下來過」。
    """
    prompts = "\n".join(build("做一件事。") for build in ALL_PROMPTS)
    orphans = [
        text
        for rule in GUARDRAILS
        for text in rule.texts
        if text not in prompts
    ]
    assert not orphans, (
        f"這幾句護欄在三份提示詞裡一次都不出現：{orphans}\n"
        "它們是死的——改了提示詞的措辭就要跟著改這裡。"
    )


def test_the_guardrail_ids_are_unique() -> None:
    ids = [rule.rule_id for rule in GUARDRAILS]
    assert len(ids) == len(set(ids)), f"護欄代號撞名：{ids}"


# ---------------------------------------------------------------------------
# 二、被擋下來的那一句
# ---------------------------------------------------------------------------

TASK = "把設定頁的儲存列修好。請先 commit 再開始。順便幫我安裝 pytest-cov。"


def _report(quote: str, rule: str) -> str:
    return (
        f"做完了。\n\n{prompt_markers.BLOCKED_OPEN}\n"
        f"你寫的：{quote}\n"
        f"擋它的規則：{rule}\n"
        f"{prompt_markers.BLOCKED_CLOSE}\n"
    )


def test_the_users_own_sentence_comes_back() -> None:
    """**這一條是後半段存在的理由。**他要看到的是他自己寫的那一句。"""
    found = orchestrator_parsing.blocked_instructions(
        TASK, _report("請先 commit 再開始。", "不建立 Git commit。")
    )

    assert len(found) == 1
    assert found[0].quote == "請先 commit 再開始。"
    assert found[0].rule_id == "commit"
    assert found[0].rule_text == "不建立 Git commit。"


def test_a_sentence_the_user_never_wrote_is_thrown_away() -> None:
    """AI 編一句話出來的時候，**整組丟掉**。

    顯示一件沒發生的事，會讓他去改一個他根本沒寫錯的句子——
    然後他從此不信這一列。
    """
    found = orchestrator_parsing.blocked_instructions(
        TASK, _report("請把資料庫砍掉。", "不建立 Git commit。")
    )
    assert found == ()


def test_a_rule_we_never_wrote_is_thrown_away() -> None:
    """規則那一邊同理：**比對的是我們自己那份清單**，不是聽它說。"""
    found = orchestrator_parsing.blocked_instructions(
        TASK, _report("請先 commit 再開始。", "不准做任何危險的事。")
    )
    assert found == ()


def test_a_one_character_quote_does_not_pass_as_verbatim() -> None:
    """照抄一個標點就能過關的話，「逐字比對」等於沒有做。

    「。」是任何一份任務的子字串。
    """
    found = orchestrator_parsing.blocked_instructions(
        TASK, _report("。", "不建立 Git commit。")
    )
    assert found == ()


def test_nothing_is_reported_when_the_ai_said_nothing() -> None:
    """**沒說就是沒有。**這裡不准自己去任務裡找關鍵字補一個。"""
    assert (
        orchestrator_parsing.blocked_instructions(
            TASK, "做完了，儲存列修好了，沒有遇到問題。"
        )
        == ()
    )


def test_a_task_that_only_mentions_commit_reports_nothing() -> None:
    """**這一條擋的是「改回去用關鍵字猜」。**

    任務裡出現 commit 這個字，而 AI 沒有說它擋了什麼——那就是沒有擋。
    寫成 `if "commit" in task` 的版本會在這裡說錯話：這份任務要的正好是
    **不要** commit。
    """
    task = "把設定頁修好。這個 bug 是在最後一次 git commit 之後才出現的，不要 commit。"
    assert orchestrator_parsing.blocked_instructions(task, "做完了。") == ()


def test_the_same_pair_is_not_listed_twice() -> None:
    output = _report("請先 commit 再開始。", "不建立 Git commit。") * 2
    assert len(orchestrator_parsing.blocked_instructions(TASK, output)) == 1


def test_line_breaks_in_the_copy_do_not_break_the_match() -> None:
    """AI 抄的時候換行位置會不一樣。**只放寬到折疊空白為止。**"""
    task = "把設定頁的儲存列修好。\n請先  commit\n再開始。"
    found = orchestrator_parsing.blocked_instructions(
        task, _report("請先 commit 再開始。", "不建立 Git commit。")
    )
    assert len(found) == 1


# ---------------------------------------------------------------------------
# 三、它真的走到畫面上了嗎
# ---------------------------------------------------------------------------


def test_the_engine_records_it_at_the_time(tmp_path: Path) -> None:
    """一次真的派工之後，那一句要**留在那次執行的資料夾裡**。

    存下來而不是事後重算：重算的那一份只會跟當時算的一致到其中一邊改動為
    止，然後畫面會很有自信地講一件當時沒有發生的事。

    而且是**累積的**——`claude-output.txt` 是覆蓋的，後面的修正輪會蓋掉
    第一次動手那一輪的輸出，而被擋下來的那一句通常就在第一輪。
    """
    from app.adapters.cli_runner import CliResult
    from app.core.orchestrator import Orchestrator
    from app.services.ai_settings import AIExecutionSettings

    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(TASK + "\n", encoding="utf-8")
    orchestrator = Orchestrator(
        project,
        run_id="run-1",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
    )
    orchestrator.timeline.create_run(TASK, run_id="run-1")

    # 第二輪也擋下一句，而且是**不同的一句**。
    #
    # 第二輪什麼都沒擋的話，合併那幾行根本走不到——那條破壞會永遠是綠的
    #（**修好之後走不到的分支，它的破壞永遠是綠的**）。這裡要量的正好是
    # 「第二輪寫下自己那一句的時候，第一輪那一句還在不在」。
    replies = [
        _report("請先 commit 再開始。", "不建立 Git commit。"),
        _report("順便幫我安裝 pytest-cov。", "不安裝套件。"),
    ]
    orchestrator.runner.run = (  # type: ignore[method-assign]
        lambda *a, **k: CliResult(
            command="claude",
            success=True,
            return_code=0,
            stdout=json.dumps({"result": replies.pop(0)}),
            stderr="",
        )
    )

    orchestrator.run_claude()
    orchestrator.run_claude()

    stored = json.loads(
        (project / ".aicc" / "runs" / "run-1" / "blocked-instructions.json")
        .read_text(encoding="utf-8")
    )
    assert stored == [
        {
            "quote": "請先 commit 再開始。",
            "rule_id": "commit",
            "rule_text": "不建立 Git commit。",
        },
        {
            "quote": "順便幫我安裝 pytest-cov。",
            "rule_id": "install",
            "rule_text": "不安裝套件。",
        },
    ], (
        "第二輪蓋掉了第一輪，而被擋下來的那一句通常就在第一輪——"
        "只留最後一份的話，使用者永遠看不到它。"
    )


def test_the_engine_writes_it_down_where_the_screen_looks(tmp_path: Path) -> None:
    """引擎算出來的東西要**推到畫面拿得到的地方**。

    這個專案昨天才踩過：引擎做完了、規格沒送出去，畫面那邊還以為沒做。
    """
    from app.services.project_dashboard import ProjectDashboardService
    from app.services.project_registry import ProjectEntry, ProjectRegistry
    from app.services.timeline_store import TimelineStore

    project = tmp_path / "demo"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    run_id = TimelineStore(project).create_run(TASK)
    (project / ".aicc" / "runs" / run_id / "blocked-instructions.json").write_text(
        json.dumps(
            [
                {
                    "quote": "請先 commit 再開始。",
                    "rule_id": "commit",
                    "rule_text": "不建立 Git commit。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _summary, items = ProjectDashboardService().build(
        [ProjectEntry(name="demo", path=str(project))]
    )

    assert items[0].blocked_instructions == (
        ("請先 commit 再開始。", "不建立 Git commit。"),
    )


def test_the_card_prints_his_own_words(tmp_path: Path) -> None:
    """卡片上要有**他寫的原話**，不是一句「有指示被忽略」。"""
    from app.i18n import t

    line = t(
        "dash.blocked_pair",
        quote="請先 commit 再開始。",
        rule="不建立 Git commit。",
    )

    assert "請先 commit 再開始。" in line, (
        "那一列沒有他寫的原話——他要回頭自己猜是哪一句，"
        "而那一句機器手上就有。"
    )
    assert "不建立 Git commit。" in line, "沒說是哪一條擋的。"


def test_a_run_with_nothing_blocked_gets_no_row(tmp_path: Path) -> None:
    """沒有被擋下來的時候**整列不出現**。

    一個常駐的「被護欄擋下的指示：—」會出現在每一張卡上，
    然後真的有的那一次淹沒在裡面。

    測兩種「沒有」，因為它們走的是不同的路：

    * **檔案根本不在**——絕大多數執行都是這一種。
    * **檔案在，但裡面那一筆是空的**——比較難想到，而且**只有這一種**
      走得到把資料變成卡片的那幾行。只測前面那一種的話，那幾行的
      破壞永遠是綠的（**修好之後走不到的分支，它的破壞永遠是綠的**）。
    """
    from app.services.project_dashboard import ProjectDashboardService
    from app.services.project_registry import ProjectEntry, ProjectRegistry
    from app.services.timeline_store import TimelineStore

    def item_for(rows: object | None):
        project = tmp_path / (
            "no-file" if rows is None else "empty-rows"
        )
        project.mkdir()
        ProjectRegistry.initialize_project(project)
        run_id = TimelineStore(project).create_run(TASK)
        if rows is not None:
            (
                project / ".aicc" / "runs" / run_id / "blocked-instructions.json"
            ).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        _summary, items = ProjectDashboardService().build(
            [ProjectEntry(name="demo", path=str(project))]
        )
        return items[0]

    assert item_for(None).blocked_instructions == ()
    assert item_for(
        [{"quote": "", "rule_id": "commit", "rule_text": ""}]
    ).blocked_instructions == (), (
        "一筆空的紀錄變成卡片上的一列——那一列上沒有他寫的話，"
        "也沒有哪一條規則，而它會出現在每一張有這個檔案的卡上。"
    )
