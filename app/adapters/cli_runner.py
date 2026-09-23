from __future__ import annotations

import locale
import os
import shutil
import subprocess

from app.adapters.no_console import NO_WINDOW
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _names_a_path(raw: str) -> bool:
    """Does this argv[0] name a location, rather than a tool on PATH?

    Matched to `shutil.which`'s own test (`os.path.split` produced a
    directory part) so that the two branches of `resolve_tool` divide the
    world exactly the way `which` does internally.
    """
    return os.path.isabs(raw) or os.path.dirname(raw) != ""


@dataclass
class CliResult:
    command: str
    success: bool
    return_code: int
    stdout: str
    stderr: str
    session_id: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    raw_stdout: str = ""
    # 「死線到了，我把它砍了」 - which is not 「它跑完了，結果是壞的」.
    # `success=False` alone cannot carry that difference, and the caller
    # that cannot see it sends somebody off to fix code nobody has found
    # fault with. Every other CliResult leaves this False.
    timed_out: bool = False
    # 「它根本沒有跑起來」 - which is also not 「它跑完了，結果是壞的」.
    #
    # Same shape of bug as `timed_out`, one step earlier: when the tool
    # cannot be resolved, when Popen refuses to launch it, or when an
    # argument cannot be carried on a command line, nothing was ever
    # executed. The only honest `return_code` for that is a made-up -1,
    # and a made-up -1 fed to a project's 「怎樣算通過」 rule comes back as
    # 「離開碼 -1 不在宣告的通過清單 [0] 內」 - a red light for code that
    # was never run, followed by a paid call sending an author to fix a
    # bug that does not exist. A caller that can see this flag stops and
    # asks instead; one that cannot, cannot tell the difference.
    never_started: bool = False


class CliRunner:
    def __init__(self, working_directory: Path) -> None:
        self.working_directory = working_directory.resolve()

        if not self.working_directory.is_dir():
            raise ValueError(
                f"Working directory does not exist: {self.working_directory}"
            )

        # Guards _current_process: run() sets/clears it around the
        # subprocess it spawns, and terminate_current() reads it - from a
        # DIFFERENT thread in the real app (MainWindow.closeEvent on the
        # GUI thread, terminating a `claude`/`codex` call still in flight
        # on a WorkflowWorker's background QThread).
        self._process_lock = threading.Lock()
        self._current_process: subprocess.Popen | None = None

    @staticmethod
    def resolve_tool(
        tool_name: str, base_dir: Path | str | None = None
    ) -> str | None:
        """**The** answer to 「這個工具找不找得到」. There must be only one.

        There used to be two. `project_verification.resolve_executable`
        answered a path-shaped argv[0] with `Path.is_file()`; this method
        answered it with `shutil.which`, which on Windows consults PATHEXT
        and therefore disagrees - in both directions, and both directions
        hurt:

        * `is_file()` says yes, `which` says no (a declared `./notes.txt`:
          the file is there, but `.txt` is not in PATHEXT). The reader
          reported 「宣告好了，可以跑」 and the runner then reported
          「Command was not found in PATH」 with a *manufactured* return
          code of -1, which the project's success rule read as 「離開碼 -1
          不在通過清單內」 → **FAILED**. Nothing was ever executed and the
          workflow was about to spend its one paid fix attempt on a bug
          that does not exist.
        * `is_file()` says no, `which` says yes (a declared `tools/verify`
          where `tools/verify.exe` is what is on disk). The reader stops
          the run and sends the user off to correct a setting that was
          already right.

        So both callers come here. `base_dir` is the other half of the
        agreement: a relative path is resolved against the project root
        when the caller knows it, because `shutil.which` would resolve it
        against the *AICC process's* working directory - some other folder
        entirely - and a declaration that only works when the app happens
        to be launched from the project root is a trap.

        Resolution is idempotent: feeding the returned path back in
        returns it unchanged. That is what lets the reader hand its
        resolved argv[0] to `run()` without the answer changing on the way.
        """
        if not _names_a_path(tool_name):
            return shutil.which(tool_name)

        candidate = Path(tool_name).expanduser()
        if not candidate.is_absolute():
            # `Path("./x")` normalises to `x`, which `shutil.which` would
            # then look for on PATH. Anchor it before that can happen.
            root = Path(base_dir) if base_dir is not None else Path.cwd()
            candidate = root / candidate
        # Deliberately still `shutil.which`, not an `is_file()` of our
        # own: it is the same function the bare-name branch above uses,
        # so 「找不找得到」 cannot mean two things depending on whether the
        # declaration happened to contain a slash. Given a path it checks
        # that one directory, applying PATHEXT on Windows exactly as
        # cmd.exe would.
        return shutil.which(str(candidate))

    @classmethod
    def tool_exists(cls, tool_name: str, base_dir: Path | str | None = None) -> bool:
        return cls.resolve_tool(tool_name, base_dir) is not None

    def run(
        self,
        arguments: list[str],
        timeout_seconds: int | float | None = 300,
        input_text: str | None = None,
    ) -> CliResult:
        if not arguments:
            raise ValueError("Command arguments cannot be empty.")

        resolved_tool = self.resolve_tool(arguments[0])

        if resolved_tool is None:
            return CliResult(
                command=subprocess.list2cmdline(arguments),
                success=False,
                return_code=-1,
                never_started=True,
                stdout="",
                stderr=f"Command was not found in PATH: {arguments[0]}",
            )

        display_command = subprocess.list2cmdline(arguments)
        suffix = Path(resolved_tool).suffix.lower()
        input_bytes = (
            input_text.encode("utf-8")
            if input_text is not None
            else None
        )
        # Matches subprocess.run's own behavior: stdin is only replaced
        # with a pipe when there's actually input to send. Piping stdin
        # unconditionally would change how a CLI that checks
        # sys.stdin.isatty() behaves even for calls that pass no input.
        stdin = subprocess.PIPE if input_bytes is not None else None

        popen_kwargs: dict[str, Any] = {"shell": False}
        if os.name != "nt":
            # Give the child its own process group so a kill can take the
            # whole tree without also killing us - see _kill_process_tree.
            popen_kwargs["start_new_session"] = True

        popen_args: Any = [resolved_tool, *arguments[1:]]

        # Checked for every target on every platform, deliberately - see
        # _argument_a_command_line_cannot_carry. The narrow version of
        # this check (Windows + `.cmd` only) let the same call succeed on
        # macOS and hard-fail on Windows, which turns one product into
        # two, and it let the rule be broken everywhere the tests looked.
        unsendable = self._argument_a_command_line_cannot_carry(popen_args)
        if unsendable is not None:
            return CliResult(
                command=display_command,
                success=False,
                return_code=-1,
                never_started=True,
                stdout="",
                stderr=unsendable,
            )

        if os.name == "nt" and suffix in {".cmd", ".bat"}:
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            popen_args = self._cmd_shim_command_line(comspec, popen_args)
            popen_kwargs["executable"] = comspec

        try:
            process = subprocess.Popen(
                popen_args,
                cwd=self.working_directory,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=NO_WINDOW,
                **popen_kwargs,
            )
        except OSError as exc:
            return CliResult(
                command=display_command,
                success=False,
                return_code=-1,
                never_started=True,
                stdout="",
                stderr=str(exc),
            )

        # Built with Popen (not subprocess.run) specifically so
        # terminate_current() has a live process handle to act on while
        # this call is still blocked below - subprocess.run only returns
        # once the child has already finished, which is too late for a
        # force-close to interrupt.
        with self._process_lock:
            self._current_process = process

        try:
            try:
                stdout_bytes, stderr_bytes = process.communicate(
                    input=input_bytes, timeout=timeout_seconds
                )
            except subprocess.TimeoutExpired:
                # process.kill() alone is not enough on Windows: a
                # `claude`/`codex` call is cmd.exe -> node, and the
                # grandchild inherits the stdout/stderr pipes. Killing
                # only cmd.exe leaves node holding them, and the wait
                # below then blocks until node finishes on its own -
                # which is what "the timeout never fires" looks like.
                self._kill_process_tree(process)
                stdout_bytes, stderr_bytes = self._drain_after_kill(process)
                stdout = self._decode_output(stdout_bytes)
                stderr = self._decode_output(stderr_bytes)
                return CliResult(
                    command=display_command,
                    success=False,
                    return_code=-1,
                    timed_out=True,
                    stdout=stdout.strip(),
                    stderr=(
                        stderr.strip()
                        + (
                            f"\nCommand timed out after {timeout_seconds} seconds."
                            if timeout_seconds is not None
                            else "\nCommand timed out."
                        )
                    ).strip(),
                )
        finally:
            with self._process_lock:
                if self._current_process is process:
                    self._current_process = None

        stdout = self._decode_output(stdout_bytes)
        stderr = self._decode_output(stderr_bytes)

        return CliResult(
            command=display_command,
            success=process.returncode == 0,
            return_code=process.returncode,
            stdout=stdout.strip(),
            stderr=stderr.strip(),
        )

    def terminate_current(self) -> bool:
        """Best-effort kill of the subprocess currently in flight inside
        run() (if any), including its full descendant process tree on
        Windows - a `claude`/`codex` CLI call typically isn't a single
        process, it launches a runtime underneath it, and killing only
        the immediate child would leave that runtime orphaned.

        Safe to call from a different thread than the one running run():
        this is specifically for MainWindow.closeEvent (GUI thread)
        killing a workflow that's still executing on its own background
        QThread when the user force-closes the app. Returns True if a
        live process was found and a kill was attempted (not a guarantee
        the process has exited by the time this returns - run()'s own
        communicate() call, unblocked by the kill, is what observes the
        actual exit).
        """
        with self._process_lock:
            process = self._current_process

        if process is None or process.poll() is not None:
            return False

        self._kill_process_tree(process)
        return True

    # How long to wait for a process we have just killed to actually let
    # go of its pipes. Only a backstop: if taskkill /T did its job this
    # returns immediately. It exists so that a stuck descendant turns a
    # timeout into a slightly-late timeout rather than an infinite wait.
    _SECONDS_TO_WAIT_FOR_A_KILLED_PROCESS = 10

    @staticmethod
    def _kill_process_tree(process: subprocess.Popen) -> None:
        """Kill `process` and everything it started.

        The descendants are the whole point. On Windows `claude` and
        `codex` resolve to `.CMD` shims, so the process we hold is
        cmd.exe and the CLI itself is a grandchild - and it is the
        grandchild, not cmd.exe, that holds our stdout/stderr pipes.
        """
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                creationflags=NO_WINDOW,
            )
            # taskkill can fail (already gone, or access denied); make
            # sure the process we do own dies either way.
            if process.poll() is None:
                process.kill()
        else:
            try:
                os.killpg(os.getpgid(process.pid), 9)  # SIGKILL
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()

    def _drain_after_kill(
        self, process: subprocess.Popen
    ) -> tuple[bytes | None, bytes | None]:
        """Collect whatever output survived, without ever waiting forever.

        This is the second half of the timeout bug: the old code called
        communicate() with no timeout here, so a descendant still holding
        the pipes could keep run() blocked long after the deadline.
        """
        try:
            return process.communicate(
                timeout=self._SECONDS_TO_WAIT_FOR_A_KILLED_PROCESS
            )
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                return process.communicate(
                    timeout=self._SECONDS_TO_WAIT_FOR_A_KILLED_PROCESS
                )
            except subprocess.TimeoutExpired:
                return b"", b""

    # cmd.exe re-parses the command line of anything it launches, so a
    # `.cmd` shim gets our arguments twice-parsed: once by cmd.exe for
    # the `/c` line, once again when the shim's own `%*` is expanded into
    # a batch line. These two helpers produce text that survives both
    # passes and then the target's C-runtime parsing, so the tool
    # receives exactly the strings AICC passed to run().
    @staticmethod
    def _quote_for_cmd(segment: str) -> str:
        """Quote one `%`-free run of text.

        Quoting (not caret-escaping) is what makes `|`, `&`, `>` and `(`
        inert, because carets are consumed by the first parse and would
        leave the metacharacter live for the second. Inside the quotes,
        `"` is doubled - the one escape both cmd.exe and the C runtime
        agree on, which keeps their idea of "am I inside quotes" in
        step - and backslash runs that sit in front of a quote (or in
        front of the closing quote) are doubled for the C runtime.
        """
        out = ['"']
        backslashes = 0
        for char in segment:
            if char == "\\":
                backslashes += 1
                out.append(char)
                continue
            if char == '"':
                out.append("\\" * backslashes)
                backslashes = 0
                out.append('""')
                continue
            backslashes = 0
            out.append(char)
        out.append("\\" * backslashes)
        out.append('"')
        return "".join(out)

    @classmethod
    def _escape_for_cmd(cls, argument: str) -> str:
        """Render one argument as a cmd.exe token.

        `%` is the one character quotes cannot simply contain: cmd.exe
        expands `%NAME%` inside quotes too, which is how a prompt
        mentioning `%USERPROFILE%` used to arrive as a real path. The
        fix is where the quotes go, not an escape character - splitting
        the argument on `%` and quoting each piece separately puts a `"`
        immediately after every `%`, and a `"` cannot start a variable
        name, so cmd.exe never finds a `%NAME%` pair to expand.
        """
        return "%".join(
            cls._quote_for_cmd(segment) for segment in argument.split("%")
        )

    @classmethod
    def _cmd_shim_command_line(
        cls, comspec: str, argv: list[str]
    ) -> str:
        inner = " ".join(cls._escape_for_cmd(item) for item in argv)
        # /d skips AutoRun commands from the registry, /s makes cmd.exe
        # strip exactly the outer pair of quotes and leave the rest alone.
        return f'{subprocess.list2cmdline([comspec])} /d /s /c "{inner}"'

    @staticmethod
    def _argument_a_command_line_cannot_carry(argv: list[str]) -> str | None:
        """Refuse a multi-line (or NUL-bearing) argument, everywhere.

        Windows command lines are single-line. There is no escape for a
        newline, so a multi-line argument to a `.cmd` shim cannot be
        delivered at all - cmd.exe simply stops at the first line break.
        Silently sending the first line is the worst outcome available,
        so say so and name the way out.

        POSIX `execve` *can* carry a newline in argv, and that is exactly
        why this refusal is not conditioned on the platform: `claude` and
        `codex` resolve to a `.CMD` shim on Windows and to a plain script
        on macOS, so a platform-conditioned rule would let one call site
        work on a developer's laptop and hard-fail on the user's PC. A
        payload big enough to contain a line break belongs on standard
        input on every operating system, and keeping the rule true
        everywhere is what makes it checkable anywhere.
        """
        for index, item in enumerate(argv):
            if "\r" in item or "\n" in item:
                return (
                    f"Argument {index} contains a line break. A Windows "
                    "command line cannot carry one, so cmd.exe would hand "
                    f"{Path(argv[0]).name} only the first line of it, and "
                    "a rule that held only on Windows would make the same "
                    "call mean two different things on two machines. Send "
                    "this payload on standard input instead - "
                    "CliRunner.run(..., input_text=...)."
                )
            if "\x00" in item:
                return (
                    f"Argument {index} contains a NUL byte, which cannot "
                    "appear in a command line. Send this payload on "
                    "standard input instead - "
                    "CliRunner.run(..., input_text=...)."
                )
        return None

    @staticmethod
    def _decode_output(data: bytes | str | None) -> str:
        if data is None:
            return ""

        if isinstance(data, str):
            return data

        for encoding in (
            "utf-8",
            locale.getpreferredencoding(False),
            "cp950",
        ):
            try:
                return data.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue

        return data.decode("utf-8", errors="replace")
