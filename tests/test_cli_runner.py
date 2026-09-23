from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from app.adapters.cli_runner import CliRunner


def _runner(tmp_path: Path) -> CliRunner:
    return CliRunner(tmp_path)


def test_run_captures_stdout_and_success(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    result = runner.run([sys.executable, "-c", "print('hello from cli')"])

    assert result.success is True
    assert result.return_code == 0
    assert "hello from cli" in result.stdout


def test_run_captures_nonzero_exit_code(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    result = runner.run(
        [sys.executable, "-c", "import sys; sys.exit(7)"]
    )

    assert result.success is False
    assert result.return_code == 7


def test_run_reports_tool_not_found(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    result = runner.run(["definitely-not-a-real-command-xyz"])

    assert result.success is False
    assert result.return_code == -1
    assert "not found in PATH" in result.stderr


def test_run_sends_input_text_to_stdin(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    result = runner.run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().strip())"],
        input_text="hello stdin",
    )

    assert result.success is True
    assert result.stdout == "hello stdin"


def test_run_times_out_and_kills_the_process(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    start = time.monotonic()
    result = runner.run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=0.5,
    )
    elapsed = time.monotonic() - start

    assert result.success is False
    assert "timed out" in result.stderr
    # 「死線到了」 has to be legible as itself, not inferred from a sentence
    # in stderr. Every caller downstream that only sees `success=False`
    # treats a timeout as 「測試紅了」 and dispatches somebody to fix code
    # that nothing has found fault with - which is the false red this
    # whole chain exists to remove, one layer lower.
    assert result.timed_out is True
    # Proves the process was actually killed, not just that run() gave up
    # waiting while the child kept running in the background - if the kill
    # didn't happen this would take ~30s instead.
    assert elapsed < 10


def test_a_command_that_finishes_is_never_marked_as_timed_out(
    tmp_path: Path,
) -> None:
    """The negative half: `timed_out` must not be a synonym for 「失敗」.

    A command that ran to the end and returned non-zero is a result, not a
    deadline - and if this flag drifted into meaning 「不成功」 the split
    above would quietly stop separating anything.
    """
    runner = _runner(tmp_path)

    failed = runner.run(
        [sys.executable, "-c", "raise SystemExit(3)"], timeout_seconds=30
    )
    assert failed.success is False
    assert failed.return_code == 3
    assert failed.timed_out is False

    passed = runner.run([sys.executable, "-c", "pass"], timeout_seconds=30)
    assert passed.success is True
    assert passed.timed_out is False


def test_run_uses_working_directory(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    marker = tmp_path / "marker.txt"
    marker.write_text("here", encoding="utf-8")

    result = runner.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; print(Path('marker.txt').is_file())",
        ]
    )

    assert result.stdout.strip() == "True"


def test_terminate_current_returns_false_when_nothing_running(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)

    assert runner.terminate_current() is False


def test_terminate_current_kills_in_flight_process_and_run_returns_promptly(
    tmp_path: Path,
) -> None:
    # This is the actual production scenario: MainWindow.closeEvent (GUI
    # thread) calling terminate_current() while a workflow's run() call is
    # still blocked on a background QThread. Simulate that with a plain
    # background thread instead of Qt machinery - the behavior under test
    # is CliRunner's own thread-safety, not Qt's.
    runner = _runner(tmp_path)
    outcome: dict = {}

    def run_long_command() -> None:
        start = time.monotonic()
        result = runner.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds=60,
        )
        outcome["result"] = result
        outcome["elapsed"] = time.monotonic() - start

    thread = threading.Thread(target=run_long_command)
    thread.start()

    deadline = time.monotonic() + 5
    while runner._current_process is None:
        assert time.monotonic() < deadline, "process never started"
        time.sleep(0.02)

    terminated = runner.terminate_current()
    thread.join(timeout=10)

    assert terminated is True
    assert not thread.is_alive(), "run() never returned after being killed"
    assert outcome["elapsed"] < 10, (
        "run() took as long as the full sleep - the process was not "
        "actually killed, terminate_current() had no effect"
    )
    assert outcome["result"].success is False


def test_terminate_current_is_idempotent_after_process_already_finished(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)

    result = runner.run([sys.executable, "-c", "print('done')"])
    assert result.success is True

    # _current_process is cleared once run() returns - nothing left to
    # terminate, and this must not raise.
    assert runner.terminate_current() is False


# ---------------------------------------------------------------------
# Windows `.cmd` shims: what we send must be what the tool receives.
#
# `claude` and `codex` are npm shims - on Windows `shutil.which` resolves
# them to `claude.CMD` / `codex.CMD`, and Windows cannot execute a batch
# file directly: CreateProcess always hands it to cmd.exe, which parses
# the command line a second time. Anything AICC puts in an argument
# therefore has to survive cmd.exe's parser before the CLI ever sees it,
# and the payloads AICC actually sends are the hostile ones - JSON fix
# instructions full of quotes, prompts containing `%`, review text with
# `|` or `>` in it.
# ---------------------------------------------------------------------

HOSTILE_ARGUMENTS = [
    '{"summary": "fix the thing", "issues": ["a b", "c"]}',
    "%USERPROFILE%",
    "a pipe | in the middle",
    "a redirect > out.txt",
    "an ampersand && echo pwned",
    'he said "hi" twice',
    "trailing backslash\\",
    "",
    "100% done",
    "^caret^ (paren) !bang!",
    "plain",
]


def _echoing_cmd_shim(directory: Path, name: str = "aicc-echo-args") -> None:
    """Write a `.cmd` shim that prints the arguments it received, verbatim,
    as a JSON array - the test double the real `claude.CMD` stands in for.
    """
    script = directory / "_echo_args.py"
    script.write_text(
        "import json, sys\n"
        "sys.stdout.write(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    shim = directory / f"{name}.cmd"
    shim.write_text(
        "@echo off\r\n"
        f'"{sys.executable}" "{script}" %*\r\n',
        encoding="utf-8",
    )


@pytest.mark.skipif(
    os.name != "nt", reason="cmd.exe re-parsing is a Windows-only hazard"
)
def test_cmd_shim_receives_every_argument_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _echoing_cmd_shim(bindir)
    monkeypatch.setenv(
        "PATH", str(bindir) + os.pathsep + os.environ.get("PATH", "")
    )

    runner = CliRunner(workdir)
    result = runner.run(["aicc-echo-args", *HOSTILE_ARGUMENTS])

    assert result.success is True, (
        f"shim did not even run cleanly: rc={result.return_code} "
        f"stderr={result.stderr!r} stdout={result.stdout!r}"
    )
    received = json.loads(result.stdout)
    assert received == HOSTILE_ARGUMENTS


@pytest.mark.skipif(
    os.name != "nt", reason="cmd.exe re-parsing is a Windows-only hazard"
)
def test_cmd_shim_argument_does_not_reach_the_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `>` inside an argument must stay a `>` inside that argument, not
    become a redirection that writes a file into the project directory.
    """
    workdir = tmp_path / "work"
    workdir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _echoing_cmd_shim(bindir)
    monkeypatch.setenv(
        "PATH", str(bindir) + os.pathsep + os.environ.get("PATH", "")
    )

    # No spaces around the metacharacters on purpose: an argument with a
    # space in it happens to get quoted on its way out, which hides the
    # bug. `a>b` does not, and that is the shape review text arrives in.
    runner = CliRunner(workdir)
    result = runner.run(
        ["aicc-echo-args", "write>pwned.txt", "and|this", "and&that"]
    )

    assert result.success is True, result.stderr
    assert json.loads(result.stdout) == [
        "write>pwned.txt",
        "and|this",
        "and&that",
    ]
    stray = sorted(p.name for p in workdir.iterdir())
    assert stray == [], f"shell metacharacters leaked as real files: {stray}"


@pytest.mark.skipif(
    os.name != "nt", reason="cmd.exe re-parsing is a Windows-only hazard"
)
def test_cmd_shim_refuses_a_newline_argument_instead_of_truncating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cmd.exe command line cannot carry a newline - there is no escape
    for one. Silently delivering only the first line (what AICC used to
    do with multi-line prompts) is the worst possible outcome, so this
    has to fail loudly and name the fix.
    """
    workdir = tmp_path / "work"
    workdir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _echoing_cmd_shim(bindir)
    monkeypatch.setenv(
        "PATH", str(bindir) + os.pathsep + os.environ.get("PATH", "")
    )

    runner = CliRunner(workdir)
    result = runner.run(["aicc-echo-args", "line one\nline two"])

    assert result.success is False
    assert result.return_code == -1
    assert "line" in result.stderr.lower()
    assert "input_text" in result.stderr


def test_a_newline_argument_is_refused_for_every_target_not_just_cmd(
    tmp_path: Path,
) -> None:
    """The refusal is not conditioned on the target being a `.cmd` shim,
    and not conditioned on the platform either.

    POSIX `execve` can carry a newline and so can a direct `.exe` launch
    on Windows, so the narrow version of this rule was two rules wearing
    one name: the same call worked on macOS and hard-failed on Windows,
    and on Windows it depended on which file `shutil.which` happened to
    find. A payload with a line break belongs on stdin everywhere, and
    a rule that only fires on one platform is a rule nobody can test on
    the other.
    """
    runner = _runner(tmp_path)

    result = runner.run([sys.executable, "-c", "print('a')\nprint('b')"])

    assert result.success is False
    assert result.return_code == -1
    assert "input_text" in result.stderr
    # ...and the process was never started, so nothing ran half of it.
    assert result.stdout == ""


# ---------------------------------------------------------------------
# Timeouts have to end the whole process tree.
#
# `claude` / `codex` on Windows are cmd.exe -> node. Killing only cmd.exe
# leaves the grandchild holding the stdout/stderr pipes open, so the
# follow-up wait blocks until the grandchild finishes on its own - which
# is exactly what "the timeout never fires" looks like from the outside.
# ---------------------------------------------------------------------

_SPAWNS_A_GRANDCHILD_THAT_HOLDS_THE_PIPE = (
    "import subprocess, sys, time; "
    "subprocess.Popen("
    "[sys.executable, '-c', 'import time; time.sleep(120)'], "
    "stdout=1, stderr=2, close_fds=False); "
    "time.sleep(120)"
)


def test_timeout_kills_the_whole_process_tree(tmp_path: Path) -> None:
    runner = _runner(tmp_path)

    start = time.monotonic()
    result = runner.run(
        [sys.executable, "-c", _SPAWNS_A_GRANDCHILD_THAT_HOLDS_THE_PIPE],
        timeout_seconds=2,
    )
    elapsed = time.monotonic() - start

    assert result.success is False
    assert "timed out" in result.stderr
    # Killing the tree makes this land just past the 2s deadline. The
    # bound is deliberately well under _SECONDS_TO_WAIT_FOR_A_KILLED_
    # PROCESS: if only the direct child were killed, the grandchild would
    # keep the pipes and run() would fall back on that deadline twice
    # over instead - still returning, but ~10x late. Late is a different
    # bug from never, and this test is the one that has to see it.
    assert elapsed < 10, (
        f"run() took {elapsed:.1f}s for a 2s timeout - the grandchild "
        "still held the pipes, so the deadline is being met by the "
        "backstop rather than by actually ending the process tree"
    )


def test_timeout_returns_even_when_the_kill_does_not_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kill is best-effort - taskkill can fail, and a descendant can
    outlive it. The collection step after the kill therefore has its own
    deadline, so the worst case is a late timeout rather than a run()
    that never returns at all (which is what the UI's force-close hit).
    """
    runner = _runner(tmp_path)
    monkeypatch.setattr(
        CliRunner, "_kill_process_tree", staticmethod(lambda process: None)
    )

    start = time.monotonic()
    result = runner.run(
        [sys.executable, "-c", _SPAWNS_A_GRANDCHILD_THAT_HOLDS_THE_PIPE],
        timeout_seconds=1,
    )
    elapsed = time.monotonic() - start

    assert result.success is False
    assert "timed out" in result.stderr
    # Nothing killed the tree, so both children are still sleeping out
    # their 120s. run() must give up on the pipes on its own.
    assert elapsed < 60, (
        f"run() took {elapsed:.1f}s with the kill disabled - the wait "
        "after the kill has no deadline of its own"
    )


# `cmd.exe /d /s /c "..."` 這三個旗標，連續三輪的審稿都點名它們沒有任何測試
# 守著。實測（審稿人做的）：把 `/d` 拿掉，tests/test_cli_runner.py 16 條
# 加 tests/test_the_prompt_never_rides_in_argv.py 5 條，**一條都沒有紅**。
# 一個沒有東西守著的安全設定，下一個人讀起來就是一個可有可無的裝飾。
_AUTORUN_KEY = r"HKCU\Software\Microsoft\Command Processor\AutoRun"

_WHY_EACH_FLAG_IS_THERE = (
    "  /d = 不要執行登錄檔 " + _AUTORUN_KEY + " 裡的命令。少了它，使用者機器上"
    "任何人（安裝程式、同事、惡意軟體）設過的 AutoRun 都會跟著 AICC 的每一次"
    " claude / codex 呼叫一起被執行，而且執行在 AICC 的工作目錄、拿著 AICC 的"
    " stdin/stdout。這是安全界線，不是裝飾。\n"
    "  /s = 只剝掉最外層那一對引號，裡面的參數原封不動。少了它 cmd.exe 會改用"
    "另一套引號規則重新切詞，帶空白或引號的參數就會變形。\n"
    "  /c = 跑完這一句就結束。少了它 cmd.exe 會停在互動提示上，"
    "communicate() 一路等到逾時。"
)


def _cmd_shim_flags(comspec: str) -> list[str]:
    """把 `_cmd_shim_command_line` 組出來的那一行拆出旗標。

    格式是 `{comspec} /d /s /c "{inner}"`。第一個 ` "` 一定是 `/c` 後面那個
    ——comspec 就算帶空白被 list2cmdline 加了引號，那個空白後面接的是字母不是
    引號——所以在那裡切開就拿得到前半段。
    """
    line = CliRunner._cmd_shim_command_line(comspec, ["claude.cmd", "--print"])
    head, separator, _ = line.partition(' "')
    assert separator, (
        f"_cmd_shim_command_line 沒有把命令包在引號裡了：{line!r}。"
        "少了那對引號，cmd.exe 會自己重新切詞，帶空白的路徑就會斷成兩段。"
    )
    return [token for token in head.split() if token.startswith("/")]


def test_the_cmd_shim_keeps_the_three_flags_it_needs() -> None:
    """`/d`、`/s`、`/c` 三個都要在，而且是這個順序。

    這條測試存在的理由是 `/d`：它關掉登錄檔的 AutoRun。cmd.exe 每次啟動都會
    先執行 Command Processor 底下那個 AutoRun 值（HKCU 與 HKLM 各一份）裡的命令，而 AICC
    每一次呼叫 `claude` / `codex` 都是走 `.cmd` 外殼、也就是走 cmd.exe。少了
    `/d`，那些命令就會被注入 AICC 的每一次 CLI 呼叫。

    拿掉任何一個旗標都要有東西變紅——這就是那個「東西」。
    """
    flags = _cmd_shim_flags(r"C:\Windows\system32\cmd.exe")

    assert flags == ["/d", "/s", "/c"], (
        f"cmd.exe 的外殼旗標被改成 {flags} 了。三個都要在、要照 /d /s /c "
        "這個順序：\n" + _WHY_EACH_FLAG_IS_THERE
    )


_SPACED_COMSPEC = r"C:\Program Files\Weird Place\cmd.exe"


def test_the_cmd_shim_survives_a_comspec_with_spaces() -> None:
    """COMSPEC 帶空白的時候，三個旗標還在，而且那個路徑有被引號包住。

    分開一條寫，是因為它守的是不同的東西：上面那條問「三個旗標在不在」，
    這條問「comspec 自己帶空白的時候，這一行還成不成立」。COMSPEC 是使用者
    改得動的環境變數，而 Windows 上「帶空白的程式路徑」是常態不是特例；
    少了那對引號，cmd.exe 會把路徑的第一段當成命令、第二段當成參數，整段
    外殼從第一個字就散掉——連帶那三個旗標也就不再是旗標了。
    """
    line = CliRunner._cmd_shim_command_line(_SPACED_COMSPEC, ["claude.cmd"])
    head = line.partition(' "')[0]

    assert head.startswith('"'), (
        f"帶空白的 COMSPEC 沒有被引號包起來：{line!r}。"
        "cmd.exe 會在第一個空白就把命令切斷，後面的 /d /s /c 也就不再是"
        "旗標了。"
    )

    flags = _cmd_shim_flags(_SPACED_COMSPEC)
    assert flags == ["/d", "/s", "/c"], (
        f"COMSPEC 帶空白的時候旗標變成 {flags}：\n" + _WHY_EACH_FLAG_IS_THERE
    )
