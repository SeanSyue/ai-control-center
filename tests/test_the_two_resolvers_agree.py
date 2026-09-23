"""「這個工具找不找得到」 must have exactly one answer.

There used to be two, and they disagreed on Windows, and the disagreement
did not stay quiet - it came out as a **red light for a program that was
never started**:

    verify_command = ["./notes.txt"]        # the file really is there

    read_verify_command  →  「宣告好了，可以跑」   (Path.is_file() says yes)
    CliRunner.run        →  return_code=-1, 「Command was not found in PATH」
                             (shutil.which consults PATHEXT; .txt is not in it)
    SuccessRule.judge    →  FAILED, 「離開碼 -1 不在宣告的通過清單 [0] 內」

At that point the engine has told everything downstream that the tests are
red without ever executing anything, and `WorkflowWorker` spends the single
paid fix attempt the run gets sending an author to repair a bug that does
not exist. The same disagreement runs the other way too - a declared
`tools/verify` whose real name on disk is `tools/verify.exe` was called
missing, and the user was sent to correct a setting that was already right.

Two mechanisms live here, deliberately overlapping:

1. One resolver. `resolve_executable` delegates to `CliRunner.resolve_tool`,
   and the first test below is what keeps it delegating.
2. `CliResult.never_started`. The second line, for when the first is wrong
   anyway: whenever `CliRunner.run` returns without a process ever having
   existed, the -1 it has to put in `return_code` is a placeholder, and
   `run_tests` refuses to let a placeholder be judged.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.adapters.cli_runner import CliRunner
from app.services.project_verification import (
    VerificationNotDeclared,
    read_verify_command,
    resolve_executable,
)
from tests.fakes import declare_verify_command


def _fixture_tree(tmp_path: Path) -> Path:
    """A project root holding one of every interesting shape."""
    root = tmp_path / "project"
    (root / "tools").mkdir(parents=True)
    # Exists, is readable, and is not an executable extension. This is the
    # exact declaration the reviewer used to manufacture a false red.
    (root / "notes.txt").write_text("not a program", encoding="utf-8")
    # Exists under a name PATHEXT would append, but not under the bare name
    # that a declaration would plausibly use.
    (root / "tools" / "verify.exe").write_text("stub", encoding="utf-8")
    if os.name != "nt":
        (root / "tools" / "verify.exe").chmod(0o755)
    return root


# Every shape a declared argv[0] can take. The point is not what any one of
# them resolves to - it is that both callers say the same thing about it.
RESOLUTION_CASES = [
    "notes.txt",                 # bare name, not on PATH
    "./notes.txt",               # path-shaped, exists, not executable
    "tools/verify",              # path-shaped, only .exe exists (PATHEXT)
    "tools/verify.exe",          # path-shaped, exact name on disk
    "tools/missing.exe",         # path-shaped, nothing there
    "tools",                     # path-shaped, but it is a directory
    sys.executable,              # absolute, real interpreter
    "a-tool-nobody-has-installed-9f3c",   # bare name, not on PATH
]


@pytest.mark.parametrize("declared", RESOLUTION_CASES)
def test_the_two_resolvers_give_the_same_answer(
    tmp_path: Path, declared: str
) -> None:
    """The invariant that had no name, no test, and not even a comment.

    Nothing in the codebase used to say these two had to agree, so they
    quietly stopped agreeing, and the shape of the disagreement was a
    FAILED verdict on a program that never ran. Assert the answers are
    identical - not merely both truthy - because the resolved string is
    what becomes argv[0], and two different strings are two different
    programs.
    """
    root = _fixture_tree(tmp_path)

    reader_answer = resolve_executable(declared, root)
    runner_answer = CliRunner.resolve_tool(declared, base_dir=root)

    assert reader_answer == runner_answer


def test_resolution_is_idempotent(tmp_path: Path) -> None:
    """The reader hands its answer to the runner, which resolves it again.

    `read_verify_command` puts an already-resolved absolute path into
    argv[0]; `CliRunner.run` then resolves argv[0] a second time. Agreeing
    on the *declared* string is not enough if the second pass can change
    its mind about the resolved one - that round trip is where the false
    red actually happened.
    """
    root = _fixture_tree(tmp_path)

    for declared in RESOLUTION_CASES:
        once = resolve_executable(declared, root)
        if once is None:
            continue
        assert CliRunner.resolve_tool(once) == once, declared


def test_an_extension_that_cannot_be_executed_is_not_found(
    tmp_path: Path,
) -> None:
    """`Path.is_file()` is not the question. 「跑得起來嗎」 is the question.

    On Windows a `.txt` is not in PATHEXT, so `cmd.exe` would not run it
    and neither will we. On POSIX the file has no execute bit. Either way
    the honest answer is 「找不到可以執行的東西」 - given *before* anything
    downstream can mistake a made-up exit code for a test result.
    """
    root = _fixture_tree(tmp_path)

    assert resolve_executable("./notes.txt", root) is None
    assert CliRunner.resolve_tool("./notes.txt", base_dir=root) is None


def test_a_path_branch_tries_the_pathext_suffixes_too(
    tmp_path: Path,
) -> None:
    """The other direction of the same disagreement (last round's low 7).

    A declared `tools/verify` with `tools/verify.exe` on disk is a tool
    that really does run. `is_file()` said no and stopped the run to
    demand a setting change that would have changed nothing.
    """
    root = _fixture_tree(tmp_path)

    resolved = resolve_executable("tools/verify", root)

    if os.name == "nt":
        assert resolved is not None
        assert Path(resolved).name.lower() == "verify.exe"
    else:
        # No PATHEXT off Windows - `tools/verify` names a file that is not
        # there, and both resolvers have to say so together.
        assert resolved is None
    assert resolved == CliRunner.resolve_tool("tools/verify", base_dir=root)


def test_a_relative_declaration_still_resolves_against_the_project(
    tmp_path: Path,
) -> None:
    """Sharing a resolver must not cost the project-root anchoring.

    `shutil.which("./x")` resolves against the *AICC process's* working
    directory, which is not the project. The `base_dir` argument is the
    half of the agreement that keeps `.venv\\Scripts\\python.exe` meaning
    *this project's* venv, and a declaration that only works when the app
    was launched from the project root is a trap.
    """
    root = _fixture_tree(tmp_path)

    resolved = resolve_executable("tools/verify.exe", root)

    assert resolved == str(root / "tools" / "verify.exe")
    # And without the anchor it is not found at all, which is what proves
    # the anchor is doing the work rather than the cwd happening to match.
    assert CliRunner.resolve_tool("tools/verify.exe") is None


# ---- 第二道防線：根本沒啟動起來 -------------------------------------------


def test_an_unrunnable_declaration_stops_the_run_instead_of_reading_red(
    tmp_path: Path,
) -> None:
    """The reviewer's end-to-end case, at the reader.

    Declaring a file that exists but cannot be executed now ends where an
    undeclared command ends - 「請先設定」 - rather than at a FAILED verdict
    the engine invented for a program it never launched.
    """
    root = _fixture_tree(tmp_path)
    declare_verify_command(root, argv=["./notes.txt"])

    lookup = read_verify_command(root)

    assert not lookup.declared
    assert "notes.txt" in lookup.problem


def test_a_tool_that_cannot_be_found_says_it_never_started() -> None:
    runner = CliRunner(Path.cwd())

    result = runner.run(["a-tool-nobody-has-installed-9f3c"])

    assert result.never_started is True
    assert result.return_code == -1


def test_an_argument_a_command_line_cannot_carry_never_started() -> None:
    """The refusal is not a failed run either.

    A multi-line argument is refused before `Popen`, so nothing executed.
    Without the flag its manufactured -1 is indistinguishable, downstream,
    from a suite that ran and came back red.
    """
    runner = CliRunner(Path.cwd())

    result = runner.run([sys.executable, "-c", "print(1)\nprint(2)"])

    assert result.never_started is True
    assert "standard input" in result.stderr


def test_a_popen_that_refuses_to_launch_never_started(monkeypatch) -> None:
    runner = CliRunner(Path.cwd())

    def refuse(*args, **kwargs):
        raise OSError(8, "Exec format error")

    monkeypatch.setattr(subprocess, "Popen", refuse)

    result = runner.run([sys.executable, "-c", "pass"])

    assert result.never_started is True
    assert result.return_code == -1


def test_a_command_that_never_started_is_never_judged(
    tmp_path: Path, monkeypatch
) -> None:
    """The flag has to change what `run_tests` does, not just be recorded.

    This is the assertion that would have caught the original bug: with a
    `verify_success` of `{"exit_codes": [0]}`, judging the placeholder -1
    yields FAILED and a perfectly readable-sounding reason. `run_tests`
    must raise `VerificationNotDeclared` instead, which is the 「停下來問」
    ending - no verdict, no author dispatched, no paid call.
    """
    from app.adapters.cli_runner import CliResult
    from app.core.orchestrator import Orchestrator

    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    declare_verify_command(project, argv=[sys.executable, "-c", "pass"])

    class NeverStartsRunner:
        def __init__(self, working_directory: Path) -> None:
            self.working_directory = working_directory

        def run(self, arguments, timeout_seconds=300, input_text=None):
            return CliResult(
                command="pytest",
                success=False,
                return_code=-1,
                never_started=True,
                stdout="",
                stderr="Command was not found in PATH: pytest",
            )

    monkeypatch.setattr("app.core.orchestrator.CliRunner", NeverStartsRunner)
    orchestrator = Orchestrator(project)

    with pytest.raises(VerificationNotDeclared) as raised:
        orchestrator.run_tests()

    # And the sentence is actionable: which field to look at, and an
    # explicit denial that anything was found wrong with the code.
    assert "verify_command" in raised.value.reason
    assert "不是測試失敗" in raised.value.reason
