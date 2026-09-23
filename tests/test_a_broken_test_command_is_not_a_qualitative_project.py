"""指令跑不起來，**不准被驗收標準洗成綠燈**。

## 這條在守什麼

2026-08-24 老闆拍板：「怎麼算做完了」兩條路**各自獨立**——`verify_command`
與 `acceptance` 兩個都有的專案照樣兩個都跑。**AICC 自己就是那樣。**

而我實作質化驗收的那一段留了一個洞：

`orchestrator.run_tests()` 有**兩處**丟 `VerificationNotDeclared`：

| 在哪 | 意思 |
|---|---|
| `orchestrator.py:1546` | 這個專案**沒有宣告**指令 |
| `orchestrator.py:1565` | 指令宣告了，但**根本沒跑起來**（執行檔不見了）|

第二處的註解自己寫著要「**stop and ask**」。而 `verification.check()` 接住
這個例外的時候**兩種一視同仁**，只要專案有一份合格的驗收標準，就當成
「這是一個質化的案子，機器本來就沒東西可跑」，蓋 `TESTS_BY_RUBRIC` 章，
**綠燈放行**。

## 那個綠燈長什麼樣

一個 `.venv` 被刪掉的 AICC：測試一條都沒跑，畫面上寫「用驗收標準驗過了」。
**不是紅燈、不是警告、不是「請設定」**——是一個看起來完全正常的成功。

而這正好是這個專案最在意的那一類：**畫面在說謊，而說的謊讀起來跟真話
一模一樣。**這台機器 2026-08-21 才發生過一次資料夾被刪掉的事故。

## 為什麼判準是「有沒有宣告指令」，不是「是哪一種例外」

用例外的種類分也做得到（多一個子型別），但那把「這件事為什麼成立」藏進
型別階層裡。真正的道理是一句話：

> **驗收標準不是指令的備胎。**它是給沒有指令的案子走的另一條路，
> 不是給有指令的案子在指令壞掉時退回去的地方。

所以問的是「這個專案有沒有宣告指令」——`declared()` 那道門問的同一句話。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.core.steps import Site, verification
from app.services import qualitative_acceptance
from app.services.project_verification import (
    VerificationNotDeclared,
    VerifyCommandLookup,
)

RUBRIC = {
    "dimensions": [
        {
            "name": "收場說得出憑什麼",
            "examples": [
                {"good": True, "text": "寫「三個章都在，而且蓋在同一版上」"},
                {"good": False, "text": "只寫「都通過了」，沒有指出看了哪幾個章"},
            ],
        },
    ]
}


def _project_with_rubric(tmp_path: Path) -> Path:
    """一個標準**已經被使用者過目過**的專案。

    要簽，因為 2026-08-24 之後沒有人過目的標準不算一把尺
    （`test_a_rubric_nobody_looked_at_is_not_a_ruler.py`）。這一份守的是
    **另一件事**——指令跑不起來的時候會發生什麼——所以要先把那個前提滿足，
    不然兩條規則會混在同一個紅燈裡，而看到紅燈的人分不出是哪一條。
    """
    project = tmp_path / "案子"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "project.json").write_text(
        json.dumps({"acceptance": RUBRIC}, ensure_ascii=False),
        encoding="utf-8",
    )
    qualitative_acceptance.approve(
        project, note="測試裡的使用者看過了", now="2026-08-24T00:00:00+08:00"
    )
    return project


@dataclass
class _FakeStamper:
    stamped: list = field(default_factory=list)

    def verified(self, **kwargs) -> None:  # noqa: ANN003
        self.stamped.append(kwargs)


@dataclass
class _FakeOrchestrator:
    project_path: Path
    # 這個專案有沒有宣告指令。`None` = 沒宣告。
    declared_command: object = None
    # **他在設定檔裡寫過那一行沒有。**跟「跑不跑得起來」是兩件事，
    # 而第一版這個假物件沒有這一格——於是它產出了一個**真實程式產不出來
    # 的形狀**（`command=<物件>` 而執行檔不存在）。真實情況是
    # `read_verify_command()` 在執行檔找不到的時候回 `command=None`，
    # 所以那一版測試綠著，而產品在真的 `.venv` 被刪掉時照樣放行。
    declared_in_file: bool = False
    raised: Exception | None = None

    def verification(self) -> VerifyCommandLookup:
        return VerifyCommandLookup(
            command=self.declared_command,
            problem=r"這個專案宣告的驗證指令跑不起來：找不到 .venv\Scripts\python.exe。",
            declared_in_file=self.declared_in_file,
        )

    def run_tests(self):  # noqa: ANN201
        assert self.raised is not None
        raise self.raised

    def get_git_diff(self) -> str:
        return "--- a/x\n+++ b/x\n+改了一行\n"


#  那個假指令 2026-08-24 拿掉了：**真實程式產不出那個組合**
# （執行檔找不到的時候  回的是 ），
# 而測試用一個產不出來的形狀，守的就是那個假物件不是產品。

    display = ".venv\\Scripts\\python.exe -m pytest -q"
    argv = (".venv\\Scripts\\python.exe", "-m", "pytest", "-q")
    timeout_seconds = 60


def _run(project: Path, *, declared, reason: str, declared_in_file=None) -> tuple:
    said: list[str] = []
    stamper = _FakeStamper()
    site = Site(
        orchestrator=_FakeOrchestrator(
            project_path=project,
            declared_command=declared,
            declared_in_file=(declared is not None)
            if declared_in_file is None
            else declared_in_file,
            raised=VerificationNotDeclared(reason),
        ),
        stamper=stamper,
        on_stage=said.append,
    )
    return verification.check(site), stamper, said


def test_a_declared_command_that_never_started_stops_instead_of_going_green(
    tmp_path: Path,
) -> None:
    """**這一條是這份檔案存在的理由。**

    專案兩樣都有（指令 ＋ 驗收標準），而指令的執行檔不見了。
    在這之前：蓋 `TESTS_BY_RUBRIC`、綠燈、往下走。
    """
    project = _project_with_rubric(tmp_path)

    checked, stamper, _said = _run(
        project,
        declared=None,
        declared_in_file=True,
        reason="Command was not found in PATH: .venv\\Scripts\\python.exe",
    )

    assert checked.ending, (
        "指令跑不起來卻放行了。這個專案宣告過指令，"
        "驗收標準是它**額外**的那一把尺，不是它的備胎。"
    )
    assert not stamper.stamped, (
        "什麼都沒跑就蓋了一個「驗過了」的章："
        f"{stamper.stamped!r}"
    )


def test_the_rubric_is_still_the_road_for_a_project_with_no_command(
    tmp_path: Path,
) -> None:
    """**修法不准把質化那條路一起關掉。**

    剪片、寫文件、做簡報那些案子沒有可執行的檢查，而它們正是質化驗收
    存在的理由。這一條紅了就代表修過頭。
    """
    project = _project_with_rubric(tmp_path)

    checked, stamper, said = _run(
        project, declared=None, reason="這個專案沒有宣告 verify_command"
    )

    assert not checked.ending, (
        "沒有指令的專案有一份合格的驗收標準，卻被擋下來了："
        f"{checked.ending}／{checked.problem}"
    )
    assert stamper.stamped, "走質化那條路照樣要蓋章——不蓋的話紀錄裡這一段是空的"
    assert said, "畫面上要說一句「這一次沒有機器可跑的檢查」"


def test_the_run_is_counted_only_on_the_qualitative_road(tmp_path: Path) -> None:
    """`runs_so_far` 只有真的用了那把尺才加。

    指令壞掉那一次沒有用到標準，加上去的話「前三次請你自己核對」會被一次
    根本沒發生的判斷消耗掉——而那三次是這把尺唯一的校準機會。
    """
    project = _project_with_rubric(tmp_path)

    _run(
        project,
        declared=None,
        declared_in_file=True,
        reason="Command was not found in PATH: .venv\\Scripts\\python.exe",
    )
    after_broken = qualitative_acceptance.read(project)
    assert after_broken is not None
    assert after_broken.runs_so_far == 0, "指令壞掉那一次不該算成用過一次標準"

    _run(project, declared=None, reason="沒有宣告")
    after_real = qualitative_acceptance.read(project)
    assert after_real is not None
    assert after_real.runs_so_far == 1, "真的走質化那條路要算一次"


@pytest.mark.parametrize("declared_in_file", [False, True])
def test_a_project_with_no_rubric_is_never_waved_through(
    tmp_path: Path, declared_in_file
) -> None:
    """兩種情況下，**沒有標準的專案都不准放行**——這一條本來就在，釘住它。"""
    project = tmp_path / "空的"
    (project / ".aicc").mkdir(parents=True)

    checked, stamper, _said = _run(
        project, declared=None, declared_in_file=declared_in_file, reason="沒得驗"
    )

    assert checked.ending, "兩樣都沒有卻放行了"
    assert not stamper.stamped


def test_the_real_shape_from_a_real_project_file(tmp_path: Path) -> None:
    """**不用假物件，直接讀一個真的 project.json。**

    這一條是 2026-08-24 那次掃描逼出來的：上面那幾條用假的 orchestrator，
    而第一版的假物件產出了一個**真實程式產不出來的組合**
    （`command=<物件>` 而執行檔不存在）。於是測試綠著，產品在真的
    `.venv` 被刪掉時照樣放行。

    > **判準**：一個假物件能不能組出真實程式組不出來的狀態，
    > 決定了那條測試守的是產品還是守的是那個假物件。
    """
    from app.services import project_verification

    project = tmp_path / "兩樣都有"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "project.json").write_text(
        json.dumps(
            {
                # 宣告了，但那個執行檔不存在——`.venv` 被刪掉的樣子。
                "verify_command": [".venv/Scripts/python.exe", "-m", "pytest"],
                "acceptance": RUBRIC,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    qualitative_acceptance.approve(project, note="看過了", now="2026-08-24T00:00:00+08:00")

    look = project_verification.read_verify_command(project)
    assert look.command is None, "前提變了：執行檔居然找得到"
    assert look.declared_in_file, (
        "**這一格就是修法的全部**。它是 False 的話，"
        "「有指令的專案不准退回驗收標準」那一刀問的就是一句答不出來的話"
    )


def test_the_gate_before_dispatch_also_refuses_a_broken_command(
    tmp_path: Path,
) -> None:
    """**派工之前那道門也要問**，不是只有測試那一步問。

    現在停的代價是一個對話框；晚一點停的代價是一次已經跑掉的派工，
    加上一棵被人動過的工作樹。而這一條 2026-08-24 第一版沒有——
    當天只修了測試那一步，`DECISIONS.md` 明寫的是兩道門都問。
    """
    from app.services import project_verification

    project = tmp_path / "兩樣都有"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "project.json").write_text(
        json.dumps(
            {
                "verify_command": [".venv/Scripts/python.exe", "-m", "pytest"],
                "acceptance": RUBRIC,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    qualitative_acceptance.approve(project, note="看過了", now="2026-08-24T00:00:00+08:00")

    look = project_verification.read_verify_command(project)
    site = Site(
        orchestrator=_FakeOrchestrator(
            project_path=project,
            declared_command=look.command,
            declared_in_file=look.declared_in_file,
        ),
        stamper=_FakeStamper(),
        on_stage=lambda _m: None,
    )
    blocked = verification.declared(site)
    assert blocked, (
        "指令跑不起來，卻在派工之前就被放行了——"
        "驗收標準是它額外的那一把尺，不是它的備胎"
    )


def test_the_gate_before_dispatch_still_lets_a_pure_qualitative_project_through(
    tmp_path: Path,
) -> None:
    """**不准擋過頭**：剪片那種案子從來沒宣告過指令，照樣要走得動。"""
    project = _project_with_rubric(tmp_path)
    site = Site(
        orchestrator=_FakeOrchestrator(
            project_path=project, declared_command=None, declared_in_file=False
        ),
        stamper=_FakeStamper(),
        on_stage=lambda _m: None,
    )
    assert verification.declared(site) == ""
