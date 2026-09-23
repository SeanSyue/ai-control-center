"""稽核匯出要講出這條鏈從哪裡開始（使用者 2026-08-16 定案）。

這份檔案的用途是拿給別人看。它原本會說「N 筆通過驗證」——真的、可驗證、
**而且不會說它只涵蓋一段時間**。收到的人沒有理由懷疑手上這份不是全部。

起因是使用者自己的專案：舊機的 AICC 資料在 2026-08-07 搬家之後被刪光了，
那之前的決策紀錄永遠不存在。而 `handover.py` 裡那句「去舊機匯出一份收好」
現在沒有對象。

既有的「不能證明」段落已經誠實講了**從結尾**砍掉不會留痕跡。缺的是**起點**
——同一個形狀，另一頭。

兩句話，刻意分開：

1. **「涵蓋期間：X 起」**：每一份匯出都該有的界線，讀第一筆的時間算出來。
   **不寫死日期**——寫死的話那句話只對這一台機器是真的。
2. **「舊機另有 N 筆」**：只有搬過來的專案講得出來，因為那個數字只有舊機
   知道（`handover.json` 的 `decision_entries`）。不成立時不要硬講：一句
   講不出數字的警告，讀的人只會學會忽略它。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.decision_export import DecisionExportService
from app.services.long_task import DecisionLogEntry, DecisionLogStore
from app.services.project_registry import ProjectRegistry


def _project(root: Path, name: str = "案子", stamps: tuple[str, ...] = ()) -> Path:
    project = root / name
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("整理開戶文件\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)
    store = DecisionLogStore(project)
    for index, created_at in enumerate(stamps):
        store.append(
            DecisionLogEntry(
                run_id=f"run-{index}", action="ANSWERED",
                question=f"問題 {index}", answer="可以",
                answered_by="user", reason="測試", created_at=created_at,
            )
        )
    return project


def _markdown(root: Path, project: Path, out_name: str = "out") -> str:
    out = root / out_name
    out.mkdir(exist_ok=True)
    result = DecisionExportService(project).export(out)
    return Path(result.markdown_path).read_text(encoding="utf-8")


# --- 這條鏈從哪裡開始 --------------------------------------------------------


@pytest.mark.parametrize(
    "first, shown",
    [
        ("2026-08-07T09:30:00+08:00", "2026-08-07 09:30"),
        ("2026-03-01T22:05:00+08:00", "2026-03-01 22:05"),
    ],
)
def test_it_says_when_the_chain_starts(tmp_path: Path, first: str, shown: str) -> None:
    """**這一組存在的理由。**

    兩組時間都驗，是因為只驗一個值的話，一個寫死的日期也會通過——而寫死的
    日期正是這裡最該防的那種錯（它只對寫它的那台機器是真的）。
    """
    project = _project(
        tmp_path, name=f"案子-{shown[:10]}", stamps=(first, "2026-08-15T10:00:00+08:00")
    )

    text = _markdown(tmp_path, project, out_name=f"out-{shown[:10]}")

    assert f"- 涵蓋期間：{shown} 起" in text, text[:400]


def test_it_reads_the_first_entry_not_the_last(tmp_path: Path) -> None:
    # 拿最後一筆的話，那句話會說「涵蓋期間：今天起」——比不講更糟。
    project = _project(
        tmp_path,
        stamps=("2026-08-07T09:30:00+08:00", "2026-08-15T23:59:00+08:00"),
    )

    text = _markdown(tmp_path, project)

    assert "2026-08-07 09:30 起" in text
    assert "2026-08-15 23:59 起" not in text


def test_an_empty_log_says_nothing_about_coverage(tmp_path: Path) -> None:
    """一筆都沒有的時候，「涵蓋期間」沒有可以講的內容。

    比對的是**開頭那一行欄位**，不是那四個字：說明段落裡也有「涵蓋」二字，
    而那一段是每份匯出都在的。第一版這條寫鬆了，紅在說明段落上——順帶抓到
    文案裡一句指著不存在欄位的話（「上面那個『涵蓋期間』」），已改掉。
    """
    project = _project(tmp_path, stamps=())

    text = _markdown(tmp_path, project)

    assert "- 涵蓋期間：" not in text
    # 前提檢查：匯出本身還是成功的，不是整份都沒產生。
    assert "# 決策紀錄" in text
    # 而說明段落不該指著一個不存在的欄位。
    assert "上面那個" not in text


def test_the_limits_section_says_the_history_may_be_cut_at_the_front(
    tmp_path: Path,
) -> None:
    """既有的「不能證明」講了結尾，這裡補上開頭。

    只有 header 那一行數字的話，讀的人會把它當成「這個專案就是這時候開始的」，
    而不是「這條鏈就是這時候開始的」——那兩件事在換過電腦之後不一樣。
    """
    project = _project(tmp_path, stamps=("2026-08-07T09:30:00+08:00",))

    text = _markdown(tmp_path, project)

    limits = text.split("## 這份紀錄能證明什麼", 1)[1].split("---", 1)[0]
    assert "涵蓋整個專案的歷史" in limits
    assert "舊機" in limits


# --- 搬過來的專案：舊機還有幾筆 ----------------------------------------------


@pytest.mark.parametrize("left_behind", [1, 47])
def test_a_moved_project_says_how_many_stayed_behind(
    tmp_path: Path, left_behind: int
) -> None:
    project = _project(
        tmp_path, name=f"搬過來-{left_behind}", stamps=("2026-08-07T09:30:00+08:00",)
    )
    (project / ".aicc" / "handover.json").write_text(
        json.dumps({"decision_entries": left_behind}), encoding="utf-8"
    )

    text = _markdown(tmp_path, project, out_name=f"out-{left_behind}")

    assert f"舊機另有 {left_behind} 筆" in text
    # 光說「少了東西」沒有下一步，讀的人只會知道自己少了東西。
    assert "在舊機上另外匯出一份" in text


def test_a_project_that_did_not_move_says_nothing_about_an_old_machine(
    tmp_path: Path,
) -> None:
    """前提檢查，也是這條規則的另一半。

    對沒搬過的專案硬講一句「舊機還有 N 筆」，會讓每一份匯出都帶一句不成立的
    警告——而那種警告會連帶把旁邊真的重要的那條一起訓練成雜訊。
    """
    project = _project(tmp_path, stamps=("2026-08-07T09:30:00+08:00",))

    text = _markdown(tmp_path, project)

    assert "舊機另有" not in text
    # 但「這條鏈有起點」那句對每個專案都成立，不該一起消失。
    assert "涵蓋期間" in text


@pytest.mark.parametrize(
    "marker",
    [{"decision_entries": 0}, {"had_git": True}, {"decision_entries": "很多"}],
)
def test_a_marker_without_a_usable_count_says_nothing(
    tmp_path: Path, marker: dict
) -> None:
    """0 筆、缺欄位、或欄位不是數字，三種都不該生出一條沒有內容的提醒。

    第三種是舊版壓縮檔或手改壞的檔案：這條釘住它不會炸，也不會印出
    「舊機另有 很多 筆」。
    """
    project = _project(
        tmp_path, name=f"marker-{len(marker)}-{marker.get('decision_entries')}",
        stamps=("2026-08-07T09:30:00+08:00",),
    )
    (project / ".aicc" / "handover.json").write_text(
        json.dumps(marker, ensure_ascii=False), encoding="utf-8"
    )

    text = _markdown(
        tmp_path, project, out_name=f"o-{len(marker)}-{marker.get('decision_entries')}"
    )

    assert "舊機另有" not in text
