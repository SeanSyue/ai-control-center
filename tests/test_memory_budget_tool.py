"""`tools/memory_budget.py`：共同記憶實際送出去多少，以及有沒有被切掉。

**這支工具原本量錯了對象。** 它的通過條件是「只要有任何一個模式拿得到全部就算
OK」，而拿得到全部的是 `strict`（深入）——出廠預設是 `minimal`（省額度）。
所以它在「預設模式下八份全被切成 750 字、安全規則斷在句子中間」的狀態下，
**每一次都是綠燈**。

一個量錯對象的綠燈，比沒有綠燈糟：它讓人以為這件事已經有人看著了。

2026-08-21 換了問題：不再問「有沒有任何一個模式做得到」，改問「**使用者選得到
的每一個設定，八份是不是都完整送得出去**」，並且回報實際送出的字數與 token 量。

**這裡不讀這個 repo 自己的 `.aicc/knowledge/`。** 那些檔案沒有進版本控制，
在乾淨的 clone 上不存在，所以那樣寫出來的測試在別台機器上要嘛跳過（違反零跳過
標準）要嘛紅得莫名其妙。改成用 tmp 專案餵已知的長度。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.services.project_knowledge import (
    CONTEXT_PROFILES,
    MEMORY_FILES,
    MEMORY_MUST_ARRIVE_WHOLE,
    ProjectKnowledgeStore,
)

ROOT = Path(__file__).resolve().parents[1]
# 當年最大的那個上限。工具現在必須在**遠大於它**的記憶量下仍然回報「完整」。
FORMER_BIGGEST_BUDGET = 22_000


def _tool():
    """把工具當模組載進來。

    先註冊進 `sys.modules` 再 exec——`spec_from_file_location` 少了這一步，
    模組裡的 dataclass 會在解析型別註記時找不到自己（這個專案踩過一次）。
    """
    name = "memory_budget_tool"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "tools" / "memory_budget.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _project(root: Path, each: int) -> Path:
    project = root / f"proj{each}"
    knowledge = project / ".aicc" / "knowledge"
    knowledge.mkdir(parents=True)
    for file_name in MEMORY_FILES:
        (knowledge / file_name).write_text(
            f"{file_name}開頭" + "字" * each + f"{file_name}結尾", encoding="utf-8"
        )
    return project


# --- 判定 --------------------------------------------------------------------


def test_the_default_profile_is_the_one_it_judges(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """**這是這支工具現在存在的理由。**

    舊判定是「至少有一個模式拿得到全部」，所以只要最寬鬆的那個模式過了就綠。
    新判定要求每一個設定都過——包含出廠預設。這裡餵的量在舊規則下只有
    `strict` 撐得住，所以舊判定會綠、也應該綠；新判定要能在**同一份輸入上**
    給出正確答案，靠的是截斷已經不存在了。
    """
    each = FORMER_BIGGEST_BUDGET // len(MEMORY_FILES) - 50

    code = _tool().report(_project(tmp_path, each))

    assert code == 0
    out = capsys.readouterr().out
    for name in CONTEXT_PROFILES:
        assert f"{name}" in out and "8/8" in out


def test_long_files_are_still_delivered_whole(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """一份就吃掉當年整個最大預算——舊工具在這裡回非零並叫人去瘦身。

    現在這不是問題：文件長不是罪，切掉安全規則才是。八份全部送到，回 0。
    """
    code = _tool().report(_project(tmp_path, FORMER_BIGGEST_BUDGET))

    assert code == 0
    out = capsys.readouterr().out
    assert "8/8" in out
    assert "完整送到" in out


def test_it_reports_how_much_actually_goes_out(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """它的新角色：回報實際送出多少，不是判斷有沒有超標。

    數字要對得上真的組裝函式，不是一個估算——這個專案已經有過一個「原始量
    自己也被截斷」的缺口數字。
    """
    project = _project(tmp_path, 500)

    _tool().report(project)

    out = capsys.readouterr().out
    sent = len(ProjectKnowledgeStore(project).build_memory_context())
    assert f"{sent:,}" in out
    assert f"{sent // 3:,}" in out, "沒有回報約略的 token 量"


def test_it_names_the_files_that_may_not_be_cut(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    _tool().report(_project(tmp_path, 500))

    out = capsys.readouterr().out
    for file_name in MEMORY_MUST_ARRIVE_WHOLE:
        assert file_name in out
    assert "不准截斷" in out


def test_it_goes_red_when_something_gets_cut_again(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**這條是這支工具的牙齒。**

    今天不會紅是對的——沒有人在切。但一支永遠不會紅的工具跟沒有那支工具的
    差別，是它會讓人以為這件事已經有人看著了（舊版正是如此）。所以這裡把
    截斷放回去，工具必須紅、必須點名是哪一份、而且**不准建議去調高上限**。
    """
    whole = ProjectKnowledgeStore.build_context

    def cut(self, task, *args, **kwargs):  # noqa: ANN001
        return whole(self, task, *args, **kwargs)[:400]

    monkeypatch.setattr(ProjectKnowledgeStore, "build_context", cut)

    code = _tool().report(_project(tmp_path, 3_000))

    assert code == 1
    out = capsys.readouterr().out
    assert "沒有完整送到" in out
    assert "SECURITY_MODEL.md" in out
    assert "不要用調高上限解決" in out


def test_it_goes_red_when_the_dispatch_path_gets_cut(
    tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**派工那條路也要被量到。**

    2026-08-23【規則分三層】上線之前，這支工具只量 `build_context()` 不帶
    職責的那條路——而那條路是給**人**看的文件（交接包、啟動脈絡）走的。
    派工走的是另一條。

    少了這一條，分層上線的那一天，這支工具會繼續對著**一條沒有人在跑的
    路徑**印綠燈，而真正送到 AI 手上的安全規則被剪了一半也不會有人知道。
    """
    whole = ProjectKnowledgeStore.build_dispatch_context

    def cut(self, duty):  # noqa: ANN001
        """只剪安全規則的**內文**，標題與點名那一行原封不動。

        剪成前 400 字那種粗暴的切法，點名那條檢查也會跟著紅——那樣這條測試
        分不出是哪一條在守，而**分不出來的紅燈守不住任何一條**。
        """
        sent = whole(self, duty)
        body = (
            (self.knowledge_path / "SECURITY_MODEL.md")
            .read_text(encoding="utf-8")
            .strip()
        )
        return sent.replace(body, body[: len(body) // 2])

    monkeypatch.setattr(ProjectKnowledgeStore, "build_dispatch_context", cut)

    code = _tool().report(_project(tmp_path, 3_000))

    assert code == 1
    out = capsys.readouterr().out
    assert "派工" in out
    assert "SECURITY_MODEL.md" in out
    assert "全文對不上硬碟原文" in out


def test_it_shows_which_files_each_seat_gets(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """看不到「這一格送了哪幾份、哪幾份只給指標」，「該送的」就變成一句
    沒有人能反駁的話。
    """
    _tool().report(_project(tmp_path, 500))
    out = capsys.readouterr().out

    assert "派工時每一格實際收到什麼" in out
    for duty in ("plan", "work", "review", "approve"):
        assert duty in out
    assert "逐字：" in out
    assert "點名：" in out


def test_a_project_with_no_shared_memory_says_so_instead_of_pretending(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """沒有共同記憶檔時要回非零並說出來。

    印一張「0 份、100%」的漂亮表格，是這個專案一直在抓的那種答案。
    """
    empty = tmp_path / "empty"
    (empty / ".aicc" / "knowledge").mkdir(parents=True)

    code = _tool().report(empty)

    assert code == 1
    assert "UNAVAILABLE" in capsys.readouterr().out


def test_it_reports_every_profile(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _tool().report(_project(tmp_path, 500))

    out = capsys.readouterr().out
    for name in CONTEXT_PROFILES:
        assert name in out, out


# --- 分類（節錄／只留名／完整）----------------------------------------------


@pytest.mark.parametrize(
    "context, expected",
    [
        ("### Architecture\n內容", "完整"),
        ("### Architecture（節錄 1,500／26,424 字）\n內容", "節錄"),
        ("### Architecture\n（這次沒有送出：全文 5,500 字，預算排不到。）", "只留名"),
        ("### Decisions\n內容", "沒出現"),
    ],
)
def test_classify(context: str, expected: str) -> None:
    """「節錄」與「只留名」現在的組裝路徑不會產生——留著是為了認得出它們。

    認不出來的話，一個被重新加上截斷的檔案會被字面比對當成「完整」。
    """
    assert _tool().classify(context, "Architecture") == expected


def test_classify_does_not_confuse_a_truncated_file_with_a_complete_one() -> None:
    """節錄版的標題後面多了「（節錄 …）」，所以字面比對會漏掉它。

    第一次寫這個判斷時就是這樣錯的：探針回報「0/8 進得去」，而實際上八份都在。
    量錯的探針比沒有探針糟——它會讓人去修一個不存在的問題。
    """
    context = "### Architecture（節錄 1,500／26,424 字）\n內容"

    assert _tool().classify(context, "Architecture") != "沒出現"
