r"""會寫檔的步驟要拿到**寫得了檔**的沙箱，唯讀的步驟不准拿到。

## 這一份取代了什麼

`test_every_road_to_a_writing_codex_is_blocked.py`（2026-08-25〜09-01）守的是
「Codex 不准坐在會寫檔的位子上」。老闆 2026-09-01 看到那個拒絕訊息時說：

    > 哪個智障說只有 claude 可以寫的，我之前明明就說都可以，
    > 只是在預設設定的時候說過那個 node 用 codex 剛好只有審閱、
    > 不需要寫資料而已

查完**他是對的**：翻遍全部對話紀錄（他講 codex/claude ＋ 寫／檔案／權限的
142 則）、`DECISIONS.md`、`WHY.md`，沒有一句是他講的。源頭是 `orchestrator`
自己的一段註解——「AICC 今天跑的每個 Codex 步驟都是唯讀的，所以會寫檔的那個
沙箱旗標從來沒被驗證過，猜一個等於用一個沒人選過的權限跑他的 repo」。

**那是一個還沒填的值，不是一條規則。** 而它被寫死成
`WRITING_PROVIDERS = ("claude",)`，散進畫面三道閘門、三個測試檔、一份破壞
配方，還反過來被 `WHY.md` 引用成前提。

2026-09-01 他拍板填 `-s workspace-write`（只能寫專案資料夾裡面，跟 Claude
那一半拿到的權限對等）；`danger-full-access` 明確否決。

## 所以現在要守的是另一件事

拒絕拿掉之後，**新的失敗模式是安靜的**：

| 壞法 | 症狀 |
|---|---|
| 寫檔的步驟拿到 `read-only` | Codex 什麼都寫不出來，而那一格看起來跑完了 |
| 唯讀的步驟拿到 `workspace-write` | 一個只該審閱的位子拿到了寫入權 |

兩種在畫面上都完全正常——**沒有紅字、沒有例外、沒有 traceback**。
所以這一份量的是真的送進 CLI 的那個 `-s` 值。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.lineup import (  # noqa: E402
    DUTY_REVIEW,
    DUTY_WORK,
    WRITES_OFF,
    AgentSlot,
    Lineup,
)
from app.core.orchestrator import Orchestrator  # noqa: E402


def _orchestrator(tmp_path: Path, monkeypatch, *, duty: str) -> tuple[Orchestrator, list]:
    """一個 Codex 坐在 `duty` 那一格的引擎，並攔下真正的命令列。"""
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    # `Orchestrator.__init__` 要求任務檔存在——它是這一輪的題目。
    (project / ".aicc" / "task.md").write_text("題目。\n", encoding="utf-8")

    lineup = Lineup.of(
        [
            AgentSlot(role="author", provider="codex", duty=duty),
            AgentSlot(role="reviewer", provider="claude", duty=DUTY_REVIEW),
        ]
    )
    made = Orchestrator(project, lineup=lineup)

    seen: list[list[str]] = []

    class _Runner:
        def run(self, arguments, *a, **kw):  # noqa: ANN001, ANN002, ANN003
            seen.append(list(arguments))
            raise RuntimeError("擋在真的花錢之前")

    monkeypatch.setattr(made, "runner", _Runner(), raising=False)
    return made, seen


def _sandbox_of(arguments: list[str]) -> str:
    """那一次呼叫真的帶了哪一個 `-s`。"""
    assert "-s" in arguments, f"這次呼叫沒有帶 -s：{arguments}"
    return arguments[arguments.index("-s") + 1]


def test_a_writing_step_gets_workspace_write(tmp_path, monkeypatch) -> None:
    """會寫檔的那一步，Codex 要拿到 `workspace-write`。

    ⛔ 這一條是 2026-09-01 之前那個 bug 的正面：那時候
    `permission="read-only"` 是**寫死的**，所以就算拒絕被拿掉，
    一個真的要寫檔的步驟也只會安靜地跑成唯讀。
    """
    made, seen = _orchestrator(tmp_path, monkeypatch, duty=DUTY_WORK)

    with pytest.raises(RuntimeError):
        made._run_role_prompt("author", "做點事", "work", read_only=False)

    assert seen, "一次都沒有送出去"
    assert _sandbox_of(seen[-1]) == "workspace-write", (
        f"會寫檔的步驟拿到的是 {_sandbox_of(seen[-1])!r} —— "
        "Codex 會什麼都寫不出來，而那一格看起來跑完了"
    )


def test_a_read_only_step_still_gets_read_only(tmp_path, monkeypatch) -> None:
    """唯讀的那一步**不准**拿到寫入權。

    這是同一條規則的另一半，而它比上面那一條更要緊：一個只該審閱的位子
    拿到寫入權，在畫面上跟正常完全一樣。
    """
    made, seen = _orchestrator(tmp_path, monkeypatch, duty=DUTY_REVIEW)

    with pytest.raises(RuntimeError):
        made._run_role_prompt("author", "看一下", "review", read_only=True)

    assert seen, "一次都沒有送出去"
    assert _sandbox_of(seen[-1]) == "read-only", (
        f"唯讀的步驟拿到的是 {_sandbox_of(seen[-1])!r} —— "
        "一個只該審閱的位子拿到了寫入權，而畫面上看不出來"
    )


def test_the_write_switch_still_downgrades_a_codex_seat(tmp_path, monkeypatch) -> None:
    """他把那一格的寫檔開關關掉，Codex 也要跟著降級成唯讀。

    那顆開關是老闆 2026-08-25 要的（「一般使用者像我的話可以自己選擇要不要
    寫入文件」）。降級寫在 `_run_role_prompt` 的漏斗裡，而沙箱翻譯**在它
    下面**——順序反過來的話，關掉開關的那一格照樣拿到寫入權。
    """
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("題目。\n", encoding="utf-8")
    lineup = Lineup.of(
        [
            # `writes_files` 是**三態的字串**，不是 bool：
            # `""` 照職責、`"on"` 明著給、`"off"` 明著關。
            # 「沒有人被問過這一題」跟「他明著說不要」是兩件事。
            AgentSlot(
                role="author",
                provider="codex",
                duty=DUTY_WORK,
                writes_files=WRITES_OFF,
            ),
            AgentSlot(role="reviewer", provider="claude", duty=DUTY_REVIEW),
        ]
    )
    made = Orchestrator(project, lineup=lineup)
    seen: list[list[str]] = []

    class _Runner:
        def run(self, arguments, *a, **kw):  # noqa: ANN001, ANN002, ANN003
            seen.append(list(arguments))
            raise RuntimeError("擋在真的花錢之前")

    monkeypatch.setattr(made, "runner", _Runner(), raising=False)

    with pytest.raises(RuntimeError):
        made._run_role_prompt("author", "做點事", "work", read_only=False)

    assert seen, "一次都沒有送出去"
    assert _sandbox_of(seen[-1]) == "read-only", (
        "他關掉了這一格的寫檔開關，而它還是拿到了 "
        f"{_sandbox_of(seen[-1])!r}"
    )


def test_nobody_hard_codes_the_sandbox_any_more(tmp_path, monkeypatch) -> None:
    """前提檢查：那個值不准再被寫死。

    2026-09-01 之前 `permission="read-only"` 是字面值，所以上面兩條裡的
    任何一條都問不出真相——兩種步驟都會拿到同一個答案，而其中一條會**恰好
    是對的**。抓不到的話，底下每一條都是恆真的。
    """
    made_w, seen_w = _orchestrator(tmp_path / "w", monkeypatch, duty=DUTY_WORK)
    with pytest.raises(RuntimeError):
        made_w._run_role_prompt("author", "做", "work", read_only=False)

    made_r, seen_r = _orchestrator(tmp_path / "r", monkeypatch, duty=DUTY_REVIEW)
    with pytest.raises(RuntimeError):
        made_r._run_role_prompt("author", "看", "review", read_only=True)

    assert _sandbox_of(seen_w[-1]) != _sandbox_of(seen_r[-1]), (
        "會寫檔的步驟跟唯讀的步驟拿到同一個沙箱值 —— "
        "那個值又被寫死了，而上面每一條都會變成恆真的"
    )
