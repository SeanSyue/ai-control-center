"""交出去的那份紀錄，別人在自己的機器上驗得起來（`tools/verify_decision_log.py`）。

這支工具是整個稽核日誌**說服力的來源**。它自己的 docstring 說得最清楚：

> A log verified only by the program that wrote it is worth less to whoever is
> asking for it - the point of handing someone this file is that they can check
> it themselves, on their machine, without trusting ours.

而在 2026-08-11 之前，**沒有任何測試碰過它**。整條路徑（匯出 → 交出去 → 對方
自己跑）一次都沒有被自動驗過，而它是這個產品最強的那個宣稱：`PITCH.md` 敢寫
「證明 AI 生成的判斷有經過人員審核」，靠的就是這條鏈。

實測（手動跑過一次，然後變成這個檔案）結論是 ROADMAP 寫的每一句都成立，
包含它自己寫下的那個界線：**從結尾整段砍掉抓不到**。所以這裡連那一條也釘住
——它是宣稱的邊界，而邊界最容易在日後被人「順手放大回去」。

跑真的子行程，不是 import 進來呼叫函式：這支工具的價值就在於**不必開 AICC**，
而那句話只有真的從命令列跑起來才算驗過。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "verify_decision_log.py"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
ENTRIES = 5


@pytest.fixture(scope="module")
def exported(tmp_path_factory, ) -> tuple[Path, list[str]]:
    """(匯出的 .jsonl, 它的每一行)。"""
    from app.services.decision_export import DecisionExportService
    from app.services.long_task import DecisionLogEntry, DecisionLogStore
    from app.services.project_registry import ProjectRegistry

    root = tmp_path_factory.mktemp("chain")
    project = root / "project"
    (project / ".aicc").mkdir(parents=True)
    ProjectRegistry.initialize_project(project)

    store = DecisionLogStore(project)
    for index in range(ENTRIES):
        store.append(
            DecisionLogEntry(
                run_id=f"run-{index}",
                action="ANSWERED",
                question=f"問題 {index}：要不要覆寫 index.html？",
                answer="可以",
                answered_by="user",
                reason="測試用",
            )
        )

    out = root / "out"
    out.mkdir()
    result = DecisionExportService(project).export(out)
    assert result.jsonl_path is not None, "匯出沒有產生 .jsonl"
    return result.jsonl_path, result.jsonl_path.read_text(
        encoding="utf-8"
    ).splitlines()


def _run(path: Path) -> tuple[int, str]:
    if not PYTHON.is_file():  # pragma: no cover - 環境問題，不是產品問題
        pytest.skip(f"找不到 venv 的 python：{PYTHON}")
    proc = subprocess.run(
        [str(PYTHON), str(TOOL), str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return proc.returncode, proc.stdout


def _tampered(exported, name: str, lines: list[str]) -> Path:
    path = exported[0].with_name(f"{name}.jsonl")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- 沒有動過的那一份 --------------------------------------------------------


def test_an_untouched_export_passes(exported) -> None:
    code, output = _run(exported[0])

    assert code == 0, output
    assert f"{ENTRIES} 筆通過驗證" in output


def test_the_export_is_a_verbatim_copy_of_the_log(exported, ) -> None:
    """`.jsonl` 不重新序列化，而這一條是**功能性的**不是潔癖。

    key 順序一變，指紋就對不上，於是**匯出的副本會通不過自己的驗證**——
    而發現的那一刻正好是交給稽核的那一刻。
    """
    from app.services.long_task import DecisionLogStore

    path, lines = exported
    # 問 store 自己檔案在哪，不要在這裡拼路徑：拼錯的話這條會變成
    # 「找不到檔案」而不是「內容不一樣」，那是兩種完全不同的結論。
    source = DecisionLogStore(path.parent.parent / "project").path

    assert source.is_file(), f"找不到原始日誌：{source}"
    assert lines == source.read_text(encoding="utf-8").splitlines()


def test_the_pass_message_says_what_it_does_not_prove(exported) -> None:
    # 一句沒有界線的「通過驗證」會被當成「這份紀錄是真的」。
    _, output = _run(exported[0])

    assert "不能證明紀錄本身誠實" in output


# --- 動過手腳的 -------------------------------------------------------------


def test_editing_one_entry_is_caught(exported) -> None:
    lines = list(exported[1])
    obj = json.loads(lines[2])
    obj["answer"] = "不可以"
    lines[2] = json.dumps(obj, ensure_ascii=False)

    code, output = _run(_tampered(exported, "edited", lines))

    assert code == 1, output
    assert "第 3 筆" in output and "被改過" in output


def test_rewriting_only_the_hash_is_caught(exported) -> None:
    # 內容不動、只改指紋。這是「把鏈修好看」最省事的做法。
    lines = list(exported[1])
    obj = json.loads(lines[3])
    obj["hash"] = "0" * 64
    lines[3] = json.dumps(obj, ensure_ascii=False)

    code, output = _run(_tampered(exported, "rehashed", lines))

    assert code == 1, output
    assert "第 4 筆" in output


def test_deleting_a_middle_entry_is_caught(exported) -> None:
    lines = exported[1][:2] + exported[1][3:]

    code, output = _run(_tampered(exported, "deleted", lines))

    assert code == 1, output
    assert "接不上" in output


def test_inserting_an_entry_is_caught(exported) -> None:
    lines = list(exported[1])
    extra = json.loads(lines[1])
    extra["question"] = "偷偷加進來的一筆"
    lines.insert(3, json.dumps(extra, ensure_ascii=False))

    code, output = _run(_tampered(exported, "inserted", lines))

    assert code == 1, output
    assert "接不上" in output


# --- 宣稱的邊界 -------------------------------------------------------------


def test_truncating_the_end_is_NOT_caught(exported) -> None:
    """**這一條刻意釘住一件做不到的事。**

    從結尾整段砍掉不留痕跡，因為沒有地方記著「應該有幾筆」。ROADMAP P8 的
    「能證明／不能證明」那張表裡就寫著這一句，而初稿寫的是「任何一筆被修改或
    刪除都會被抓」——**那句話是錯的**，是做負向驗證時才發現的。

    釘住它是為了不讓宣稱被放大回去。要真的抓到需要外部錨點（簽章的筆數、
    或時間戳服務），那是層級 2／3。
    """
    lines = exported[1][:3]

    code, output = _run(_tampered(exported, "truncated", lines))

    assert code == 0, "如果這條開始紅了，代表層級 2 做了——去改 ROADMAP 那張表"
    assert "3 筆通過驗證" in output
    # 而且它不會假裝知道原本有幾筆。
    assert f"{ENTRIES} 筆" not in output


# --- 不是紀錄檔的東西 -------------------------------------------------------


def test_a_missing_file_is_not_a_pass(tmp_path) -> None:
    # 「檔案不存在」讀成通過，會是這支工具最糟的失敗方式。
    code, output = _run(tmp_path / "nope.jsonl")

    assert code != 0
    assert "找不到檔案" in output


def test_it_prints_usage_with_no_argument() -> None:
    if not PYTHON.is_file():  # pragma: no cover
        pytest.skip("找不到 venv 的 python")
    proc = subprocess.run(
        [str(PYTHON), str(TOOL)],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )

    assert proc.returncode == 2
    assert "verify_decision_log.py" in proc.stdout
