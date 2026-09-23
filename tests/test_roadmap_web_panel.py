"""The ROADMAP.md parser behind the Roadmap panel.

These are the cases that were actually wrong during development, not invented
ones: a header variant that silently dropped a whole list, a section boundary
that let one phase swallow the rest of the file, and a scoring rule that marked
every phase the same.
"""
from __future__ import annotations

from pathlib import Path

from app.ui.roadmap_web_panel import ROADMAP, parse_roadmap

SAMPLE = """# Roadmap

## P1 - 文件管理

狀態：第一版完成，已加入匯出前預覽。

已完成：

- 匯入 PDF
  - 支援分頁
- 匯出預覽

下一步：

- 支援 OCR

## P6 - Windows 產品化

狀態：安裝程式基礎建設已完成（2026-08-05）；更大的範圍仍未開始。

已完成（2026-08-05）：

- PyInstaller 打包

還沒做（P6 剩餘範圍）：

- Update wizard
- 自訂圖示

## 版本計畫

### v0.9.x

- 這一段不屬於任何 phase
"""


def test_prefix_matched_headers_keep_the_dated_completed_list() -> None:
    # "已完成（2026-08-05）：" was skipped by an exact-match header regex, which
    # silently emptied P6's shipped work.
    phases = {p["id"]: p for p in parse_roadmap(SAMPLE)}

    done = [i for i in phases["P6"]["items"] if i["done"] and i["title"] != "狀態"]
    todo = [i for i in phases["P6"]["items"] if not i["done"]]

    assert [i["title"] for i in done] == ["PyInstaller 打包"]
    assert [i["title"] for i in todo] == ["Update wizard", "自訂圖示"]


def test_a_non_phase_section_ends_the_last_phase() -> None:
    # Without this, P9 kept collecting through 版本計畫 and reported 66 items.
    phases = {p["id"]: p for p in parse_roadmap(SAMPLE)}

    assert "這一段不屬於任何 phase" not in [
        i["title"] for i in phases["P6"]["items"]
    ]
    assert set(phases) == {"P1", "P6"}


def test_next_steps_do_not_make_a_finished_phase_unfinished() -> None:
    # Every phase has a 下一步 list; scoring on the lists alone marked all
    # eleven phases "now".
    phases = {p["id"]: p for p in parse_roadmap(SAMPLE)}

    assert phases["P1"]["state"] == "done"
    # P6's prose says 仍未開始, but it has shipped items, so it is under way.
    assert phases["P6"]["state"] == "now"


def test_every_phase_leads_with_the_original_status_sentence() -> None:
    # The state is a heuristic over prose, so the prose must stay visible.
    for phase in parse_roadmap(SAMPLE):
        assert phase["items"][0]["title"] == "狀態"
        assert phase["items"][0]["note"]


def test_nested_bullets_become_a_note_on_their_parent() -> None:
    phases = {p["id"]: p for p in parse_roadmap(SAMPLE)}
    first = next(i for i in phases["P1"]["items"] if i["title"] == "匯入 PDF")

    assert first["note"] == "支援分頁"


def test_the_real_roadmap_parses_into_the_shape_the_page_expects() -> None:
    phases = parse_roadmap(ROADMAP.read_text(encoding="utf-8"))

    assert len(phases) >= 10
    assert {p["state"] for p in phases} <= {"done", "now", "todo"}
    for phase in phases:
        assert phase["id"].startswith("P")
        assert phase["title"]
        assert all({"title", "note", "done"} <= set(i) for i in phase["items"])
