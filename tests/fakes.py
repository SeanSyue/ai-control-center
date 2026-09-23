"""Shared test doubles for characterizing Orchestrator/WorkflowWorker.

Orchestrator's only external dependency is subprocess calls made through
a plain instance attribute, `self.runner = CliRunner(self.project_path)`
(app/core/orchestrator.py). Because that assignment happens inside
`Orchestrator.__init__` using the `CliRunner` name imported into
`app.core.orchestrator`'s own namespace, a test can swap in a scripted
double with `monkeypatch.setattr("app.core.orchestrator.CliRunner", ...)`
before constructing an Orchestrator (or a WorkflowWorker, which
constructs its own Orchestrator internally) - no production code needs
to change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.adapters.cli_runner import CliResult


# What a test project says its verification step is. Deliberately NOT
# `["python", "-m", "pytest", "-q"]`: that was the string the engine used to
# hard-code, so a fixture repeating it would let a revert pass unnoticed.
# argv[0] is an interpreter path because that is the whole point of the
# field - the project names the interpreter, AICC does not guess one.
VERIFY_COMMAND = [sys.executable, "-m", "pytest", "-q"]
VERIFY_TIMEOUT_SECONDS = 456
# 「怎樣算通過」 is declared too, and 「離開碼 0」 is a *declaration* here, not
# the engine's opinion - which is the whole point of the field. Written as
# an ordinary exit-code rule because the scripted CliRunner in these tests
# returns 0 for a pass and 1 for a failure, so the fixtures keep meaning
# what they always meant while the sentence that decides moves to the
# project. The 「輸出樣式」 half is exercised in test_project_verification.
VERIFY_SUCCESS = {"exit_codes": [0]}


def declare_verify_command(
    project: Path,
    argv: list[str] | None = None,
    timeout_seconds: int | None = VERIFY_TIMEOUT_SECONDS,
    success: dict | None = None,
) -> list[str]:
    """Write the verification declaration into a test project's project.json.

    A project with no declaration is a real and separate state (the workflow
    stops and asks for one), so tests that want to reach the verification
    step have to say how this project is verified - same as a real project.
    That is three sentences now, not one: 跑什麼 (`verify_command`), 等多久
    (`verify_timeout_seconds`), 怎樣算過 (`verify_success`). Each of them
    stops the run when missing, and the negative of all three is
    test_a_project_that_never_declared_how_to_verify_stops_instead_of_red.
    """
    declared = list(argv if argv is not None else VERIFY_COMMAND)
    metadata_path = project / ".aicc" / "project.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    if metadata_path.is_file():
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["verify_command"] = declared
    if timeout_seconds is not None:
        payload["verify_timeout_seconds"] = timeout_seconds
    payload["verify_success"] = dict(
        success if success is not None else VERIFY_SUCCESS
    )
    metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return declared


def scripted_cli_runner_class(script: list[CliResult], auto: bool = True):
    """Build a CliRunner-compatible class that replays `script` in order.

    Each call to `.run(...)` pops and returns the next `CliResult` from
    the script, regardless of the arguments passed - callers assert on
    argument shape separately via the `.calls` list the returned class
    exposes. Raises `AssertionError` if more calls happen than the
    script has results for, so a test's script length also documents
    exactly how many CLI invocations a given workflow branch performs.
    """
    remaining = list(script)
    calls: list[dict] = []

    class ScriptedCliRunner:
        def __init__(self, working_directory: Path) -> None:
            self.working_directory = working_directory

        def run(
            self,
            arguments: list[str],
            timeout_seconds: int | float | None = 300,
            input_text: str | None = None,
        ) -> CliResult:
            calls.append(
                {
                    "arguments": list(arguments),
                    "input_text": input_text,
                    "timeout_seconds": timeout_seconds,
                }
            )
            canned = _canned_setup_answer(input_text, auto)
            if canned is not None:
                # **不消耗腳本。**這幾步是每一次開發執行的前置，不是任何一條
                # 分支的一部分——見 `SETUP_STEPS`。
                calls[-1]["setup_step"] = True
                return canned
            if not remaining:
                raise AssertionError(
                    "ScriptedCliRunner ran out of scripted results - "
                    f"got an extra call with arguments={arguments!r}"
                )
            return remaining.pop(0)

    ScriptedCliRunner.calls = calls
    return ScriptedCliRunner


def cli_result(
    stdout: str,
    success: bool = True,
    stderr: str = "",
    command: str = "fake",
) -> CliResult:
    """Convenience constructor for a successful/failed CliResult."""
    return CliResult(
        command=command,
        success=success,
        return_code=0 if success else 1,
        stdout=stdout,
        stderr=stderr,
    )


# 需求確認那一格「問清楚了」的回答。
#
# 2026-08-22 起，每一次執行在派人動手之前會先跑一輪需求確認——**那是一次真的
# 模型呼叫**，所以每一份假腳本都要多一則。放在這裡而不是各檔案自己寫一份：
# 這句話的格式是引擎剖析的對象（`parse_intake`），各抄一份的話，格式改了只會
# 有一部分測試變紅，而剩下那些會安靜地開始測一個不存在的行為。
#
# 想測「它還要再問一題」或「它回了看不懂的東西」的測試，不要用這一則——
# 自己寫一則，那正是那些測試的主題。
def intake_settled(criteria: str = "") -> CliResult:
    return cli_result(
        "需求定案：\n"
        + (
            criteria
            or "1. 完成狀態：任務描述裡那件事做完。\n"
            "2. 怎麼驗：跑專案宣告的驗證指令。\n"
            "3. 不准動什麼：.aicc/task.md。\n"
            "4. 每輪要記什麼：改了哪些檔案、驗證結果。\n"
            "5. 什麼時候停：驗證紅了兩輪還沒進展就回報。"
        )
    )


def with_intake(script, settings=None, user_answer: str = "", intake=None):
    """**已經不需要了，留著是為了不動一百多個呼叫點。**

    前置步驟現在由假的 CLI 照內容自己回答（見 `SETUP_STEPS`），不再靠在腳本
    前面補一則。留一個原樣回傳的函式，比一次改掉一百多處安全——每改一處都是
    一次弄錯位置的機會。
    """
    return list(script)


def _legacy_with_intake(script, settings=None, user_answer: str = "", intake=None):
    """開發路線的假腳本，前面補一則需求確認的回答。

    只有「開發」那條路會跑需求確認；計畫模式（`workflow_mode="planning"`）走
    的是另一條路，不經過那一步。帶著使用者答案續跑的那一條**會**經過，因為
    它進的也是開發路線。

    寫成一個函式而不是各檔案自己判斷：這個條件跟 `WorkflowWorker.run()` 裡
    那個分支是同一件事，兩份各自演化的話，測試會開始餵一則沒有人要的回答，
    而症狀是「假腳本用完了」——完全看不出來是這裡錯了。
    """
    mode = getattr(settings, "workflow_mode", "development") if settings else "development"
    if mode != "development" and not user_answer:
        return list(script)
    return [intake or intake_settled()] + list(script)


def calls_after_intake(calls: list[dict], settings=None, user_answer: str = "") -> list[dict]:
    """這一條分支真的派了幾次工——**不含每一次都會跑的前置**。

    前置是需求確認與「先交做法」（見 `SETUP_STEPS`）。把它們算進去，等於要求
    每一個案例都重述同一件事，而且下一次再加一個前置步驟就要再改一遍全部。

    **不是默默拿掉**：拿掉的是被假 CLI 認出來的那幾次，而它認的是那兩步的
    開場白。那兩步哪天不跑了，`setup_calls()` 會是空的，而守著那件事的是
    專門測它們的那幾支。
    """
    return [call for call in calls if not call.get("setup_step")]


def _legacy_calls_after_intake(calls: list[dict], settings=None, user_answer: str = "") -> list[dict]:
    """把需求確認那一次派工從清單前面拿掉，並且**證明它真的發生過**。

    為什麼要拿掉：需求確認是每一次開發執行的前置，不是任何一條分支的一部分。
    測試修正迴圈的那些案例，斷言的是「這條分支跑了幾次」——把一個所有分支
    都會跑的步驟算進去，等於要求每一個案例都重述同一件事，而且下一次再加
    一個前置步驟就要再改一遍全部。

    為什麼不是默默拿掉：那樣的話需求確認哪天不跑了，這些測試一條都不會紅。
    所以這裡**先確認第一次派工真的是它**，確認不過就在這裡爆——而不是讓
    某個下游斷言以一個看不懂的形狀失敗。
    """
    mode = getattr(settings, "workflow_mode", "development") if settings else "development"
    if mode != "development" and not user_answer:
        return list(calls)
    if not calls:
        # 一次派工都沒發生：那是「還沒開始就停下來」那一族結局（例如專案沒有
        # 宣告怎麼驗）。**那些案例本來就不該有任何派工**，在這裡當成錯誤的話，
        # 會把一個正確的行為報成缺了需求確認。
        return []
    first = calls[0].get("input_text") or ""
    assert "需求定案：" in first and "需要確認：" in first, (
        "第一次派工不是需求確認。開發路線在派人動手之前要先問清楚需求，"
        "那一步不見了——而底下每一條斷言都還是綠的，因為它們數的是它後面"
        f"那幾次。實際送出去的開頭：{first[:120]!r}"
    )
    return list(calls[1:])


# --- 每一次開發執行的前置步驟，由假的 CLI 自動回答 --------------------------
#
# 2026-08-22 起，派人動手之前固定會跑兩步：
#
#   1. **需求確認**——第一格跟使用者把需求問清楚，問完的那份東西就是驗收標準。
#   2. **先交做法**——做事的那一格自己說它打算怎麼做，還不動手（定案
#      【執行角色第一次交做法，不交成品】）。
#
# 它們是**每一條分支都會跑的前置**，不是任何一條分支的一部分。以前每加一步，
# 一百多處假腳本的位置就全部位移一格——而位移之後的斷言看起來完全合理，
# 只是量的是別的東西。
#
# 所以改成**照內容回答**：假的 CLI 看到這兩步的開場白就自己回一則，不消耗
# 腳本、也不要求任何測試知道它們排第幾。想測這兩步本身的測試傳 `auto=False`
# 自己接管。
#
# 認的是**開場白**，因為那是每一份提示詞裡唯一保證存在、而且指名那一步的句子。
SETUP_STEPS: tuple[tuple[str, str], ...] = (
    (
        "你是此專案的需求確認者。",
        "需求定案：\n"
        "1. 完成狀態：任務描述裡那件事做完。\n"
        "2. 怎麼驗：跑專案宣告的驗證指令。\n"
        "3. 不准動什麼：.aicc/task.md。\n"
        "4. 每輪要記什麼：改了哪些檔案、驗證結果。\n"
        "5. 什麼時候停：驗證紅了兩輪還沒進展就回報。",
    ),
    (
        "你是這件事的執行者。動手之前",
        "**我理解的需求**\n照任務描述做那件事。\n\n"
        "**我打算怎麼做**\n照驗收標準一條一條做。\n\n"
        "**我不確定的地方**\n沒有——這個案子的範圍很清楚。\n\n"
        "**我判斷不該做的**\n不動 .aicc/task.md。",
    ),
    (
        # 2026-08-24 老闆拍板「規定要最後一個 NODE 去驗證」之後，這一步
        # 變成**每一次執行都會跑**——包括出廠的一人與兩人陣容。所以它跟
        # 上面兩步同一種東西：前置，不是分支。
        #
        # 放進來的那一刻有 18 條測試變紅，而它們全部都是「假腳本剛好只夠
        # 原本的呼叫次數」。**那正是這個機制存在的理由**：不然那 18 條要
        # 一條一條補一則回答，而補錯位置的斷言看起來完全合理。
        "你是此專案的核准者，負責審閱所有簽章。",
        "拍板：可以\n理由：章都在，而且都蓋在同一版的交付物上。",
    ),
)


def _canned_setup_answer(input_text: str | None, auto=True) -> CliResult | None:
    """這一次派工是不是前置步驟；是的話回一則現成的答案。

    `auto` 可以是：

    * `True`——用 `SETUP_STEPS` 那幾則現成的（絕大多數測試）。
    * `False`——關掉，前置也要自己寫進腳本（想測那兩步本身的時候）。
    * 一組 `(開場白, 回答)`——換掉其中一則，例如要在驗收標準裡塞一個哨兵。
    """
    if auto is False:
        return None
    text = input_text or ""
    steps = SETUP_STEPS if auto is True else tuple(auto)
    for marker, answer in steps:
        if marker in text:
            return cli_result(answer)
    return None


def setup_with(**overrides: str) -> tuple[tuple[str, str], ...]:
    """換掉其中一則前置回答，其餘照舊。

    用法：`setup_with(intake="需求定案：⋯哨兵⋯")`。
    """
    keys = {"intake": 0, "intent": 1, "approval": 2}
    steps = [list(pair) for pair in SETUP_STEPS]
    for name, answer in overrides.items():
        steps[keys[name]][1] = answer
    return tuple((marker, answer) for marker, answer in steps)


def setup_calls(calls: list[dict]) -> list[dict]:
    """那幾次前置的派工。給想斷言「它真的跑了」的測試用。"""
    return [call for call in calls if call.get("setup_step")]


# 只自動回答「先交做法」那一步，需求確認留給測試自己接管。
#
# 想測需求確認本身的檔案用這個：整個關掉（`auto=False`）的話，連「先交做法」
# 都要自己寫進腳本，而那一步跟它們要測的事情沒有關係——那種腳本讀起來會像
# 在測需求確認，實際上一半的行是在餵一個無關的步驟。
SETUP_INTENT_ONLY = SETUP_STEPS[1:]

# 想測「驗簽章那一步本身」的檔案用這個：前面兩步照樣自動回答，**最後那一步
# 讓腳本自己接管**。
#
# 2026-08-24 加的。不加的話，那幾支測的東西會被現成答案蓋掉——它們斷言的是
# 「回覆看不懂就停下來」「不同意要指名退給誰」，而現成答案永遠說「可以」。
SETUP_WITHOUT_APPROVAL = SETUP_STEPS[:2]
