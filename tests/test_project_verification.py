"""The project declares how it is verified; AICC never guesses.

These are about one distinction: 「沒得驗」 is not 「驗了但是紅的」. Everything
below is either the reading of a declaration, or a refusal to invent one.

Three sentences are declared, and each of them used to be the engine's:
跑什麼 (`verify_command`), 等多久 (`verify_timeout_seconds`), and - added
2026-08-21 - 怎樣算過 (`verify_success`). The last two are here because the
first one alone did not remove the false red: the engine still answered
「算不算過」 with `returncode == 0`, and still answered 「等多久」 with a
made-up 120.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.adapters.cli_runner import CliResult
from app.services.project_verification import (
    TESTS_FAILED,
    TESTS_PASSED,
    combined_output,
    read_verify_command,
)


PASSES_ON_ZERO = {"exit_codes": [0]}


def _project(tmp_path: Path, metadata: dict | None = None) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    if metadata is not None:
        (project / ".aicc" / "project.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return project


def _declared(tmp_path: Path, **overrides) -> Path:
    """A project with every required sentence declared, then overridden."""
    metadata = {
        "version": 1,
        "verify_command": [sys.executable, "-m", "pytest", "-q"],
        "verify_timeout_seconds": 900,
        "verify_success": dict(PASSES_ON_ZERO),
    }
    metadata.update(overrides)
    for key in [key for key, value in metadata.items() if value is _ABSENT]:
        del metadata[key]
    return _project(tmp_path, metadata)


_ABSENT = object()


def test_declared_command_is_returned_verbatim(tmp_path: Path) -> None:
    project = _declared(tmp_path)

    lookup = read_verify_command(project)

    assert lookup.declared
    assert lookup.problem == ""
    assert lookup.command.argv == [sys.executable, "-m", "pytest", "-q"]
    assert lookup.command.timeout_seconds == 900


def test_a_project_json_written_before_this_field_existed_still_reads(
    tmp_path: Path,
) -> None:
    """The compatibility promise, stated as a test.

    Every project.json on disk today predates `verify_command`. Reading one
    has to be an ordinary 「還沒宣告」 answer - not an exception, not a
    corrupt-file path, and above all not a made-up default that would send
    the run straight back into the bug this replaced.
    """
    project = _project(
        tmp_path,
        {
            "version": 1,
            "project_id": "abc",
            "name": "legacy",
            "sensitivity": "personal",
        },
    )

    lookup = read_verify_command(project)

    assert not lookup.declared
    assert lookup.command is None
    assert "verify_command" in lookup.problem


def test_a_relative_interpreter_resolves_against_the_project_not_the_cwd(
    tmp_path: Path,
) -> None:
    """`.venv\\Scripts\\python.exe` has to mean *this project's* venv.

    `CliRunner` resolves argv[0] with `shutil.which`, which resolves a
    relative path against the AICC **process's** working directory - some
    other folder entirely. A declaration that only works when the app
    happens to be launched from the project root is a trap, and a relative
    declaration is the only form that survives being cloned to another
    machine, so it has to be the form that works.
    """
    project = _declared(tmp_path, verify_command=["tools/verify.exe"])
    interpreter = project / "tools" / "verify.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("not really an exe", encoding="utf-8")

    lookup = read_verify_command(project)

    assert lookup.declared
    assert lookup.command.argv == [str(interpreter)]


def test_a_declared_interpreter_that_is_not_there_is_not_a_red_light(
    tmp_path: Path,
) -> None:
    """The original bug, one layer down.

    A declared-but-missing interpreter reaching `CliRunner` comes back as
    「Command was not found in PATH」 with return code -1 - which the caller
    reads as 「測試失敗」 and hands to the author as a bug in the code. It is
    not: nobody has verified anything. Same answer as no declaration at all.
    """
    project = _declared(
        tmp_path,
        verify_command=[".venv/Scripts/python.exe", "-m", "pytest"],
    )

    lookup = read_verify_command(project)

    assert not lookup.declared
    assert ".venv/Scripts/python.exe" in lookup.problem


def test_a_command_that_is_not_argv_is_refused_rather_than_split(
    tmp_path: Path,
) -> None:
    """A shell string is rejected, not guessed at.

    Splitting `"C:\\Program Files\\py\\python.exe -m pytest"` on spaces
    produces a command that runs the wrong thing; splitting it "cleverly"
    means picking one of several incompatible quoting dialects. Refusing
    costs the user one edit and cannot silently run something else.
    """
    project = _declared(
        tmp_path, verify_command=f"{sys.executable} -m pytest -q"
    )

    lookup = read_verify_command(project)

    assert not lookup.declared
    assert "argv" in lookup.problem


def test_a_project_json_that_cannot_be_parsed_says_so(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / ".aicc" / "project.json").write_text("{ broken", encoding="utf-8")

    lookup = read_verify_command(project)

    assert not lookup.declared
    assert "project.json" in lookup.problem


# ---- 等多久 ---------------------------------------------------------------


def test_an_undeclared_timeout_stops_the_run_instead_of_inventing_one(
    tmp_path: Path,
) -> None:
    """`DEFAULT_VERIFY_TIMEOUT_SECONDS = 120` was the one invented default.

    It was also the one that bites. A timeout used to arrive downstream as
    `success=False`, which is the false red again - so a Node/Go/monorepo
    suite that runs for three minutes got 「測試失敗」 and an author sent to
    fix code nothing had found fault with. This repo's own suite takes 138
    seconds and only escaped by declaring 900; the next project would not
    have been so lucky.

    Undeclared is now the same answer as an undeclared command: stop and
    ask. The module must not be able to name a number here.
    """
    project = _declared(tmp_path, verify_timeout_seconds=_ABSENT)

    lookup = read_verify_command(project)

    assert not lookup.declared
    assert lookup.command is None
    assert "verify_timeout_seconds" in lookup.problem
    # And the sentence has to be actionable: which file, which field, and
    # what a plausible value looks like.
    assert ".aicc" in lookup.problem
    assert "900" in lookup.problem


def test_a_broken_timeout_stops_the_run(tmp_path: Path) -> None:
    for broken in (0, -5, "900", True, None):
        project = _declared(
            tmp_path / f"case-{broken!r}", verify_timeout_seconds=broken
        )
        lookup = read_verify_command(project)
        assert not lookup.declared, broken
        assert "verify_timeout_seconds" in lookup.problem


def test_half_a_second_rounds_up_instead_of_truncating_to_zero(
    tmp_path: Path,
) -> None:
    """`int(0.5) == 0`, and `subprocess` reads 0 as 「已經逾時了」.

    A project that declared `0.5` - a perfectly meaningful number, and the
    only shape a sub-second declaration can take - used to be handed a
    timeout of **zero**: every run killed the instant it started, reported
    as a failure, author dispatched. The field's own rule is 「壞掉的
    timeout 要停下來，不准默默用預設」, and this value was not broken; it
    was broken *by the reader*.

    Rounding up, not down: shortening what the project asked for is the
    direction that manufactures timeouts.
    """
    project = _declared(tmp_path, verify_timeout_seconds=0.5)

    lookup = read_verify_command(project)

    assert lookup.declared
    assert lookup.command.timeout_seconds == 1

    longer = _declared(tmp_path / "b", verify_timeout_seconds=129.2)
    assert read_verify_command(longer).command.timeout_seconds == 130


# ---- 怎樣算過 -------------------------------------------------------------


def test_an_undeclared_success_rule_stops_the_run(tmp_path: Path) -> None:
    """The engine has no opinion about what 「通過」 means.

    `returncode == 0` was the leftover: `verify_command` moved 「跑什麼」 to
    the project and left 「算不算過」 in the engine. This repo is the proof
    that the leftover is wrong - its suite prints 「1224 passed」 and exits
    0xC0000005 - and `go test`, wrapper scripts that always exit 0, and
    linters that report through their output are the same shape.
    """
    project = _declared(tmp_path, verify_success=_ABSENT)

    lookup = read_verify_command(project)

    assert not lookup.declared
    assert lookup.command is None
    assert "verify_success" in lookup.problem
    assert ".aicc" in lookup.problem
    # Both expressible forms are named, because a user who has just been
    # stopped needs the shape, not the field name.
    assert "exit_codes" in lookup.problem
    assert "output_pattern" in lookup.problem


def test_exit_codes_declared_by_the_project_decide(tmp_path: Path) -> None:
    """「這些離開碼算過」 - including the one this repo actually returns."""
    project = _declared(
        tmp_path,
        verify_success={"exit_codes": [0, 3221225477, 139]},
    )

    rule = read_verify_command(project).command.success_rule

    assert rule.judge(0, "1224 passed").status == TESTS_PASSED
    # The QtWebEngine teardown fault, on both the values it shows up as.
    assert rule.judge(3221225477, "1224 passed").status == TESTS_PASSED
    assert rule.judge(139, "1224 passed").status == TESTS_PASSED
    assert rule.judge(1, "3 failed").status == TESTS_FAILED
    # The verdict says which rule decided it, in a sentence a person can act
    # on. 「測試失敗」 on its own is what nobody could act on.
    assert "3221225477" in rule.judge(3221225477, "x").reason


def test_an_output_pattern_declared_by_the_project_decides(
    tmp_path: Path,
) -> None:
    """「輸出符合這個樣式算過」, whatever the exit code was.

    This is the declaration this repo itself uses, and the one its own
    CLAUDE.md has always specified in prose: 「全過也會回非零 exit code，看
    最後一行不要看 exit code」. The engine used to do the exact opposite.
    """
    project = _declared(
        tmp_path,
        verify_success={"output_pattern": r"(?m)^\d+ passed in [\d.]+s\s*$"},
    )

    rule = read_verify_command(project).command.success_rule

    # Exit code 139 / 0xC0000005, summary line present: this is a pass.
    assert rule.judge(139, "1224 passed in 129.22s\n").status == TESTS_PASSED
    assert (
        rule.judge(3221225477, "72 passed in 13.87s\r\n").status
        == TESTS_PASSED
    )
    # Exit code 0 but the summary says otherwise: this is not.
    assert (
        rule.judge(0, "3 failed, 1221 passed in 129.22s").status
        == TESTS_FAILED
    )
    # Crashed before printing anything: also not. An exit-code rule that
    # allowed 139 would have called this a pass.
    assert rule.judge(139, "").status == TESTS_FAILED


def test_a_pattern_is_matched_against_both_streams(tmp_path: Path) -> None:
    """Which stream a runner summarises on is the runner's business.

    pytest prints its summary to stdout; plenty of Node tools print theirs
    to stderr. A project should not have to know, and should not have its
    declaration break the day a tool changes its mind.
    """
    project = _declared(
        tmp_path, verify_success={"output_pattern": r"Tests:\s+\d+ passed"}
    )
    rule = read_verify_command(project).command.success_rule

    on_stderr = CliResult(
        command="jest",
        success=False,
        return_code=0,
        stdout="",
        stderr="Tests:       12 passed, 12 total",
    )

    assert rule.judge(0, combined_output(on_stderr)).status == TESTS_PASSED


def test_declaring_both_criteria_requires_saying_how_they_combine(
    tmp_path: Path,
) -> None:
    """`match` is required, not defaulted - the two answers really differ.

    「印了通過樣式但離開碼非零」 is exactly the case this repo produces, and
    `any` calls it a pass while `all` calls it a failure. Picking one for
    the project would be the engine inventing half the criterion, which is
    the thing this field exists to stop.
    """
    ambiguous = _declared(
        tmp_path / "ambiguous",
        verify_success={"exit_codes": [0], "output_pattern": r"\d+ passed"},
    )
    lookup = read_verify_command(ambiguous)
    assert not lookup.declared
    assert "match" in lookup.problem
    assert "all" in lookup.problem and "any" in lookup.problem

    strict = _declared(
        tmp_path / "all",
        verify_success={
            "exit_codes": [0],
            "output_pattern": r"\d+ passed",
            "match": "all",
        },
    )
    strict_rule = read_verify_command(strict).command.success_rule
    assert strict_rule.judge(0, "9 passed").status == TESTS_PASSED
    assert strict_rule.judge(139, "9 passed").status == TESTS_FAILED

    lenient = _declared(
        tmp_path / "any",
        verify_success={
            "exit_codes": [0],
            "output_pattern": r"\d+ passed",
            "match": "any",
        },
    )
    lenient_rule = read_verify_command(lenient).command.success_rule
    assert lenient_rule.judge(139, "9 passed").status == TESTS_PASSED
    assert lenient_rule.judge(139, "boom").status == TESTS_FAILED


def test_the_same_declaration_works_for_node_and_go(tmp_path: Path) -> None:
    """Not a Python field wearing a general name.

    A Node runner behind a wrapper that always exits 0 can only be judged
    by its output; `go test ./...` exits 1 for a build error and for a real
    failure alike, and 「ok」 on stdout is what separates them from a pass.
    Both are expressible without the engine knowing either ecosystem.
    """
    node = _declared(
        tmp_path / "node",
        verify_success={"output_pattern": r"(?m)^Tests:.*\b0 failed\b"},
    )
    node_rule = read_verify_command(node).command.success_rule
    assert (
        node_rule.judge(0, "Tests: 31 passed, 0 failed").status
        == TESTS_PASSED
    )
    assert (
        node_rule.judge(0, "Tests: 29 passed, 2 failed").status
        == TESTS_FAILED
    )

    go = _declared(
        tmp_path / "go",
        verify_success={
            "exit_codes": [0],
            "output_pattern": r"(?m)^ok\s",
            "match": "all",
        },
    )
    go_rule = read_verify_command(go).command.success_rule
    assert go_rule.judge(0, "ok  \texample/pkg\t0.4s").status == TESTS_PASSED
    assert (
        go_rule.judge(1, "FAIL\texample/pkg [build failed]").status
        == TESTS_FAILED
    )


@pytest.mark.parametrize(
    "broken, expected_in_problem",
    [
        ({}, "exit_codes"),
        ({"exit_codes": []}, "exit_codes"),
        ({"exit_codes": [0, "1"]}, "exit_codes"),
        ({"exit_codes": [True]}, "exit_codes"),
        ({"exit_codes": 0}, "exit_codes"),
        ({"output_pattern": ""}, "output_pattern"),
        ({"output_pattern": "(unclosed"}, "output_pattern"),
        ({"output_pattern": 7}, "output_pattern"),
        ({"exit_code": [0]}, "看不懂的欄位"),
        ({"exit_codes": [0], "match": "all"}, "match"),
        # The dangerous typo: a misspelt `output_pattern` beside a valid
        # `exit_codes` still parses as a perfectly good rule if unknown
        # keys are ignored - so the project believes it declared 「看輸出」
        # and the engine is quietly judging on the exit code alone. That is
        # an undeclared **green**, and it is why the check has to refuse
        # rather than warn.
        ({"exit_codes": [0], "outputPattern": "passed"}, "outputPattern"),
        ("exit_codes=[0]", "verify_success"),
    ],
)
def test_a_success_rule_that_cannot_be_trusted_stops_the_run(
    tmp_path: Path, broken, expected_in_problem: str
) -> None:
    """Every rejection here would otherwise produce an undeclared **green**.

    A typo'd `exit_code` silently drops the whole criterion; an empty
    `exit_codes` can never match; a `match` with only one criterion means
    the author thinks they declared two. Leniency in this field does not
    cost a red light, it costs the light being meaningless - which is the
    one failure mode worse than a false red.
    """
    project = _declared(
        tmp_path / str(abs(hash(str(broken)))), verify_success=broken
    )

    lookup = read_verify_command(project)

    assert not lookup.declared
    assert expected_in_problem in lookup.problem


# ---- 本專案自己的宣告 ------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[1]

# Real last lines, copied verbatim from runs of this repo's own suite - not
# invented. The `(0:02:05)` suffix is the one pytest adds once a run passes
# 60 seconds, and it is here because the first version of this repo's
# declared pattern did not allow it: the full suite (125s) would have been
# judged FAILED while entirely green. That is the exact false red this whole
# field exists to remove, reintroduced by the declaration meant to remove
# it - which is why the repo's own rule needs a test and not just a review.
REAL_GREEN_TAILS = [
    "72 passed in 13.87s\r\n",
    "1224 passed in 129.22s\r\n",
    "1249 passed in 125.87s (0:02:05)\r\n",
]
REAL_RED_TAILS = [
    "1 failed, 1248 passed in 125.52s (0:02:05)\r\n",
    "3 failed, 1221 passed in 129.22s\r\n",
    # 零跳過 is a rule of this project (CLAUDE.md), so a skip is not a pass.
    "1248 passed, 1 skipped in 125.87s (0:02:05)\r\n",
    # Collection error: pytest never prints a summary line at all.
    "ERROR tests/test_x.py\r\n1 error in 0.28s\r\n",
    # Crashed before printing anything - an exit-code rule allowing 139
    # would have called this a pass.
    "",
]


def test_this_repo_declares_how_it_is_verified() -> None:
    """AICC's first real run is against AICC. Its own declaration must work.

    Not a self-referential curiosity: this repo is the reason the field
    exists. Its suite prints 「N passed」 and then exits 0xC0000005 because
    QtWebEngine faults while tearing down, and its own CLAUDE.md has always
    said 「看最後一行不要看 exit code」 while the engine did the opposite.
    """
    lookup = read_verify_command(REPO_ROOT)

    assert lookup.declared, lookup.problem
    assert lookup.command.argv[1:] == ["-m", "pytest", "-q"]
    # 138 seconds is what the full suite actually takes; the declaration has
    # to be its own, because the engine no longer has a number to fall back
    # on and 「跑到一半被砍」 would not be a red light either.
    assert lookup.command.timeout_seconds >= 300


def test_the_summary_line_survives_a_page_of_chromium_noise() -> None:
    r"""`combined_output` joins the two streams with a **newline**.

    Load-bearing, and it was holding up nothing but a habit: change the
    `"\n".join` to `"".join` and 127 tests stay green, while this repo's
    own green light silently turns red. The mechanism, end to end:

    * `CliRunner` calls `.strip()` on both streams, so the pytest summary
      line - the last line of stdout - arrives with **no trailing
      newline**.
    * This repo's declared pattern is anchored to a line end (`...\s*$`
      under `(?m)`).
    * Its stderr is never empty: QtWebEngine restarts its GPU process and
      Chromium logs about it on every single run of the suite.

    Joined directly, 「1264 passed in 124.10s (0:02:04)」 becomes
    「1264 passed in 124.10s (0:02:04)[30012:...ERROR:...」, `$` no longer
    matches, and a completely green 1264-test run reports FAILED - the
    exact false red the whole verify_success field exists to remove.

    Written against this repo's own declaration and its own real output so
    that what it guards is what actually happens on this machine.
    """
    rule = read_verify_command(REPO_ROOT).command.success_rule

    result = CliResult(
        command="pytest",
        success=False,
        return_code=3221225477,  # 0xC0000005: QtWebEngine faults on teardown
        # Both streams non-empty, and stdout ends at the summary line with
        # no trailing newline - exactly what CliRunner.run() hands over
        # after stripping.
        stdout=(
            "tests/test_web_panel_theme.py ....\r\n"
            "\r\n1264 passed in 124.10s (0:02:04)"
        ),
        stderr=(
            "[30012:23456:0821/013825.114:ERROR:gpu_process_host.cc(993)] "
            "GPU process exited unexpectedly: exit_code=15\r\n"
            "[30012:23456:0821/013825.115:ERROR:shared_context_state.cc(83)] "
            "Failed to make current since context is marked as lost"
        ),
    )

    assert rule.judge(
        result.return_code, combined_output(result)
    ).status == TESTS_PASSED

    # And the reason it passes is the separator, not luck: glue the two
    # streams together the way a "simplification" would and the same
    # output stops matching.
    glued = result.stdout + result.stderr
    assert rule.judge(result.return_code, glued).status == TESTS_FAILED


def test_this_repos_own_rule_reads_the_summary_line_not_the_exit_code() -> None:
    rule = read_verify_command(REPO_ROOT).command.success_rule

    for tail in REAL_GREEN_TAILS:
        # 3221225477 == 0xC0000005 on native Windows, 139 under Git Bash.
        for crashed in (0, 139, 3221225477):
            assert rule.judge(crashed, tail).status == TESTS_PASSED, (
                crashed,
                tail,
            )

    for tail in REAL_RED_TAILS:
        # Exit code 0 this time: the summary alone has to be able to say no,
        # or the rule is just `returncode == 0` wearing a pattern.
        assert rule.judge(0, tail).status == TESTS_FAILED, tail
