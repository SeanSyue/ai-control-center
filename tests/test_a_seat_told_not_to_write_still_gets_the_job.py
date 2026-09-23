r"""關掉一格的寫檔開關，那一格**照樣被派工**，只是那一次走唯讀那條路。

老闆 2026-08-25 的原話：

> 一般使用者像我的話可以自己選擇要不要寫入文件

在這之前 AICC 唯一做得到的是把那一格的職責從「執行」改掉，而那**不是**
「照跑但不寫檔」：`app/core/steps/building.py` 挑人只挑 `duty == DUTY_WORK`，
所以改掉職責之後那一格**整輪不會被叫到**——它一句話都不說，而後面每一格
看得到的東西是靠 AICC 把前一格說的話接下來貼過去的，不是靠檔案。少了它，
下一格拿到的是一份中間缺一塊的脈絡。

## 改壞會怎樣

| 改壞的樣子 | 使用者看到什麼 |
|---|---|
| 降級那一段拿掉（`read_only = True` 不做）| 他明明把開關關了，那一格照樣改了他的檔案。畫面上那個勾選框還是關的，執行紀錄也看不出差別——**唯一發現的方式是事後去看 git diff**。 |
| 降級寫成「跳過這一格」或丟例外 | 那一格這一輪不開口。下一格拿到的脈絡中間缺一塊，於是把同一件事再做一次，或做一件跟前面互相矛盾的事——而兩者都會被當成正常產出收下去。 |
| 每次執行重建陣容時漏掉這一欄 | 陣容頁上開關是關的，**而開始執行的當下它變回開的**。這一條路每一次執行都會走到，所以它不是「換機器才會發生」。 |
| 空的那一欄讀成「不准寫」 | 每一個既有專案的執行那一格在升級的當下靜靜變成唯讀。他會看到「跑完了，一個檔案都沒有變」，而畫面上一切正常——`DECISIONS.md`【版本號要真的被讀】那條的不變式逐字寫著這件事。 |
| 這一欄變成「給得出權限」（複查那一格填 on 就能寫）| 一個他排成複查的位子動得了他的檔案。**權限只能由職責給**，這一欄只拿得走。 |
| 讀不懂的值退回某一個答案 | 一個讀不懂的權限被讀成「可以」是無聲的授權，讀成「不行」是無聲的癱瘓。兩種都看不出來。 |

## 案例為什麼長這樣

名字全部用他自己取的中文（「初稿」「精修」），不用出廠的 `author`——
出廠名字會走 `_DUTY_BY_LEGACY_ROLE` 回推，**掉了跟沒掉長得一模一樣**。

而且**兩格都是執行**，一格關一格開。只排一格的話，「全部都變成唯讀」
跟「只有這一格變成唯讀」在斷言上分不出來——這一份要量的正是那個差別。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.lineup import (  # noqa: E402
    DUTY_REVIEW,
    DUTY_WORK,
    FIELD_WRITES,
    WRITES_OFF,
    WRITES_ON,
    AgentSlot,
    Lineup,
)
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402
from tests.fakes import (  # noqa: E402
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

# 他自己取的名字。**不准換成 `author`／`reviewer`**——出廠名字會被
# `_DUTY_BY_LEGACY_ROLE` 推回原值，那會把這一整支變成恆綠的。
MUTE = "初稿"      # 執行，而且**他把寫檔關掉了**
LOUD = "精修"      # 執行，開關沒有動過
CHECKER = "複查"

# 關掉寫檔的那一格說的話。它必須原封不動出現在下一格的提示詞裡——
# 「不寫檔」不等於「傳不下去」，傳話的人是 AICC，不是檔案。
SAID = "哨兵-7f31c-我建議先把匯出欄位對齊法遵那份清單，再動手改格式"

PASS_REVIEW = (
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 把客戶匯出檔的欄位整理一次\n", encoding="utf-8"
    )
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    return project


def _seat(project: Path, slots: list[AgentSlot]) -> None:
    SessionRegistry(project).save_lineup(Lineup.of(slots))


def _run(project: Path, monkeypatch, script):
    QApplication.instance() or QApplication([])
    runner_class = scripted_cli_runner_class(script)
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
            max_fix_attempts=1,
        ),
    )
    failed: list = []
    completed: list = []
    worker.failed.connect(failed.append)
    worker.completed.connect(completed.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed, runner_class.calls


def _dispatches_to(calls: list[dict], seat: str) -> list[dict]:
    """派給這一格的每一次呼叫。**認 `--name`，不認提示詞裡的字。**

    提示詞裡到處都是別人的名字（誰訂的標準、這條線上有哪幾格），
    用字串比對挑「這是誰的那一次」會挑到別人的。
    """
    out = []
    for call in calls:
        arguments = call.get("arguments") or []
        if "--name" not in arguments:
            continue
        if seat in arguments[arguments.index("--name") + 1]:
            out.append(call)
    return out


def _permission(call: dict) -> str:
    arguments = call.get("arguments") or []
    if "--permission-mode" not in arguments:
        return ""
    return arguments[arguments.index("--permission-mode") + 1]


def _task_dispatch(calls: list[dict], seat: str) -> dict:
    """那一格**真的動手**的那一次派工。

    不是它「說打算怎麼做」的那一次——那一步對每一格本來就是唯讀的
    （`test_the_worker_says_what_it_will_do_first.py`）。拿那一次來斷言
    「唯讀」的話，這一份**在開關根本沒有接上的時候也是綠的**。
    """
    mine = [
        call
        for call in _dispatches_to(calls, seat)
        if not call.get("setup_step")
    ]
    assert mine, f"【{seat}】這一輪一次工都沒有被派到"
    return mine[0]


def test_the_seat_he_muted_still_runs_and_its_words_still_travel(
    tmp_path: Path, monkeypatch
) -> None:
    """關掉寫檔的那一格照樣動手、照樣講話、話照樣接給下一格。

    改壞降級那一段之後這一條會紅在權限那一句：他關掉的那一格拿著寫入權限
    去跑，而畫面上那個開關還是關的。改成「跳過那一格」則會紅在哨兵那一句：
    下一格的提示詞裡沒有它說的話。
    """
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot(MUTE, "claude", duty=DUTY_WORK, writes_files=WRITES_OFF),
            AgentSlot(LOUD, "claude", duty=DUTY_WORK),
            AgentSlot(CHECKER, "codex", duty=DUTY_REVIEW),
        ],
    )

    _completed, failed, calls = _run(
        project,
        monkeypatch,
        [
            cli_result(SAID),
            cli_result('{"result": "改好了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )
    assert failed == [], failed

    muted = _task_dispatch(calls, MUTE)
    assert _permission(muted) == "plan", (
        f"他把【{MUTE}】的寫檔關掉了，而那一次派工送出的是 "
        f"`--permission-mode {_permission(muted)!r}`。"
        "那一格動得了他的專案檔案，而陣容頁上那個開關還是關的。"
    )

    loud = _task_dispatch(calls, LOUD)
    assert _permission(loud) != "plan", (
        f"【{LOUD}】的開關他一個字都沒動，卻也變成唯讀了。"
        "整條線一起降級跟這個開關沒有關係——那是設定頁那一個，"
        "而這一格的開關就白做了。"
    )

    assert SAID in (loud["input_text"] or ""), (
        f"【{MUTE}】說的話沒有接到【{LOUD}】手上。不寫檔不等於傳不下去："
        "傳話的人是 AICC，不是檔案。"
    )


def test_muting_a_seat_is_not_the_same_as_changing_its_duty(
    tmp_path: Path, monkeypatch
) -> None:
    """關開關 ≠ 改職責。**這一條是這件事的重點。**

    改成用職責達成的話，`building.py` 整個挑不到它：那一格這一輪一次工都
    沒有，於是這一條會紅在 `_task_dispatch` 的「一次工都沒有被派到」。
    """
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot(MUTE, "claude", duty=DUTY_WORK, writes_files=WRITES_OFF),
            AgentSlot(CHECKER, "codex", duty=DUTY_REVIEW),
        ],
    )

    _completed, failed, calls = _run(
        project,
        monkeypatch,
        [
            cli_result(SAID),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )
    assert failed == [], failed

    # 職責一個字都沒有被動過——引擎是靠它挑人的。
    stored = SessionRegistry(project).load_lineup()
    assert stored.for_role(MUTE).duty == DUTY_WORK
    assert stored.for_role(MUTE).writes_files == WRITES_OFF
    assert stored.may_write_files(MUTE) is False

    assert _permission(_task_dispatch(calls, MUTE)) == "plan"


def test_an_old_sessions_json_keeps_exactly_the_powers_it_had(
    tmp_path: Path,
) -> None:
    """2026-08-25 之前寫的檔案讀回來，**每一格碰得到的東西一模一樣**。

    那些檔案裡沒有這一欄，而且裡面**沒有任何地方**記著使用者對這一題的
    答案——這一版才第一次問他。空的讀成「不准寫」的話，每一個既有專案的
    執行那一格會在升級的當下靜靜變成唯讀，而他看到的是「跑完了，一個檔案
    都沒有變」。反過來，這一欄也**不會讓任何一格多拿到權限**：複查那一格
    升級前後都碰不到檔案。
    """
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "sessions.json").write_text(
        json.dumps(
            {
                "version": 1,
                "selected": {"claude": "c-1", "codex": "x-1"},
                "sessions": {"claude": [], "codex": []},
                # 他自己取的名字＋明著寫的職責，就是舊檔真的長的樣子。
                # **沒有 `writes_files` 這一欄。**
                "lineup": [
                    {
                        "role": MUTE, "provider": "claude",
                        "session_id": "c-1", "duty": "work",
                    },
                    {
                        "role": CHECKER, "provider": "codex",
                        "session_id": "x-1", "duty": "review",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = SessionRegistry(project).load_lineup()

    assert loaded.for_role(MUTE).writes_files == "", (
        "舊檔沒有這一欄，讀回來卻變成一個明確的答案了——"
        "那個答案不是使用者給的，是這一版替他填的。"
    )
    assert loaded.may_write_files(MUTE) is True, (
        "他親手排成「執行」的那一格，升級之後不能寫檔了。"
        "他會看到「跑完了，一個檔案都沒有變」，而畫面上一切正常。"
    )
    assert loaded.may_write_files(CHECKER) is False, (
        "複查那一格升級之後拿到了寫入權——這一欄只准拿得走，給不出來。"
    )


def test_the_switch_can_take_write_away_but_never_hand_it_out(
    tmp_path: Path,
) -> None:
    """**權限由職責給，這一欄只拿得走。**

    合併成一欄（或讓這一欄自己說了算）的話，一個他排成「複查」的位子填一個
    `on` 就動得了他的檔案——而複查那一格的整個意義就是它沒看過的東西不算數。
    """
    reviewer = AgentSlot(
        CHECKER, "codex", duty=DUTY_REVIEW, writes_files=WRITES_ON
    )
    worker = AgentSlot(MUTE, "claude", duty=DUTY_WORK, writes_files=WRITES_ON)
    muted = AgentSlot(MUTE, "claude", duty=DUTY_WORK, writes_files=WRITES_OFF)

    assert reviewer.may_write_files is False
    assert worker.may_write_files is True
    assert muted.may_write_files is False


def test_a_switch_this_build_cannot_read_is_not_guessed_either_way(
    tmp_path: Path,
) -> None:
    """讀不懂的值**不准退回某一個答案**，那一格整格留著、派不動。

    讀成「可以」是無聲的授權，讀成「不行」是無聲的癱瘓——兩種在畫面上
    都看不出來。留成 `UnreadableSlot` 的話它派不動（也就寫不了任何東西），
    而原文一個字都沒有從硬碟上消失。
    """
    raw = [
        {
            "role": MUTE, "provider": "claude", "duty": "work",
            # 新版寫的檔案被舊版打開就長這樣。
            "writes_files": "ask_every_time",
        },
        {"role": CHECKER, "provider": "codex", "duty": "review"},
    ]

    lineup = Lineup.from_list(raw)

    assert lineup.roles == (CHECKER,), (
        "讀不懂寫檔開關的那一格照樣被派工了——而沒有人知道它會不會動檔案。"
    )
    assert [lost.field for lost in lineup.unreadable] == [FIELD_WRITES]
    assert lineup.unreadable_names == (MUTE,)
    assert lineup.may_write_files(MUTE) is False
    # 寫回去的時候原文一個字都不准掉。
    assert lineup.to_list()[0] == raw[0]


def test_the_page_can_flip_it_without_touching_the_duty(
    tmp_path: Path, monkeypatch
) -> None:
    """陣容頁按下去只改這一欄，職責一個字都不動。

    兩欄合併的話，這一顆會順手把職責改掉，而 `building.py` 從此挑不到
    那一格——他按的是「這一輪別動我的檔案」，得到的是「這一輪沒有人做事」。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    panel = LineupWebPanel(project)
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    panel.handle_action("set_slot_role", f"0:{MUTE}")
    panel.handle_action("set_slot_duty", "0:work")

    before = panel.collect_state()["lineup"]["slots"][0]
    assert before["writes_files"] is True, (
        "他還沒動過那顆開關，而頁面收到的是「這一格不會動檔案」——"
        "那一格的職責是執行，這句話是假的。"
    )

    panel.handle_action("set_slot_writes", f"0:{WRITES_OFF}")

    after = panel.collect_state()["lineup"]["slots"][0]
    assert after["writes_files"] is False
    assert after["duty"] == DUTY_WORK, (
        "關掉寫檔順手把職責也改掉了——那一格從此不會被派工，"
        "而他按的是「照跑，但別動我的檔案」。"
    )
    # 存回硬碟的那一份也要記得。頁面上關掉、下次打開又是開的，
    # 跟這顆開關沒接上完全一樣。
    reloaded = SessionRegistry(project).load_lineup()
    assert reloaded.for_role(MUTE).writes_files == WRITES_OFF
