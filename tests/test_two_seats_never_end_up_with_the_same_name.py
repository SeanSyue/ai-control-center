"""`.aicc/sessions.json` 裡不會出現兩格叫同一個名字——**看不見的那一格也算**。

使用者會看到的原始症狀，三種入口同一個病：

* 他在陣容頁把第一格改名叫「複查」。畫面說改好了。而他排過、這一版畫不出來
  的那一格**本來就叫複查**——檔案裡從此有兩格叫複查。
* 他按「＋ 加一格」。新的那一格自動取名 `node2`，而檔案裡讀不出來的那一格
  就叫 `node2`。
* 他的三人陣容因為一格畫不出來而只剩兩格，他以為自己排錯了，重新套用
  【三人】模式——模式裡也有一格叫「核准」，於是檔案裡兩格叫核准。

兩格同名之後會怎樣，這個 repo 自己的訊息早就寫過：**整條線是靠名字指人的**
（派工、駁回、蓋章、失效範圍全都是），「退給核准」從那一刻起有兩個答案。
而畫面上一格都看不出來——他只會看到一次成功的改名。

## 這是「把讀不出來的那一格留下來」換來的新風險

2026-08-24 之前，讀不出來的那一格會被安靜丟掉，所以它不佔名字（代價是使用者
的資料無聲消失，那才是要修的那件事）。留下來之後，「這份陣容長什麼樣」有了
兩個答案：**畫得出來的那幾格** 與 **檔案裡的全部**。每一個問這個問題的地方
都要重新選一次，而選錯的那幾處不會有任何一支測試變紅——這一份就是那幾支。

## 這幾條擋不住什麼

* **擋不住「畫面上沒有一句話說少了一格」。**被擋下來的使用者收到的是
  「已經有一個位子叫『複查』了」，而他的畫面上一格複查都沒有——那句話是真的，
  只是他看不到證據。要講出「有一格這一版讀不出來」得動頁面原始檔
  （`AICC-視覺概念/lineup_v1.html`）與 `app/i18n.py`，兩者都不在這一組手上。
* **擋不住手改檔案。**使用者自己在 `sessions.json` 裡寫兩格同名，這裡一個字
  都不會說。
* **擋不住引擎那一側**：`run_stamps.requires_for` 仍然只看讀得出來的那幾格，
  所以那一格的簽章要求照樣是消失的（見 docs/WHY.md）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from app.i18n import t  # noqa: E402
from app.core.lineup import Lineup, LineupError  # noqa: E402

# 一格「使用者真的排過、而這一版讀不出來」的位子。`gemini` 就是
# `VALID_PROVIDERS` 多一家而版本號沒跟著跳的樣子——名字讀得出來，只有廠牌
# 這一欄不認得。
INVISIBLE_REVIEWER = {
    "role": t("role.reviewer"),
    "provider": "gemini",
    "session_id": "cx-OLD",
    "duty": "review",
}


def _write(project: Path, seats: list[dict]) -> Path:
    path = project / ".aicc" / "sessions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "project_path": str(project),
                "lineup": seats,
                "selected": {"claude": "", "codex": ""},
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


def _roles_on_disk(path: Path) -> list[str]:
    seats = json.loads(path.read_text(encoding="utf-8"))["lineup"]
    return [str(seat.get("role", "")) for seat in seats]


def _said(panel) -> list[str]:
    """面板拒絕時講的那幾句話。空的代表它什麼都沒說。"""
    heard: list[str] = []
    panel.action_failed.connect(heard.append)
    return heard


# ---- 使用者自己打的名字 -------------------------------------------------
def test_renaming_a_seat_onto_the_name_of_a_seat_the_page_cannot_draw_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """他把第一格改名叫「複查」，而檔案裡已經有一格叫複查——**擋下來並講出來**。

    弄壞的樣子：改名成功，畫面說改好了，`.aicc/sessions.json` 裡從此有兩格
    叫「複查」。下一次有人說「退給複查」，那句話有兩個答案，而使用者從頭到尾
    只看到一次成功的改名。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = tmp_path / "project"
    path = _write(
        project,
        [
            {"role": t("role.worker"), "provider": "claude", "duty": "work"},
            dict(INVISIBLE_REVIEWER),
        ],
    )
    panel = _panel(project)
    assert panel._lineup.has_unreadable, "前提壞了：面板應該知道有一格讀不出來"
    heard = _said(panel)

    panel.handle_action("set_slot_role", "0:" + t("role.reviewer"))

    roles = _roles_on_disk(path)
    assert len(roles) == len(set(roles)), f"檔案裡出現兩格同名的位子：{roles}"
    assert roles == [t("role.worker"), t("role.reviewer")], f"陣容被改掉了：{roles}"
    assert heard, "擋下來了卻沒有講——按了沒反應跟改好了長得一模一樣"
    assert t("role.reviewer") in heard[0], heard


def test_a_seat_can_still_be_renamed_to_a_name_nobody_uses(
    tmp_path: Path, monkeypatch
) -> None:
    """前提檢查：**改名這件事本身還是做得到的。**

    少了這一條，「`renamed()` 一律拒絕」也會讓上面那條通過——而那會讓陣容頁
    的改名框變成一個按下去只會跳警告的東西。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = tmp_path / "project"
    path = _write(
        project,
        [
            {"role": t("role.worker"), "provider": "claude", "duty": "work"},
            dict(INVISIBLE_REVIEWER),
        ],
    )
    panel = _panel(project)
    heard = _said(panel)

    panel.handle_action("set_slot_role", "0:粗剪")

    assert _roles_on_disk(path) == ["粗剪", t("role.reviewer")], _roles_on_disk(path)
    assert not heard, f"改一個沒有人用的名字不該被拒絕：{heard}"


# ---- 自動編號的名字 -----------------------------------------------------
def test_the_add_seat_button_does_not_reuse_a_number_the_file_already_uses(
    tmp_path: Path, monkeypatch
) -> None:
    """他按「＋ 加一格」，而檔案裡讀不出來的那一格剛好就叫 `node2`。

    弄壞的樣子有兩種，兩種畫面上都說不出話：自動編號只數畫得出來的那幾格，
    於是新的一格取名 `node2`——檔案裡兩格叫 node2（名字檢查也放水的話），
    或者使用者按下加一格之後收到一句「已經有一個位子叫 node2 了」，
    **而他的畫面上一格 node2 都沒有**。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = tmp_path / "project"
    path = _write(
        project,
        [
            {"role": t("role.worker"), "provider": "claude", "duty": "work"},
            {"role": "node2", "provider": "gemini", "duty": "review"},
        ],
    )
    panel = _panel(project)
    assert panel._lineup.has_unreadable, "前提壞了"
    heard = _said(panel)

    panel.handle_action("add_slot", "")

    roles = _roles_on_disk(path)
    assert len(roles) == len(set(roles)), f"檔案裡出現兩格同名的位子：{roles}"
    assert len(roles) == 3, f"那一格根本沒有加進去（{heard}）：{roles}"
    assert not heard, f"加一格不該失敗：{heard}"


# ---- 套用一個模式 -------------------------------------------------------
def test_applying_a_mode_does_not_put_a_second_seat_with_the_same_name_in_the_file(
    tmp_path: Path, monkeypatch
) -> None:
    """三人陣容少畫了一格，他重新套用【兩人】模式——**不准因此多一格複查**。

    弄壞的樣子：套用回報成功，`.aicc/sessions.json` 裡有 執行、複查（畫不出
    來的那一格）、複查（模式帶進來的那一格）。畫面上看起來乾乾淨淨兩格，
    而檔案裡「複查」指的是兩個不同的東西。
    """
    from app.i18n import t

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = tmp_path / "project"
    path = _write(
        project,
        [
            {"role": t("role.worker"), "provider": "claude", "duty": "work"},
            dict(INVISIBLE_REVIEWER),
        ],
    )
    panel = _panel(project)
    assert panel._lineup.has_unreadable, "前提壞了"
    heard = _said(panel)

    panel.handle_action("apply_preset", t("preset.two.name"))

    roles = _roles_on_disk(path)
    assert len(roles) == len(set(roles)), f"檔案裡出現兩格同名的位子：{roles}"
    assert heard, "擋下來了卻沒有講——他會以為模式套用好了"


def test_a_mode_still_applies_when_nothing_in_the_file_is_unreadable(
    tmp_path: Path, monkeypatch
) -> None:
    """前提檢查：**套用模式這件事本身還是做得到的。**

    少了這一條，「`apply_preset` 一律拒絕」也會讓上面那條通過——而那會讓
    整個模式下拉選單變成裝飾品。
    """
    from app.i18n import t

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = tmp_path / "project"
    path = _write(
        project, [{"role": t("role.worker"), "provider": "claude", "duty": "work"}]
    )
    panel = _panel(project)
    assert not panel._lineup.has_unreadable, "前提壞了"
    heard = _said(panel)

    panel.handle_action("apply_preset", t("preset.two.name"))

    assert _roles_on_disk(path) == [t("role.worker"), t("role.reviewer")], (
        f"模式沒有套用進去（{heard}）：{_roles_on_disk(path)}"
    )
    assert not heard, heard


# ---- 模式下拉選單上的那個名字 -------------------------------------------
def test_the_mode_box_names_no_mode_while_a_seat_is_missing_from_the_page(
    tmp_path: Path, monkeypatch
) -> None:
    """三人陣容少畫一格之後，模式選單**不准說這個專案是「兩人」模式**。

    弄壞的樣子：`active_preset` 是拿畫得出來的那幾格比出來的，而剩下的兩格
    剛好就是出廠【兩人】的形狀。於是下拉選單停在「兩人：執行者 → 複查者」，
    斬釘截鐵地告訴他這個專案是兩人模式——那是假話，而且比「少畫一格」更難
    發現：少一格他會覺得自己記錯，下拉選單上的名字看起來是算出來的事實。
    """
    from app.i18n import t

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = tmp_path / "project"
    _write(
        project,
        [
            {"role": t("role.worker"), "provider": "claude", "duty": "work"},
            {"role": t("role.reviewer"), "provider": "codex", "duty": "review"},
            {"role": t("role.approver"), "provider": "gemini", "duty": "approve"},
        ],
    )
    panel = _panel(project)
    assert panel._lineup.has_unreadable, "前提壞了"

    state = panel.collect_state()["lineup"]

    assert state["active_preset"] == "", (
        "少畫一格的陣容被說成是一個現成的模式："
        f"{state['active_preset']}（畫面上只有 {len(state['slots'])} 格）"
    )


def test_a_line_up_this_build_can_read_whole_still_knows_which_mode_it_is(
    tmp_path: Path, monkeypatch
) -> None:
    """前提檢查：**讀得完的陣容照樣認得出自己是哪一個模式。**

    少了這一條，「`active_preset` 永遠回空字串」也會讓上面那條通過——而那會
    讓模式選單永遠停在「都不符合」，使用者存好的模式從此沒有一個看起來是
    生效的。
    """
    from app.i18n import t

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = tmp_path / "project"
    _write(
        project,
        [
            {"role": t("role.worker"), "provider": "claude", "duty": "work"},
            {"role": t("role.reviewer"), "provider": "codex", "duty": "review"},
        ],
    )
    panel = _panel(project)
    assert not panel._lineup.has_unreadable, "前提壞了"

    state = panel.collect_state()["lineup"]

    assert state["active_preset"] == t("preset.two.name"), state["active_preset"]


# ---- 資料結構那一層 -----------------------------------------------------
def test_the_line_up_counts_a_seat_it_cannot_draw_as_a_name_already_taken() -> None:
    """`Lineup` 自己就要知道「這個名字有人用了」含讀不出來的那幾格。

    上面那幾條走的是陣容頁。這一條守的是資料結構本身——下一個從別的入口
    加位子的人（匯入、精靈、指令列）不會經過那一頁，而他拿到的答案要一樣。
    """
    lineup = Lineup.from_list(
        [
            {"role": t("role.worker"), "provider": "claude", "duty": "work"},
            dict(INVISIBLE_REVIEWER),
        ]
    )
    assert lineup.roles == (t("role.worker"),), lineup.roles
    assert lineup.unreadable_names == (t("role.reviewer"),), lineup.unreadable_names
    assert lineup.taken_names == (t("role.worker"), t("role.reviewer")), lineup.taken_names

    with pytest.raises(LineupError):
        lineup.with_node(t("role.reviewer"), "codex")
    with pytest.raises(LineupError):
        lineup.renamed(t("role.worker"), t("role.reviewer"))

    # 反面：沒有人用的名字照樣加得進去、改得成。
    assert lineup.with_node("粗剪", "codex").roles == (t("role.worker"), "粗剪")
    assert lineup.renamed(t("role.worker"), "粗剪").roles == ("粗剪",)
