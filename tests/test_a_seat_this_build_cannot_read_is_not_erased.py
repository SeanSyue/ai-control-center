"""一格讀不出來的位子**不會在使用者不知情的時候從硬碟上消失**，
而且它消失之前，沒有人會拿到它的權限。

使用者會看到的原始症狀：執行跑完，狀態是完成／可以拍板。畫面上的節點數
少一個，而**沒有任何訊息說少了什麼、為什麼少**——讀起來就像他自己排的陣容
本來就沒有那一格。更下面一層還有兩件事跟著走：

* 「該有哪些章」是從**同一份被砍過的陣容**算出來的，所以那一格的簽章要求
  跟著消失，拍板檢查說「一個都不缺」。
* 驗簽章的那一格是「最後一格」推出來的。真正的最後一格被讀掉之後，這個
  位置會往前掉到**做事的那一格**——做事的人驗自己的鏈。訂驗收標準的那一格
  同理（「第一個不做事的節點」）。

會走到這裡的路是匯入別台機器的專案、手改 `.aicc/sessions.json`、或者另一個
版本的 build 寫的檔案（例如 `VALID_PROVIDERS` 多一家而版本號沒跳）。

## 這幾條擋不住什麼

* **擋不住「拍板檢查照樣說 READY」。**要讓它失敗關閉得動
  `app/services/approval_chain.py` 與 `app/core/run_stamps.py`，兩個都不在
  這一組手上。這裡只把「不該由誰來」那兩條路封掉（回空字串），
  沒有新增一種判定。
* **擋不住「畫面上沒說少了一格」。**那要動頁面原始檔與 `app/i18n.py`。
* **擋不住連名字都讀不出來的那一種。**沒有名字的一筆不是使用者排的位子，
  照舊丟。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.core.lineup import (
    AgentSlot,
    Lineup,
    chain_checker,
    criteria_seat,
)
from app.services.session_registry import SessionRegistry
from app.services.workspace_portability import WorkspacePortabilityService

# 一格「使用者真的排過、而這一版讀不出來」的位子。`gemini` 就是
# `VALID_PROVIDERS` 多一家而 `FORMAT_VERSION` 沒跟著跳的樣子。
UNREADABLE_SEAT = {
    "role": "複查",
    "provider": "gemini",
    "session_id": "cx-OLD",
    "duty": "review",
    "identity": "很嚴格的審查員",
}


def _seed_sessions(project: Path) -> Path:
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


def _roles_on_disk(path: Path) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [str(item.get("role", "")) for item in raw["lineup"]]


def test_a_seat_this_build_cannot_read_is_still_in_the_file_after_a_save(
    tmp_path: Path,
) -> None:
    """他在陣容頁改任何一格，另一格**沒有**跟著從檔案裡消失。

    弄壞的樣子：`Lineup.from_list` 把讀不出來的一筆丟掉，`to_list()` 寫回去
    的就是砍過的那一份。而「讀進來再存回去」是每一條路都會走的一趟——綁一段
    對話、改一個名字、換一個職責。使用者按下去，畫面說改好了，而檔案裡少了
    一格。
    """
    project = tmp_path / "project"
    path = _seed_sessions(project)
    registry = SessionRegistry(project)
    assert _roles_on_disk(path) == ["執行", "複查", "核准"], "前提壞了"

    registry.save_lineup(registry.load_lineup().with_session("執行", "sess-NEW"))

    assert _roles_on_disk(path) == ["執行", "複查", "核准"], (
        f"存一次少一格：{_roles_on_disk(path)}"
    )
    kept = json.loads(path.read_text(encoding="utf-8"))["lineup"][1]
    assert kept == UNREADABLE_SEAT, f"被照這一版的形狀重寫過了：{kept}"


def test_a_line_up_nobody_can_read_is_not_replaced_by_the_factory_pair(
    tmp_path: Path,
) -> None:
    """整份陣容都讀不出來的時候，檔案裡**不會變成出廠的那兩格**。

    這是最貴的那一種：讀完之後 `slots` 是空的，而
    `SessionRegistry._lineup_from_raw` 看到空的就改用出廠的兩人陣容，
    下一次存檔把它寫回去。使用者排的每一格在那一刻從硬碟上消失，
    畫面上是一份看起來很正常的預設陣容——**看不出任何東西壞掉**。
    """
    project = tmp_path / "project"
    path = project / ".aicc" / "sessions.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "lineup": [dict(UNREADABLE_SEAT), {"role": "細剪", "provider": "gemini"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    registry = SessionRegistry(project)

    registry.save_lineup(registry.load_lineup())

    assert _roles_on_disk(path) == ["複查", "細剪"], (
        f"整份被換成出廠陣容了：{_roles_on_disk(path)}"
    )


def test_carrying_the_project_to_a_new_machine_keeps_the_seat_it_cannot_read(
    tmp_path: Path,
) -> None:
    """搬到新電腦之後，那一格還在——而舊機的對話 id 走了。

    匯入是**唯一一條它自己就講明「資料來自別台機器」的路**，也就是最可能
    遇到「這一版讀不出來的一格」的地方。修之前它是 `load_lineup()` →
    `save()` 一趟，那一格不是被忽略，是被永久刪掉，而畫面上寫的是「匯入
    成功」。
    """
    project = tmp_path / "project"
    path = _seed_sessions(project)

    WorkspacePortabilityService._drop_machine_bound_sessions(project)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert _roles_on_disk(path) == ["執行", "複查", "核准"], (
        f"搬家把讀不出來的那一格刪掉了：{_roles_on_disk(path)}"
    )
    # 那一格的**其他欄位**也要原樣留著，只有 session id 走。
    assert raw["lineup"][1]["identity"] == "很嚴格的審查員"
    assert [item.get("session_id", "") for item in raw["lineup"]] == ["", "", ""], raw
    assert "sess-OLD" not in path.read_text(encoding="utf-8")
    assert "cx-OLD" not in path.read_text(encoding="utf-8")
    assert raw["selected"] == {"claude": "", "codex": ""}
    assert Path(raw["project_path"]) == project.resolve(), raw["project_path"]


def test_the_chain_is_not_certified_by_whoever_happens_to_be_last(
    tmp_path: Path,
) -> None:
    """有一格讀不出來的時候，**不會有人被自動指派去驗這條鏈**。

    「最後一格一定要驗簽章」那條規定的前提是「這份陣容就是他排的那一份」。
    有一格被讀掉之後前提不成立：實測過，這個位置會往前掉到做事的那一格，
    於是變成做事的人驗自己的鏈——而畫面上，那是一次正常的完成。
    """
    lineup = Lineup.from_list(
        [
            {"role": "執行", "provider": "claude", "duty": "work"},
            dict(UNREADABLE_SEAT),
        ]
    )
    assert lineup.has_unreadable, "前提壞了：這份陣容應該有一格讀不出來"

    assert chain_checker(lineup) == "", (
        "把驗簽章交給了做事的那一格"
    )
    # 反面：使用者**明著排了**核准那一格的話，那是他親手指定的，照用。
    seated = Lineup.from_list(
        [
            {"role": "執行", "provider": "claude", "duty": "work"},
            dict(UNREADABLE_SEAT),
            {"role": "核准", "provider": "claude", "duty": "approve"},
        ]
    )
    assert chain_checker(seated) == "核准"


def test_the_bar_is_not_set_by_a_seat_the_user_never_chose(tmp_path: Path) -> None:
    """有一格讀不出來的時候，**訂驗收標準的權限不會換人**。

    「第一個不做事的節點」這句話的答案可能就是那一格。跳過它往下挑，等於把
    「怎樣算做完」交給一個使用者從來沒有排成那個角色的人，而畫面上看不出來
    換過人。
    """
    lineup = Lineup.from_list(
        [
            {"role": "發想", "provider": "codex", "duty": "plan"},
            {"role": "執行", "provider": "claude", "duty": "work"},
        ]
    )
    assert criteria_seat(lineup) == "發想", "前提壞了：乾淨的陣容應該挑得到人"

    damaged = Lineup.from_list(
        [
            dict(UNREADABLE_SEAT, role="發想", duty="plan"),
            {"role": "執行", "provider": "claude", "duty": "work"},
            {"role": "核准", "provider": "claude", "duty": "approve"},
        ]
    )
    assert damaged.has_unreadable, "前提壞了"
    assert criteria_seat(damaged) == "", "訂標準的權限被交給了別人"


def test_moving_a_seat_on_the_page_does_not_delete_the_one_it_cannot_draw(
    tmp_path, monkeypatch
) -> None:
    """他在陣容頁把一格往上搬，看不見的那一格**沒有跟著被刪掉**。

    `Lineup.of()` 與 `apply_preset()` 是「從零組一份新的」，所以它們回來的
    東西上面沒有那幾格——而下一步就寫回硬碟了。搬一格位子、套一個模式，
    都會在那一刻把它從檔案裡刪掉，而畫面上什麼都不會說。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.ui.lineup_web_panel import LineupWebPanel

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    path = _seed_sessions(project)
    QApplication.instance() or QApplication([])
    panel = LineupWebPanel(project)
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    assert panel._lineup.has_unreadable, "前提壞了：面板應該知道有一格讀不出來"

    panel.handle_action("move_slot", "1:-1")

    assert _roles_on_disk(path) == ["執行", "複查", "核准"] or set(
        _roles_on_disk(path)
    ) == {"執行", "複查", "核准"}, f"搬一格把另一格刪掉了：{_roles_on_disk(path)}"


def test_a_seat_with_no_name_at_all_is_still_thrown_away() -> None:
    """一筆連名字都沒有的資料**照舊丟掉**，不算「使用者失去了什麼」。

    這條是上面每一條的邊界：全部留著的話，一個空的 `{}` 或一段亂碼就能讓
    這個專案的陣容永遠處在「有一格讀不出來」的狀態，而上面那兩條會因此
    **永遠**不指派驗簽章與訂標準的人——一條沒有出口的路，比原本的問題更糟。
    """
    lineup = Lineup.from_list(
        [
            {"role": "執行", "provider": "claude", "duty": "work"},
            {"role": "", "provider": "codex"},
            "根本不是一筆位子",
            {},
        ]
    )

    assert [slot.role for slot in lineup] == ["執行"]
    assert lineup.unreadable == (), lineup.unreadable
    assert chain_checker(lineup) == "執行"


def test_the_engine_still_only_sees_seats_it_can_run() -> None:
    """引擎拿到的還是**只有讀得出來的那幾格**。

    留住那一筆不等於把它交給引擎跑：它的 provider 就是這一版啟動不了的東西。
    這條守的是「留痕」沒有變成「派一個跑不動的位子出去」。
    """
    lineup = Lineup.from_list(
        [
            {"role": "執行", "provider": "claude", "duty": "work"},
            dict(UNREADABLE_SEAT),
        ]
    )

    assert len(lineup) == 1
    assert [slot.role for slot in lineup] == ["執行"]
    assert lineup.roles == ("執行",)
    assert lineup.for_role("複查") is None
    # 但是「這裡有沒有陣容」要回答有——空的話上層會改用出廠的兩人陣容。
    assert bool(lineup) is True


def test_every_copy_of_a_line_up_carries_the_seat_it_cannot_read() -> None:
    """改任何一格之後，看不見的那一格**還在那份陣容上**。

    `Lineup` 的每一支複製方法都是「重建一個新的 Lineup」，而漏掉一支的症狀
    是無聲的：那一支被呼叫到的那條路上，檔案少一格。這個檔案 2026-08-22
    已經因為同一個形狀（`duty` 每次設定 session 就掉一次）在三個地方各中過
    一次。

    **這條擋不住新增一支忘了帶。**它只認得今天有的這幾支。
    """
    lineup = Lineup.from_list(
        [
            {"role": "執行", "provider": "claude", "duty": "work"},
            dict(UNREADABLE_SEAT),
        ]
    )
    lost = lineup.unreadable
    assert lost, "前提壞了"

    copies = {
        "with_session": lineup.with_session("執行", "x"),
        "without_sessions": lineup.without_sessions(),
        "with_identity": lineup.with_identity("執行", "資深後端"),
        "with_planner": lineup.with_planner(),
        "without_planner": lineup.with_planner().without_planner(),
        "with_node": lineup.with_node("上字幕", "claude", "review"),
        "renamed": lineup.renamed("執行", "粗剪"),
        "with_duty": lineup.with_duty("執行", "review"),
        "without_node": lineup.without_node("執行"),
        "moved": lineup.moved("執行", 0),
    }
    dropped = [name for name, copy in copies.items() if copy.unreadable != lost]
    assert not dropped, f"這幾支把讀不出來的那一格弄丟了：{dropped}"


def test_a_stored_slot_that_only_this_build_cannot_read_says_which_column(
    tmp_path: Path,
) -> None:
    """讀不出來的時候，記下來的是**哪一欄**讀不懂，不是一句「壞了」。

    沒有這個，下一個接畫面的人只能寫「有一格讀不出來」——而使用者要能做的
    事（換一個廠牌？升級 AICC？）完全取決於是哪一欄。

    存的是英文代號不是中文：這個值會被拿去查該顯示哪一句話，中文當鍵一翻譯
    就對不上。
    """
    lineup = Lineup.from_list(
        [
            {"role": "執行", "provider": "claude", "duty": "work"},
            dict(UNREADABLE_SEAT),
            {"role": "上字幕", "provider": "claude", "duty": "剪片"},
        ]
    )

    assert [(u.role, u.field) for u in lineup.unreadable] == [
        ("複查", "provider"),
        ("上字幕", "duty"),
    ], lineup.unreadable


def test_a_clean_line_up_still_names_its_checker_and_its_bar_setter() -> None:
    """一份讀得完的陣容，該指派的還是照樣指派得出來。

    上面那幾條的反面。少了這一條，「一律回空字串」也會是綠的——而那會讓
    **每一次**執行都沒有人驗簽章、沒有人訂驗收標準。
    """
    lineup = Lineup.of(
        [
            AgentSlot("發想", "codex", duty="plan"),
            AgentSlot("執行", "claude", duty="work"),
            AgentSlot("複查", "codex", duty="review"),
        ]
    )

    assert criteria_seat(lineup) == "發想"
    assert chain_checker(lineup) == "複查"
