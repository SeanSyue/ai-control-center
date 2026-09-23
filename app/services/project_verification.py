"""How a project proves it still works - declared by the project itself.

Before this module the verification step was `["python", "-m", "pytest",
"-q"]`, written into `Orchestrator.run_tests`. Two things were wrong with
that, and only the second one is about Python:

1. **It could not tell 「沒得驗」 from 「驗了但是紅的」.** `python` is resolved
   off PATH, and on the machine this was found on that resolves to the
   WindowsApps stub, which has no pytest. The step came back with return
   code 1 and 「No module named pytest」 - indistinguishable, to every caller
   downstream, from a real test failure. The workflow then handed that
   non-failure to the author as a bug to fix, burned its single fix attempt
   on code that was never broken, and ended before review or memory update.
   A run that cannot verify anything must **stop and say so**, never report
   a red light it did not earn.

2. **It hard-coded one ecosystem.** A product that also has to work for a
   Node repo, a Go service, or a folder of documents cannot own the sentence
   「怎麼驗證」. The project owns it; AICC reads it.

So the declaration lives in the project's own `.aicc/project.json`:

    "verify_command": [".venv\\\\Scripts\\\\python.exe", "-m", "pytest", "-q"],
    "verify_timeout_seconds": 900,
    "verify_success": {"output_pattern": "(?m)^\\\\d+ passed .* in [\\\\d.]+s"}

`verify_command` is **argv, not a shell line** - a list of already-separated
arguments. That is deliberate: a single string would have to be split by
somebody, and every splitter disagrees about quotes, spaces and backslashes
(`C:\\Program Files\\...` being the obvious casualty). argv has no such
ambiguity, and `CliRunner` already knows how to deliver a list verbatim.

argv[0] may name an interpreter, and a **relative** path there is resolved
against the project root, not the process working directory. That matters
twice: `shutil.which()` (what `CliRunner` uses) resolves a relative path
against the *AICC process's* cwd, which is not the project; and a relative
declaration is the only form that survives being cloned to another machine.

## 「怎樣算通過」 is a declaration too (2026-08-21)

`verify_command` moved 「跑什麼」 to the project and left 「算不算過」 in the
engine, where it was `process.returncode == 0`. That leftover is the same
bug wearing a different hat, and this repo is the proof: its own suite
prints 「1224 passed in 129.22s」 and then exits **0xC0000005** because
QtWebEngine faults while tearing down. Its own CLAUDE.md says 「看最後一行
不要看 exit code」 - the engine was doing the exact opposite, so the very
first real run would have spent its one fix attempt repairing code that
nothing had found fault with. `go test` (exit 1 on a build error *and* on a
failure), a Node runner behind a wrapper that always exits 0, and a linter
that reports through its output are all the same shape.

So `verify_success` is declared, and there is **no built-in default**:

    "verify_success": {"exit_codes": [0]}                     # 一般情形
    "verify_success": {"output_pattern": "(?m)^ok\\\\s"}        # go test
    "verify_success": {                                       # 兩個都要成立
        "exit_codes": [0],
        "output_pattern": "(?m)^\\\\d+ passed",
        "match": "all"
    }

Write the pattern against output you have actually seen, not output you
remember. This repo's first attempt required the summary line to end at the
seconds - and pytest appends ` (0:02:05)` once a run passes 60 seconds, so
a completely green 125-second suite would have been judged FAILED. The
declaration meant to remove the false red had reintroduced it; a test that
feeds real captured tails through the repo's own rule is what caught it.

* `exit_codes` - a list of integers. Anything else is a failure.
* `output_pattern` - a Python regular expression, `re.search`-ed against
  stdout and stderr joined together (which stream a runner talks on is not
  something the project should have to know).
* `match` - `"all"` or `"any"`. **Required when both criteria are present**
  and rejected otherwise: with both declared, guessing the connective would
  be the engine inventing half the criterion, and the two answers differ on
  exactly the interesting case (「印了 passed 但離開碼非零」).

At least one criterion is required, and unknown keys are refused rather
than ignored - a typo'd `"exit_code": [0]` that silently disabled the whole
rule would be a false **green**, which is worse than anything above.

## Nothing here invents a default, timeouts included

A project that has not declared anything gets
`VerifyCommandLookup(command=None, problem=...)`, and a project.json written
before these fields existed is exactly that case - not an error, and not
something to guess a value for.

`verify_timeout_seconds` used to be the one exception: a module-level 120.
It was also the one that bites, because a timeout arrived downstream as
`success=False` - the false red again, now for every Node/Go/monorepo whose
suite runs longer than two minutes. This repo needs 138 seconds and only
escaped by declaring 900. The default is gone; an undeclared timeout stops
the run and asks, the same as an undeclared command.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.adapters.cli_runner import CliResult, CliRunner
from app.i18n import t


VERIFY_COMMAND_KEY = "verify_command"
VERIFY_TIMEOUT_KEY = "verify_timeout_seconds"
VERIFY_SUCCESS_KEY = "verify_success"

EXIT_CODES_KEY = "exit_codes"
OUTPUT_PATTERN_KEY = "output_pattern"
MATCH_KEY = "match"

MATCH_ALL = "all"
MATCH_ANY = "any"

# The verdict on one verification run. Four values, not a boolean, because
# the three ways a run can fail to be green are three different next moves:
# fix the code, set up the project, or look at why it never finished.
TESTS_PASSED = "PASSED"
TESTS_FAILED = "FAILED"
TESTS_TIMED_OUT = "TIMED_OUT"
TESTS_NOT_DECLARED = "NOT_DECLARED"
# 這個專案沒有可執行的檢查，但**有一份質化驗收標準**
#（`.aicc/project.json` 的 `acceptance`）。機器什麼都沒跑，判斷在審查那一格。
#
# 跟 `NOT_DECLARED` 分開是必要的：那一個的意思是「還沒設定」，畫面上會叫
# 使用者去設定；這一個的意思是「設定好了，只是不是用指令驗的」——把剪片的
# 案子畫成「尚未設定驗證」，是叫一個已經設定好的人再設定一次。
TESTS_BY_RUBRIC = "BY_RUBRIC"
# The step was never reached (the run ended before it). Not a verdict, but
# it lives in the same field, so it lives beside them.
TESTS_NOT_RUN = "NOT_RUN"

_SETTINGS_LOCATION = ".aicc\\project.json"

_COMMAND_EXAMPLE = (
    "[\".venv\\\\Scripts\\\\python.exe\", \"-m\", \"pytest\", \"-q\"]"
)
def _success_example() -> str:
    """設定檔長什麼樣子的例子。**在函式裡叫 `t()`**——模組層叫會把字凍在 import 那一刻。"""
    return t("verify.success_example")


class VerificationNotDeclared(RuntimeError):
    """Raised instead of returning a failed result with nothing behind it.

    An exception rather than a `CliResult(success=False)` on purpose: a
    falsy result object is one `if not result.success` away from being
    treated as a red test run, which is the exact confusion this whole
    module exists to remove.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Verdict:
    """What the project's own rule says about one verification run."""

    status: str
    reason: str

    @property
    def passed(self) -> bool:
        return self.status == TESTS_PASSED


@dataclass(frozen=True)
class SuccessRule:
    """The project's answer to 「怎樣算通過」.

    Held as data rather than a callback so it can be printed back to the
    user in the run record: 「離開碼 3221225477 不在宣告的通過清單 [0] 內」
    is a sentence somebody can act on, and 「測試失敗」 is not.
    """

    exit_codes: tuple[int, ...] | None
    output_pattern: str | None
    match_all: bool

    @property
    def describe(self) -> str:
        parts = []
        if self.exit_codes is not None:
            parts.append(f"離開碼屬於 {list(self.exit_codes)}")
        if self.output_pattern is not None:
            parts.append(f"輸出符合 /{self.output_pattern}/")
        joiner = t("verify.join.and") if self.match_all else t("verify.join.or")
        return joiner.join(parts)

    def judge(self, return_code: int, output: str) -> Verdict:
        checks: list[tuple[bool, str]] = []

        if self.exit_codes is not None:
            met = return_code in self.exit_codes
            checks.append(
                (
                    met,
                    f"離開碼 {return_code} "
                    f"{'在' if met else '不在'}宣告的通過清單 "
                    f"{list(self.exit_codes)} 內",
                )
            )

        if self.output_pattern is not None:
            met = re.search(self.output_pattern, output) is not None
            checks.append(
                (
                    met,
                    f"輸出{'' if met else '沒有'}出現宣告的通過樣式 "
                    f"/{self.output_pattern}/",
                )
            )

        met_all = all(met for met, _ in checks)
        met_any = any(met for met, _ in checks)
        passed = met_all if self.match_all else met_any

        return Verdict(
            status=TESTS_PASSED if passed else TESTS_FAILED,
            reason="；".join(text for _, text in checks) + "。",
        )


@dataclass(frozen=True)
class VerifyCommand:
    argv: list[str]
    timeout_seconds: int
    success_rule: SuccessRule

    @property
    def display(self) -> str:
        return subprocess.list2cmdline(self.argv)


@dataclass(frozen=True)
class VerificationOutcome:
    """One verification run, judged.

    Wraps the `CliResult` rather than replacing it so that the raw output
    is still there for the review step, but **`success` here means 「符合
    專案宣告的通過條件」**, not `returncode == 0`. Callers that keep asking
    a `CliResult` directly are asking the wrong object - that is what
    `Orchestrator.run_tests` returning this type is for.
    """

    verdict: Verdict
    result: CliResult

    @property
    def status(self) -> str:
        return self.verdict.status

    @property
    def reason(self) -> str:
        return self.verdict.reason

    @property
    def passed(self) -> bool:
        return self.verdict.passed

    @property
    def success(self) -> bool:
        return self.verdict.passed

    @property
    def timed_out(self) -> bool:
        return self.verdict.status == TESTS_TIMED_OUT

    @property
    def stdout(self) -> str:
        return self.result.stdout

    @property
    def stderr(self) -> str:
        return self.result.stderr

    @property
    def return_code(self) -> int:
        return self.result.return_code


def combined_output(result: CliResult) -> str:
    """What `output_pattern` is matched against.

    Both streams, because which one a runner talks on is an implementation
    detail of the runner: pytest summarises on stdout, plenty of Node tools
    summarise on stderr, and a project should not have to know (or have its
    declaration break when a tool changes its mind).

    **The separator is a newline, and that is load-bearing - never join
    the two directly.** `CliRunner` strips both streams, so the last line
    of stdout arrives with no trailing newline of its own. With no
    separator here it would be glued to the first line of stderr, and an
    `output_pattern` anchored to a line end (`(?m)...$`) would stop
    matching. Not hypothetical: this repo's own declaration ends at the
    pytest summary line, its stdout ends with that line, and its stderr is
    a page of QtWebEngine/Chromium noise on every single run - so
    「1264 passed in 124.10s (0:02:04)」 would arrive as
    「1264 passed in 124.10s (0:02:04)[30012:...ERROR:...」 and a fully
    green suite would be reported FAILED. The guard is
    `test_the_summary_line_survives_a_page_of_chromium_noise` in
    tests/test_project_verification.py.
    """
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def never_started_problem(display: str, result: CliResult) -> str:
    """The verification command never launched. That is not a red light.

    The second line of defence behind one shared resolver, and it exists
    because the first line can still be wrong. `CliResult.never_started`
    is set on every branch of `CliRunner.run` that returns without a
    process ever existing - unresolvable tool, `Popen` raising `OSError`,
    an argument a command line cannot carry - and all three have to invent
    a `return_code` of -1 to fill the field. Handing that invented number
    to the project's `SuccessRule` produces 「離開碼 -1 不在宣告的通過清單
    [0] 內」: a FAILED verdict for code nothing ever executed, and then an
    author dispatched to fix a bug that does not exist, spending the one
    paid fix attempt the run gets.

    So it comes out here instead, wearing the same clothes as an
    undeclared command: stop, say what is wrong, ask the user. Nothing
    downstream may turn it back into `TESTS_FAILED`.
    """
    detail = (result.stderr or result.stdout).strip()
    return (
        f"這個專案宣告的驗證指令沒有啟動起來：{display}。"
        + (f"（{detail}）" if detail else "")
        + "沒有任何程式被執行過，所以這不是測試失敗——沒有人證明程式壞了。"
        f"請確認 {_SETTINGS_LOCATION} 的 {VERIFY_COMMAND_KEY} 指到一個"
        "真的跑得起來的程式。"
    )


def timed_out_verdict(timeout_seconds: int) -> Verdict:
    """The deadline fired. That is not a red light.

    Kept apart from `TESTS_FAILED` deliberately: a timeout means nobody
    found anything wrong with the code, so dispatching the author to 「修正
    造成測試失敗的問題」 would burn a paid call on a bug that has not been
    shown to exist. The user's move is a bigger `verify_timeout_seconds`,
    or a look at what hung - neither of which is 「改程式」.
    """
    return Verdict(
        status=TESTS_TIMED_OUT,
        reason=(
            f"驗證指令在宣告的 {timeout_seconds} 秒內沒有結束，已被中止。"
            "這不是測試失敗——沒有人證明程式壞了。"
            f"請確認 {_SETTINGS_LOCATION} 的 {VERIFY_TIMEOUT_KEY} 是否足夠，"
            "或檢查驗證指令為什麼卡住。"
        ),
    )


@dataclass(frozen=True)
class VerifyCommandLookup:
    """Either a runnable command, or the reason there isn't one.

    `problem` is written for the person who has to fix it: it names the
    file, the field, and what was wrong with what it found.
    """

    command: VerifyCommand | None
    problem: str
    # **這個專案的設定檔裡有沒有寫 `verify_command`**，跟它跑不跑得起來
    # 是兩件事。
    #
    # 2026-08-24 之前只有 `command`，於是「從來沒宣告」與「宣告了但執行檔
    # 不見了」收成同一個 `None`。那個合併讓當天修的假綠燈**修不到它要修的
    # 情境**：`.venv` 被刪掉的專案，`command` 一樣是 `None`，於是
    # 「有指令的專案不准退回驗收標準」那一刀問了一句答不出來的話。
    #
    # 同一個病往上跑了一層——**兩件事收成一個值，接的人分不出來**。
    declared_in_file: bool = False

    @property
    def declared(self) -> bool:
        return self.command is not None


def _not_declared(problem: str, *, declared_in_file: bool = False) -> VerifyCommandLookup:
    """跑不了。`declared_in_file` 講的是**他有沒有寫過那一行**。

    預設 `False`，因為前幾種情況（檔案不存在、讀不動、不是設定物件、
    欄位是空的）都真的沒有那一行。**有寫但壞掉的那幾種要自己傳 `True`**
    ——漏傳的後果是驗收標準又變回它的備胎。
    """
    return VerifyCommandLookup(
        command=None, problem=problem, declared_in_file=declared_in_file
    )


def resolve_executable(raw: str, project_root: Path) -> str | None:
    """Turn argv[0] into something that can actually be launched.

    A path-shaped argv[0] is taken as a path (relative ones against the
    project root); a bare name is looked up on PATH here rather than left
    for `CliRunner`, so that 「宣告的工具沒安裝」 also stops the run instead
    of arriving downstream as a failed command - same false-red trap.

    **This delegates to `CliRunner.resolve_tool` and must keep doing so.**
    It used to answer the path branch with its own `Path.is_file()`, and
    two answers to 「找不找得到」 is not a smaller version of one answer -
    it is a way to manufacture a red light for a program that was never
    started. The full account is on `CliRunner.resolve_tool`; the property
    that matters here is that *this function and the runner never
    disagree*, which is only true while there is one implementation.
    `tests/test_the_two_resolvers_agree.py` is what holds it there.
    """
    return CliRunner.resolve_tool(raw, base_dir=project_root)


def _read_timeout(raw: dict) -> tuple[int | None, str]:
    """Seconds, or the reason there is no usable number.

    `math.ceil`, not `int`: `int(0.5)` is 0, `subprocess` reads 0 as 「已經
    逾時了」, and every run of that project would have been killed on the
    spot and reported as a failure. A declaration that is merely *precise*
    must never be silently turned into one that is broken - and rounding
    **up** is the only direction that cannot shorten what was asked for.
    """
    if VERIFY_TIMEOUT_KEY not in raw or raw.get(VERIFY_TIMEOUT_KEY) is None:
        return None, (
            "這個專案沒有宣告驗證逾時，請先設定："
            f"在 {_SETTINGS_LOCATION} 加上 {VERIFY_TIMEOUT_KEY}，"
            "例如 900（秒）。"
            "引擎不會替你猜一個數字——猜短了，逾時會被當成測試失敗，"
            "然後派人去修一個沒壞的東西。"
        )

    value = raw[VERIFY_TIMEOUT_KEY]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value != value  # NaN
        or value == float("inf")
        or value <= 0
    ):
        return None, (
            f"{_SETTINGS_LOCATION} 的 {VERIFY_TIMEOUT_KEY} 格式不對："
            "必須是大於 0 的秒數。"
        )

    return math.ceil(value), ""


def _read_success_rule(raw: dict) -> tuple[SuccessRule | None, str]:
    """The project's 「怎樣算通過」, or the reason it cannot be used.

    Strict on purpose. Every rejection below is a case where being lenient
    produces a **green** light nobody declared: an ignored unknown key
    silently drops a criterion, an empty `exit_codes` list can never match,
    and a missing `match` with two criteria means picking the answer for
    the one case the two connectives disagree about.
    """
    if VERIFY_SUCCESS_KEY not in raw or raw.get(VERIFY_SUCCESS_KEY) is None:
        return None, (
            "這個專案沒有宣告「怎樣算通過」，請先設定："
            f"在 {_SETTINGS_LOCATION} 加上 {VERIFY_SUCCESS_KEY}，"
            f"例如 {_success_example()}。"
            "引擎不會預設「離開碼 0 才算過」——本專案自己的測試就是全過、"
            "離開碼卻非零（QtWebEngine 關閉時崩潰）。"
        )

    declared = raw[VERIFY_SUCCESS_KEY]
    if not isinstance(declared, dict):
        return None, (
            f"{_SETTINGS_LOCATION} 的 {VERIFY_SUCCESS_KEY} 格式不對："
            f"必須是一個設定物件，例如 {_success_example()}。"
        )

    unknown = sorted(
        set(declared) - {EXIT_CODES_KEY, OUTPUT_PATTERN_KEY, MATCH_KEY}
    )
    if unknown:
        return None, (
            f"{_SETTINGS_LOCATION} 的 {VERIFY_SUCCESS_KEY} 有看不懂的欄位："
            f"{unknown}。可用的只有 {EXIT_CODES_KEY}、{OUTPUT_PATTERN_KEY}、"
            f"{MATCH_KEY}。"
            "拼錯的欄位不會被忽略——被忽略等於這條規則悄悄鬆掉一半。"
        )

    exit_codes: tuple[int, ...] | None = None
    if EXIT_CODES_KEY in declared and declared[EXIT_CODES_KEY] is not None:
        codes = declared[EXIT_CODES_KEY]
        if (
            not isinstance(codes, list)
            or not codes
            or any(
                isinstance(code, bool) or not isinstance(code, int)
                for code in codes
            )
        ):
            return None, (
                f"{_SETTINGS_LOCATION} 的 {VERIFY_SUCCESS_KEY}."
                f"{EXIT_CODES_KEY} 格式不對："
                "必須是一串非空的整數，例如 [0] 或 [0, 3221225477]。"
            )
        exit_codes = tuple(codes)

    output_pattern: str | None = None
    if (
        OUTPUT_PATTERN_KEY in declared
        and declared[OUTPUT_PATTERN_KEY] is not None
    ):
        pattern = declared[OUTPUT_PATTERN_KEY]
        if not isinstance(pattern, str) or not pattern.strip():
            return None, (
                f"{_SETTINGS_LOCATION} 的 {VERIFY_SUCCESS_KEY}."
                f"{OUTPUT_PATTERN_KEY} 格式不對："
                "必須是一個非空的正規表示式字串，"
                "例如 \"(?m)^\\\\d+ passed\"。"
            )
        try:
            re.compile(pattern)
        except re.error as error:
            return None, (
                f"{_SETTINGS_LOCATION} 的 {VERIFY_SUCCESS_KEY}."
                f"{OUTPUT_PATTERN_KEY} 不是合法的正規表示式（{error}）。"
            )
        output_pattern = pattern

    if exit_codes is None and output_pattern is None:
        return None, (
            f"{_SETTINGS_LOCATION} 的 {VERIFY_SUCCESS_KEY} 沒有說任何判準："
            f"至少要有 {EXIT_CODES_KEY} 或 {OUTPUT_PATTERN_KEY} 其中一個，"
            f"例如 {_success_example()}。"
        )

    both = exit_codes is not None and output_pattern is not None
    match_value = declared.get(MATCH_KEY)

    if not both:
        if MATCH_KEY in declared:
            return None, (
                f"{_SETTINGS_LOCATION} 的 {VERIFY_SUCCESS_KEY}.{MATCH_KEY} "
                f"只有在同時宣告 {EXIT_CODES_KEY} 與 {OUTPUT_PATTERN_KEY} "
                "時才有意義，請把它拿掉。"
            )
        # One criterion: 「全部成立」 and 「任一成立」 are the same sentence.
        return SuccessRule(exit_codes, output_pattern, match_all=True), ""

    if match_value not in (MATCH_ALL, MATCH_ANY):
        return None, (
            f"同時宣告了 {EXIT_CODES_KEY} 與 {OUTPUT_PATTERN_KEY}，"
            f"就必須在 {_SETTINGS_LOCATION} 的 {VERIFY_SUCCESS_KEY} 加上 "
            f"{MATCH_KEY}：\"{MATCH_ALL}\"（兩個都要成立）或 "
            f"\"{MATCH_ANY}\"（成立一個就算過）。"
            "引擎不會替你決定——這兩個答案剛好在「印了通過樣式、離開碼卻非零」"
            "這個情況上完全相反。"
        )

    return (
        SuccessRule(exit_codes, output_pattern, match_all=match_value == MATCH_ALL),
        "",
    )


def read_verify_command(project_path: Path) -> VerifyCommandLookup:
    """Read `.aicc/project.json` and report how this project is verified."""
    project_root = Path(project_path).expanduser().resolve()
    metadata_path = project_root / ".aicc" / "project.json"

    if not metadata_path.is_file():
        # Same sentence as a project.json without the field, on purpose: the
        # user's next move is identical either way, and 「檔案不存在」 alone
        # would leave them looking for a file instead of writing a setting.
        return _not_declared(
            "這個專案沒有宣告驗證指令，請先設定："
            f"找不到 {_SETTINGS_LOCATION}，"
            f"需要在裡面加上 {VERIFY_COMMAND_KEY}，"
            f"例如 {_COMMAND_EXAMPLE}。"
        )

    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return _not_declared(
            f"讀不到這個專案的驗證指令：{_SETTINGS_LOCATION} 無法解析（{error}）。"
        )

    if not isinstance(raw, dict):
        return _not_declared(
            f"讀不到這個專案的驗證指令：{_SETTINGS_LOCATION} 的內容不是設定物件。"
        )

    declared = raw.get(VERIFY_COMMAND_KEY)
    if declared is None or declared == [] or declared == "":
        return _not_declared(
            "這個專案沒有宣告驗證指令，請先設定："
            f"在 {_SETTINGS_LOCATION} 加上 {VERIFY_COMMAND_KEY}，"
            f"例如 {_COMMAND_EXAMPLE}。"
        )

    if not isinstance(declared, list) or not all(
        isinstance(item, str) and item.strip() for item in declared
    ):
        return _not_declared(
            f"{_SETTINGS_LOCATION} 的 {VERIFY_COMMAND_KEY} 格式不對："
            "必須是一串非空字串（argv），"
            f"例如 {_COMMAND_EXAMPLE}。",
            declared_in_file=True,
        )

    executable = resolve_executable(declared[0], project_root)
    if executable is None:
        return _not_declared(
            f"這個專案宣告的驗證指令跑不起來：找不到 {declared[0]}（"
            f"在 {_SETTINGS_LOCATION} 的 {VERIFY_COMMAND_KEY}）。",
            declared_in_file=True,
        )

    timeout_seconds, timeout_problem = _read_timeout(raw)
    if timeout_seconds is None:
        return _not_declared(timeout_problem, declared_in_file=True)

    success_rule, success_problem = _read_success_rule(raw)
    if success_rule is None:
        return _not_declared(success_problem, declared_in_file=True)

    return VerifyCommandLookup(
        command=VerifyCommand(
            argv=[executable, *declared[1:]],
            timeout_seconds=timeout_seconds,
            success_rule=success_rule,
        ),
        problem="",
    )
