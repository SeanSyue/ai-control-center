"""每一種收場都要有人替它想過畫面要說什麼。

2026-08-23 頁面設計那一輪查到的事：

> 二十幾種收場裡有十種被畫成紅燈（而它們都不是失敗），還有一種——**章不齊
> 的那一種**——被畫成薄荷色，旁邊寫著「工作流程已完成」。

成因不是誰粗心，是畫面那條判定鏈最後掛了一個 `else`：

    else:
        status_text, status_style = review_status, "error"

**有預設值的分類等於沒有分類。**漏掉一種跟處理過一種在畫面上長得一模一樣，
所以它漏了十種，漏了很久，而且沒有任何一條測試變紅過。

這一份就是那個紅燈：引擎新增一種收場而沒有人替它想過畫面要說什麼的時候，
這裡會紅。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core import outcomes
from app.services.approval_chain import Verdict

WORKER = Path(__file__).resolve().parents[1] / "app" / "core" / "workflow_worker.py"


def _statuses_the_engine_can_produce() -> set[str]:
    """引擎實際吐得出來的收場碼。

    **從程式碼裡撈，不是照著抄一份清單。**抄一份的話，新增一種收場的人要記得
    改兩個地方，而「要記得」正是這條檢查要取代的東西。

    兩個來源：主程式的 `workflow_status="X"`，以及每一扇門的
    `Stop(status="X")` 與 `ending="X"`（`_as_result` 把它們原樣帶出去）。

    門那一側用語法樹比對而不是用字串比對，是因為門後還有別的東西叫 `status`
    ——複查那一扇的 `Reviewed(status="PASS")` 是**複查的狀態**，不是收場。
    用字串比會把它撈進來，而那條紅燈是假的。
    """
    source = WORKER.read_text(encoding="utf-8")
    found = set(re.findall(r'workflow_status=\s*"([A-Z_]+)"', source))
    for path in (WORKER.parent / "steps").glob("*.py"):
        found |= _endings_in(ast.parse(path.read_text(encoding="utf-8")))
    return found


def _endings_in(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "ending":
            if isinstance(node.value, ast.Constant) and node.value.value:
                found.add(str(node.value.value))
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "Stop"):
            continue
        for keyword in node.keywords:
            if keyword.arg == "status" and isinstance(
                keyword.value, ast.Constant
            ):
                found.add(str(keyword.value.value))
    return found


def test_the_engine_really_does_produce_more_than_a_handful() -> None:
    """先確認上面那個撈法沒有撈空。

    撈空的話底下每一條都會「全部通過」——一條抓不到東西的檢查，讀起來跟
    一條全過的檢查一模一樣。
    """
    assert len(_statuses_the_engine_can_produce()) >= 15


def test_every_ending_the_engine_can_produce_has_a_row() -> None:
    known = outcomes.known_statuses()
    missing = sorted(_statuses_the_engine_can_produce() - known)
    assert not missing, (
        "這幾種收場沒有人替它寫過畫面要說什麼："
        + "、".join(missing)
        + "。到 app/core/outcomes.py 補一行——**不要加預設值**。"
    )


def test_every_undecidable_verdict_has_a_row() -> None:
    """`NOT_DECIDABLE` 一種橫跨四組，六種判定一種都不能漏。

    漏掉一種的話 `describe()` 會丟例外，而畫面會退成「沒認出來的結局」——
    那比畫錯好，但仍然是一次使用者看得到的瑕疵。
    """
    missing = sorted(
        v.name for v in Verdict if v.name not in outcomes._UNDECIDABLE
    )
    assert not missing, "這幾種判定沒有分組：" + "、".join(missing)


def test_there_is_no_default_row() -> None:
    """沒列到的收場要炸開，不准回一個「其他」。"""
    with pytest.raises(outcomes.UnknownOutcome):
        outcomes.describe({"workflow_status": "SOMETHING_NOBODY_THOUGHT_OF"})
    with pytest.raises(outcomes.UnknownOutcome):
        outcomes.describe({})


def test_the_screen_never_prints_an_engine_code() -> None:
    """畫面上的字不准是引擎的狀態碼。客群不是工程師。

    改壞的樣子就是它原本的樣子：`status_text = review_status`——
    螢幕上出現「NOT_RUN」四個字，旁邊寫「無法判定結果」。

    （「PASS」是刻意留的：那是 2026 年之前就在畫面上的字，不是這一輪漏出來的
    碼。所以這裡比的是**這一次的狀態碼**，不是「看起來像不像英文」。）
    """
    for status in outcomes.known_statuses():
        result = {"workflow_status": status, "review_status": "NOT_RUN"}
        if status == outcomes.UNDECIDABLE_STATUS:
            result["decision_verdict"] = "MISSING"
        outcome = outcomes.describe(result)
        assert outcome.label != status, f"{status} 的畫面文字就是它自己的碼"
        assert outcome.label != "NOT_RUN"
        assert outcome.stage.strip()
        assert outcome.stage != "無法判定結果"


def test_not_decidable_is_never_painted_as_finished() -> None:
    """**這一條是這份檔案存在的理由。**

    章不齊那一種曾經是薄荷色、寫著「PASS」、旁邊「工作流程已完成」。

    > 其他十種畫成紅的，使用者至少會去看；
    > 這一種畫成綠的，他不會去看，而那份東西事後回溯不出來。
    """
    for verdict in Verdict:
        if verdict is Verdict.READY:
            continue
        outcome = outcomes.describe(
            {
                "workflow_status": outcomes.UNDECIDABLE_STATUS,
                "decision_verdict": verdict.name,
            }
        )
        assert outcome.style != "done", f"{verdict.name} 被畫成完成了"
        assert outcome.group != outcomes.GROUP_DONE
        assert "完成" not in outcome.stage


def test_missing_stamps_and_a_red_check_do_not_land_in_the_same_group() -> None:
    """同一個狀態碼底下，這兩件事使用者要做的事完全相反。

    缺章 → 再跑一次就補上。有檢查是紅的 → 重跑一定再紅一次，要先去修。
    畫成同一格的話，是叫人去按一顆一定會失敗的按鈕。
    """
    base = {"workflow_status": outcomes.UNDECIDABLE_STATUS}
    missing = outcomes.describe(base | {"decision_verdict": "MISSING"})
    failed = outcomes.describe(base | {"decision_verdict": "FAILED"})
    assert missing.group != failed.group
    assert missing.group == outcomes.GROUP_RERUN
    assert failed.group == outcomes.GROUP_FIX


def test_only_the_group_that_is_still_waiting_counts_as_waiting() -> None:
    """脈動的那一種金色是「有人在等你回話」，只有一組配得上。

    多一組進去，使用者會對著一個沒有回答框的畫面等 AI 回應。
    """
    assert outcomes.WAITING_GROUPS == {outcomes.GROUP_ASK}
    assert outcomes.describe({"workflow_status": "ASK_USER"}).waiting
    assert outcomes.describe({"workflow_status": "INTAKE_ASKING"}).waiting
    assert not outcomes.describe({"workflow_status": "COMPLETED"}).waiting
    assert not outcomes.describe(
        {"workflow_status": "FIX_LIMIT_REACHED"}
    ).waiting


def test_every_group_has_a_name_a_human_can_read() -> None:
    for status in outcomes.known_statuses():
        result = {"workflow_status": status, "decision_verdict": "MISSING"}
        assert outcomes.describe(result).group_label


def test_the_more_specific_sentence_wins() -> None:
    """結果裡有更具體的說明時，畫面要用那一句，不是用預設那句。

    不然使用者看到的永遠是「請先設定」，而引擎其實已經寫好了缺哪一項。
    """
    generic = outcomes.describe({"workflow_status": "VERIFY_NOT_CONFIGURED"})
    specific = outcomes.describe(
        {
            "workflow_status": "VERIFY_NOT_CONFIGURED",
            "verification_problem": "這個專案沒有宣告 verify_command",
        }
    )
    assert specific.stage == "這個專案沒有宣告 verify_command"
    assert specific.stage != generic.stage


def test_the_screen_does_not_keep_its_own_copy_of_the_table() -> None:
    """畫面不准再長出第二張表。

    改壞的樣子：有人為了「順手」在 main_window 再寫一次
    `if workflow_status == ...`——那就是四份互相漂開的答案的第二份。

    今天允許的只有一處，而且不是在判定顏色：修正上限那顆彈窗。它決定的是
    「要不要跳一個對話框」，不是「這一格畫什麼顏色」。
    """
    source = (
        Path(__file__).resolve().parents[1] / "app" / "ui" / "main_window.py"
    ).read_text(encoding="utf-8")
    comparisons = re.findall(r'workflow_status\s*==\s*"([A-Z_]+)"', source)
    assert comparisons == ["FIX_LIMIT_REACHED"], (
        "main_window 又開始自己判定收場了："
        + "、".join(comparisons)
        + "。收場長什麼樣只有 app/core/outcomes.py 知道。"
    )
    # 那條 else 不准回來。
    assert '"無法判定結果"' not in source


def test_the_record_that_lands_on_disk_carries_the_group() -> None:
    """分組要蓋在**引擎唯一的出口**，不是蓋在畫面那一側。

    蓋在畫面那一側的話，落地的紀錄（時間軸、`workflow-result.json`）沒有它
    ——而事後回頭看一次執行的人，看到的正是那份紀錄。

    這也是 `tools/zero_callers.py` 存在的理由：一個算出來了、推出去了、
    沒有人讀的值，跟一個根本沒算的值一樣沒用。
    """
    source = WORKER.read_text(encoding="utf-8")
    body = source[source.index("def _complete(self, result: dict)"):]
    stamp = body.index('result["outcome_group"]')
    finalize = body.index("finalize_run")
    assert stamp < finalize, (
        "分組蓋在 finalize_run 之後——落地的紀錄裡不會有它"
    )
