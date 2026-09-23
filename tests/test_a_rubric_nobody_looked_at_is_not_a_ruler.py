"""沒有人過目的驗收標準**不算一把尺**，改過之後也要再看一次。

老闆 2026-08-24：

> 那七條是我寫的⋯⋯**AI 起草是可以的，但起草的東西在他過目之前不算一把尺，
> 也不准拿它量出來的結果當證據。**

在這之前那句話**只活在一份 Markdown 裡**，而這個專案同一天才證實過：
**沒有機制的規則是叮嚀，而叮嚀失敗的時候不會有任何東西變紅。**

## 為什麼指紋，不是一個布林

一個 `approved: true` 是一張**永久通行證**：核准的時候兩個維度，半年後
變成十個，畫面上照樣寫著「已核准」。綁內容之後，改一個字核准就自動失效。

## 這一份**擋得住什麼、擋不住什麼**

擋得住：一份沒有人看過的標準被拿去驗收；核准之後偷偷改內容。

**擋不住**：一個決定要騙人的呼叫端直接呼叫 `approve()`。機器分不出
「使用者按了那顆按鈕」跟「程式自己呼叫了這個函式」——那一題只有畫面
那一側關得起來（那顆按鈕是唯一的呼叫端）。

寫下來是因為【每一條檢查都講得出改壞的樣子】：這一條防的是
**「沒有人看過，而且沒有人看得出來沒有人看過」**，不是防偽造。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.core.steps import Site, verification
from app.services import qualitative_acceptance as qa
from app.services.project_verification import (
    VerificationNotDeclared,
    VerifyCommandLookup,
)

NOW = "2026-08-24T04:30:00+08:00"

GOOD = {
    "dimensions": [
        {
            "name": "收場說得出憑什麼",
            "examples": [
                {"good": True, "text": "寫「三個章都在，而且蓋在同一版上」"},
                {"good": False, "text": "只寫「都通過了」，沒有指出看了哪幾個章"},
            ],
        }
    ]
}


def _project(tmp_path: Path, acceptance: dict | None = GOOD) -> Path:
    project = tmp_path / "案子"
    (project / ".aicc").mkdir(parents=True)
    payload: dict = {"name": "案子"}
    if acceptance is not None:
        payload["acceptance"] = acceptance
    (project / ".aicc" / "project.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return project


# ---- 那份標準的三種身分 ------------------------------------------------


def test_a_fresh_rubric_has_never_been_looked_at(tmp_path: Path) -> None:
    """**預設是不算數。**這一條是整份檔案的地基。"""
    rubric = qa.read(_project(tmp_path))
    assert rubric is not None
    assert qa.approval_state(rubric) == qa.NEVER_LOOKED_AT
    assert not rubric.approved
    assert qa.why_it_does_not_count(rubric)


def test_approving_it_makes_it_count(tmp_path: Path) -> None:
    project = _project(tmp_path)
    stored = qa.approve(project, note="很棒 全部按照你建議的做", now=NOW)

    assert qa.approval_state(stored) == qa.APPROVED
    assert stored.approved
    assert qa.why_it_does_not_count(stored) == ""
    assert stored.approval is not None
    # **他當時說的話要留著**，不是留一個「已核准」。
    assert stored.approval.note == "很棒 全部按照你建議的做"
    assert stored.approval.at == NOW


def test_editing_a_dimension_takes_the_approval_away(tmp_path: Path) -> None:
    """**這一條是這份檔案存在的理由。**

    核准是綁在**那一版內容**上的，不是綁在專案上。改一個字就要再看一次。
    """
    project = _project(tmp_path)
    qa.approve(project, note="可以", now=NOW)

    edited = qa.Rubric(
        dimensions=(
            qa.Dimension(
                name="收場說得出憑什麼",
                examples=(
                    qa.Example(True, "寫「三個章都在，而且蓋在同一版上」"),
                    qa.Example(False, "只寫「都通過了」，沒有指出看了哪幾個章"),
                    qa.Example(False, "**這一條是後來偷偷加的**，他沒有看過"),
                ),
            ),
        )
    )
    qa.save(project, edited)

    after = qa.read(project)
    assert after is not None
    assert qa.approval_state(after) == qa.CHANGED_SINCE, (
        "改了一條標準，核准卻還在——那張通行證變成永久的了"
    )
    assert not after.approved
    # 訊息要講得出「什麼時候被過目的」，不然他不知道自己在跟哪一版比。
    assert NOW in qa.why_it_does_not_count(after)


def test_the_three_states_never_read_the_same(tmp_path: Path) -> None:
    """「我改了一個字」跟「我從來沒看過」要的是不同的下一步。

    **這一條原本測錯了對象**：它比的是「沒看過」跟「已核准」，而已核准回的
    是空字串——兩者當然不一樣，所以把 `CHANGED_SINCE` 改成跟 `NEVER_LOOKED_AT`
    講同一句話，它照樣是綠的（破壞驗證 A7 抓到的）。要比的是**兩種不算數的
    理由**，因為它們才是會被寫成同一句話的那一對。
    """
    fresh = qa.read(_project(tmp_path / "a"))

    changed_project = _project(tmp_path / "b")
    qa.approve(changed_project, note="可以", now=NOW)
    qa.save(
        changed_project,
        qa.Rubric(
            dimensions=(
                qa.Dimension(
                    name="改過的維度",
                    examples=(
                        qa.Example(True, "這一版他沒有看過，長度要夠"),
                        qa.Example(False, "這一版他沒有看過，反例也要夠長"),
                    ),
                ),
            )
        ),
    )
    changed = qa.read(changed_project)

    assert fresh is not None and changed is not None
    assert qa.approval_state(fresh) == qa.NEVER_LOOKED_AT
    assert qa.approval_state(changed) == qa.CHANGED_SINCE
    assert qa.why_it_does_not_count(fresh) != qa.why_it_does_not_count(changed), (
        "「從來沒看過」跟「看過但被改了」講的是同一句話——"
        "而它們要的下一步不一樣：一個是「請看一遍」，"
        "一個是「你看過的那一版跟現在這一版不同」"
    )


# ---- save() 不准變成第二個核准入口 -------------------------------------


def test_save_cannot_smuggle_in_an_approval(tmp_path: Path) -> None:
    """呼叫端組一個「已核准」的 Rubric 送進 `save()`，**不准生效**。

    照收的話 `save()` 就是第二個寫得出核准的入口，而那正好把 `approve()`
    那道門繞過去——**一道有旁路的門等於沒有門**。
    """
    project = _project(tmp_path)
    forged = qa.Rubric(
        dimensions=qa.read(project).dimensions,
        approval=qa.Approval(at=NOW, digest="不管這裡寫什麼", note="我自己說可以"),
    )
    qa.save(project, forged)

    after = qa.read(project)
    assert after is not None
    assert qa.approval_state(after) == qa.NEVER_LOOKED_AT, (
        "`save()` 把呼叫端送進來的核准照收了"
    )


def test_save_does_not_lose_a_real_approval_when_nothing_changed(
    tmp_path: Path,
) -> None:
    """存一份**一模一樣**的回去，核准要留著——不然每次存檔都要重簽。"""
    project = _project(tmp_path)
    qa.approve(project, note="可以", now=NOW)
    qa.save(project, qa.read(project))

    after = qa.read(project)
    assert after is not None and after.approved


def test_a_broken_rubric_cannot_be_approved(tmp_path: Path) -> None:
    """核准一份機器已經說不行的標準，等於用他的名字替它背書。"""
    project = _project(tmp_path, {"dimensions": [{"name": "太少", "examples": []}]})
    with pytest.raises(qa.RubricProblem):
        qa.approve(project, note="可以", now=NOW)


def test_there_is_nothing_to_approve_without_a_rubric(tmp_path: Path) -> None:
    project = _project(tmp_path, acceptance=None)
    with pytest.raises(qa.RubricProblem):
        qa.approve(project, note="可以", now=NOW)


def test_a_broken_approval_block_falls_back_to_never_looked_at(
    tmp_path: Path,
) -> None:
    """核准那一格壞掉 → 退到**比較嚴**的那一邊，不是讓整份標準讀不出來。

    丟例外的話畫面上會變成「這個專案沒有標準」，而那會被讀成「還沒設定」
    ——**比較鬆**的結果。
    """
    # 兩種壞法各測一次：**整格不是物件**，以及**是物件但沒有指紋**。
    # 只測第一種的話，守著第二種的那一行（`if digest:`）根本不會被執行到
    # ——破壞驗證 A6 就是這樣照樣綠的。
    for broken in ("這不是一個物件", {}, {"at": NOW, "note": "沒有指紋"}):
        project = _project(tmp_path / f"壞法{hash(str(broken)) & 0xff}", {**GOOD, "approval": broken})
        rubric = qa.read(project)
        assert rubric is not None
        assert qa.approval_state(rubric) == qa.NEVER_LOOKED_AT, (
            f"核准那一格壞成 {broken!r} 卻被當成有核准"
        )


def test_running_it_does_not_invalidate_the_approval(tmp_path: Path) -> None:
    """跑一次不算改動標準——指紋不含 `runs_so_far`。

    含的話，第一次跑完核准就失效，而那一次沒有任何一條標準被動過。
    """
    project = _project(tmp_path)
    qa.approve(project, note="可以", now=NOW)
    qa.record_run(project)

    after = qa.read(project)
    assert after is not None
    assert after.runs_so_far == 1
    assert after.approved, "跑一次就把核准弄掉了"


# ---- 兩道門都要問 -------------------------------------------------------


@dataclass
class _FakeStamper:
    stamped: list = field(default_factory=list)

    def verified(self, **kwargs) -> None:  # noqa: ANN003
        self.stamped.append(kwargs)


@dataclass
class _FakeOrchestrator:
    project_path: Path
    declared_command: object = None

    def verification(self) -> VerifyCommandLookup:
        return VerifyCommandLookup(command=self.declared_command, problem="沒宣告")

    def run_tests(self):  # noqa: ANN201
        raise VerificationNotDeclared("這個專案沒有宣告 verify_command")

    def get_git_diff(self) -> str:
        return "--- a/x\n+++ b/x\n+改了\n"


def _site(project: Path) -> tuple[Site, _FakeStamper]:
    stamper = _FakeStamper()
    return (
        Site(
            orchestrator=_FakeOrchestrator(project_path=project),
            stamper=stamper,
            on_stage=lambda _m: None,
        ),
        stamper,
    )


def test_the_gate_before_dispatch_stops_an_unapproved_rubric(
    tmp_path: Path,
) -> None:
    """派工**之前**就停——現在停是一個對話框，晚一點停是一次跑掉的派工。"""
    site, _ = _site(_project(tmp_path))
    assert verification.declared(site), "沒有人過目的標準卻放行派工了"


def test_the_gate_before_dispatch_lets_an_approved_one_through(
    tmp_path: Path,
) -> None:
    """**不准擋過頭**：核准過的照樣走得動，這一條紅了代表修過頭。"""
    project = _project(tmp_path)
    qa.approve(project, note="可以", now=NOW)
    site, _ = _site(project)
    assert verification.declared(site) == ""


def test_the_verification_step_asks_again(tmp_path: Path) -> None:
    """兩道門各問一次**不是重複**——中間隔著一整輪派工，而標準編得動。"""
    project = _project(tmp_path)
    qa.approve(project, note="可以", now=NOW)
    site, stamper = _site(project)
    assert verification.check(site).ending == "", "核准過的卻被擋在測試那一步"

    # 派工之後有人改了標準（畫面上編得動，那是刻意的）。
    qa.save(
        project,
        qa.Rubric(
            dimensions=(
                qa.Dimension(
                    name="換了一個維度",
                    examples=(
                        qa.Example(True, "這一版他沒有看過，長度要夠"),
                        qa.Example(False, "這一版他沒有看過，反例也要夠長"),
                    ),
                ),
            )
        ),
    )
    site2, stamper2 = _site(project)
    checked = verification.check(site2)
    assert checked.ending, "改過的那一版沒有人看過，卻蓋章放行了"
    assert not stamper2.stamped
