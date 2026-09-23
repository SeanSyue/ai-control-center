"""陣容頁上**每一個**改動之後，那一格讀不出來的位子還在檔案裡。

使用者會看到的原始症狀：他在陣容頁把第一格接上一段對話（五節點測試的第一步
就是這個），畫面說改好了。而 `.aicc/sessions.json` 裡他排過的第二格
——這一版讀不出來的那一格——在那一刻被刪掉了，**沒有任何一句話說少了什麼**。
再打開陣容頁，那一格就是不存在了；他會以為自己記錯。

## 為什麼是「每一個動作」而不是「那兩個」

2026-08-24 之前，「把讀不出來的那幾格帶回去」是每一支改陣容的動作**自己記得
叫一支 helper**。那個形狀當場漏了兩支：

    move_slot          → 有叫       ✓
    apply_preset       → 有叫       ✓
    set_slot_session   → 沒有叫     ✗ ← 最常按的那一個
    set_slot_provider  → 沒有叫     ✗

漏掉的症狀是無聲的：測試全綠、畫面正常、檔案少一格。所以修法不是「再叫兩次
helper」，是把那件事搬到**唯一一個寫檔的地方**（`_persist()`），讓沒有一支
動作有機會忘記。這一支測試照著那個形狀寫：**列舉頁面送得出來的每一種改動**，
一個一個按過去。

## 這幾條擋不住什麼

* **擋不住「畫面上沒說少了一格」。**那一格還在硬碟上，但陣容頁仍然少畫一格
  而沒有一句話說為什麼。要補那句話得動頁面原始檔（`AICC-視覺概念/`）與
  `app/i18n.py`，兩者都不在這一組手上。
* **擋不住「拍板檢查照樣說 READY」。**「該有哪些章」是從**讀得出來的那幾格**
  算的（`run_stamps.requires_for`），所以那一格的簽章要求還是消失的。要讓它
  失敗關閉得動 `app/services/approval_chain.py` 與 `app/core/run_stamps.py`。
* **擋不住新增一種頁面動作忘了走 `_persist()`。**它只認得今天 `handle_action`
  接得住的這幾種。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

# 一格「使用者真的排過、而這一版讀不出來」的位子。`gemini` 就是
# `VALID_PROVIDERS` 多一家而 `FORMAT_VERSION` 沒跟著跳的樣子。
UNREADABLE_SEAT = {
    "role": "複查",
    "provider": "gemini",
    "session_id": "cx-OLD",
    "duty": "review",
    "identity": "很嚴格的審查員",
}

# 頁面送得出來的每一種「會改到陣容並寫回硬碟」的動作，以及它的參數。
#
# **清單寫在這裡而不是散在各條測試裡**，因為漏掉一種的代價就是原本那個 bug：
# 一支沒有被走到的動作，看起來跟一支被走到而且沒事的動作一模一樣。
WRITING_ACTIONS = [
    ("set_slot_session", "0:sess-NEW"),
    ("set_slot_provider", "1:codex"),
    ("set_slot_identity", "0:很資深的後端"),
    ("set_slot_role", "0:粗剪"),
    ("set_slot_duty", "1:review"),
    ("move_slot", "1:-1"),
    ("add_slot", ""),
    ("remove_slot", "1"),
]


def _seed(project: Path) -> Path:
    """一份三格的 sessions.json，中間那一格這一版讀不出來。"""
    path = project / ".aicc" / "sessions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "project_path": str(project),
                "lineup": [
                    {"role": "執行", "provider": "claude", "session_id": "sess-OLD",
                     "duty": "work"},
                    dict(UNREADABLE_SEAT),
                    {"role": "核准", "provider": "claude", "duty": "approve"},
                ],
                "selected": {"claude": "sess-OLD", "codex": ""},
                "sessions": {"claude": [], "codex": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _panel(project: Path):
    from PySide6.QtWidgets import QApplication

    from app.ui.lineup_web_panel import LineupWebPanel

    QApplication.instance() or QApplication([])
    panel = LineupWebPanel(project)
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    return panel


def _seats_on_disk(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["lineup"]


@pytest.mark.parametrize("kind,arg", WRITING_ACTIONS)
def test_no_action_on_the_line_up_page_deletes_the_seat_it_cannot_draw(
    tmp_path: Path, monkeypatch, kind: str, arg: str
) -> None:
    """陣容頁上按任何一個東西，看不見的那一格**還在檔案裡，而且沒被改寫**。

    弄壞的樣子：那一支動作用 `Lineup.of(slots)` 從零重建一份陣容，接著
    `_persist()` 把它寫回硬碟。使用者排過的那一格在那一刻從
    `.aicc/sessions.json` 上消失——**畫面說改好了，而且改的確實改了**，
    只是同時少了一格他沒有動過的東西。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = tmp_path / "project"
    path = _seed(project)
    panel = _panel(project)
    assert panel._lineup.has_unreadable, "前提壞了：面板應該知道有一格讀不出來"

    panel.handle_action(kind, arg)

    kept = [seat for seat in _seats_on_disk(path) if seat.get("provider") == "gemini"]
    assert kept, (
        f"「{kind}」把讀不出來的那一格從硬碟上刪掉了："
        f"{[s.get('role') for s in _seats_on_disk(path)]}"
    )
    assert kept[0] == UNREADABLE_SEAT, (
        f"「{kind}」之後那一格被照這一版的形狀重寫過了：{kept[0]}"
    )


def test_switching_projects_does_not_carry_a_seat_into_the_wrong_file(
    tmp_path: Path, monkeypatch
) -> None:
    """換一個專案之後，上一個專案讀不出來的那一格**不會被寫進這一個**。

    這是把「讀不出來的那幾格」記在面板上（而不是每次從陣容現算）換來的風險，
    所以要有一條守著。弄壞的樣子：換專案時忘了跟著換，於是使用者打開 B 專案、
    動一格，B 的 `sessions.json` 裡多出一格他從來沒有排過的位子——而那一格
    這一版還畫不出來，他連看都看不到它。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    dirty = tmp_path / "dirty"
    _seed(dirty)
    clean = tmp_path / "clean"
    clean_path = clean / ".aicc" / "sessions.json"
    clean_path.parent.mkdir(parents=True)
    clean_path.write_text(
        json.dumps(
            {
                "version": 1,
                "project_path": str(clean),
                "lineup": [
                    {"role": "執行", "provider": "claude", "duty": "work"},
                    {"role": "複查", "provider": "codex", "duty": "review"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    panel = _panel(dirty)
    assert panel._lineup.has_unreadable, "前提壞了"

    panel.set_project(clean)
    panel.handle_action("set_slot_session", "0:sess-NEW")

    seats = _seats_on_disk(clean_path)
    assert [s["role"] for s in seats] == ["執行", "複查"], (
        f"上一個專案的位子被寫進來了：{seats}"
    )
    assert all(s.get("provider") != "gemini" for s in seats), seats


def test_a_clean_project_still_saves_what_the_user_changed(
    tmp_path: Path, monkeypatch
) -> None:
    """一份讀得完的陣容，改動照樣存得進去。

    上面那幾條的反面。少了這一條，「`_persist()` 什麼都不寫」也會是綠的
    ——而那會讓陣容頁**每一顆按鈕都沒有作用**，同時每一條都通過。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = tmp_path / "project"
    path = project / ".aicc" / "sessions.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "project_path": str(project),
                "lineup": [
                    {"role": "執行", "provider": "claude", "duty": "work"},
                    {"role": "複查", "provider": "codex", "duty": "review"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    panel = _panel(project)
    assert not panel._lineup.has_unreadable, "前提壞了：這一份應該全部讀得出來"

    panel.handle_action("set_slot_session", "0:sess-NEW")

    seats = _seats_on_disk(path)
    assert [s["role"] for s in seats] == ["執行", "複查"], seats
    assert seats[0]["session_id"] == "sess-NEW", f"他綁的那一段沒有存進去：{seats[0]}"
