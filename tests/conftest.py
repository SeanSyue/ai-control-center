r"""Every test in this suite gets a throwaway QSettings store, never yours.

`QSettings("Sean", "AI Control Center")` is not a file this suite owns. On
Windows it is `HKEY_CURRENT_USER\Software\Sean\AI Control Center` in the real
registry - the same key the installed app reads when it starts. Tests were
writing straight into it. Before this file existed, a plain `reg query` on this
machine showed four values put there by the test suite, not by the user:
`ui/mode`, `dashboard/collapsed`, `diagnostics/skip_when_clean` and (under the
neighbouring `AIControlCenter` key) `ai_settings_panel/expanded`.

That leaks in both directions, and the outbound one is the expensive one:

* `test_the_mode_is_remembered_between_runs` sets the mode to day and reads it
  straight back out of the shared key. Let any second process touch that key in
  between - another pytest run, the app itself - and the read comes back
  "night". Measured, not guessed: with one helper process writing "night" into
  that value in a loop, the test failed on the first attempt with
  `AssertionError: assert 'night' == 'day'`. On screen that is
  indistinguishable from a real failure, so it spends the one real fix on
  something that was never broken.
* Running the tests quietly changed which theme the user's app opens in
  tomorrow.

So: redirect the whole process into a directory made by `mkdtemp`.

`QSettings.setDefaultFormat()` alone does not do it. In Qt 6 the two-argument
`QSettings(organization, application)` constructor delegates to
`QSettings(NativeFormat, UserScope, ...)` with the format hard-coded - measured
in this PySide6 6.11.1 build: after `setDefaultFormat(IniFormat)`, a freshly
built `QSettings("Sean", "AI Control Center").fileName()` still reported
`\HKEY_CURRENT_USER\Software\Sean\AI Control Center`. Only the no-argument
constructor honours the default format.

What does work is replacing the class the app is about to import. Every
`QSettings(...)` in `app/` uses the plain `(organization, application)` form,
so a subclass that fills in `IniFormat` + `UserScope` for exactly that call
shape puts all of them in the sandbox and leaves any other constructor
untouched. `tests/test_your_real_settings_stay_yours.py` keeps app code using
that form, and keeps this redirect in place.

Timing matters twice over: `app.ui.web_panel` builds its `THEME` singleton (and
its QSettings) at import time, and every module that says
`from PySide6.QtCore import QSettings` binds the class as it was at *its*
import. pytest imports `conftest.py` before it imports any test module, which
is before either of those happen. The guard test checks that too, by looking at
where `THEME` itself ended up pointing.

The sandbox is per-process, so two pytest runs at the same time no longer share
a store either - which is the thing that made the flake possible.
"""
from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

# One sandbox per pytest process. mkdtemp, not tmp_path_factory, because this
# has to exist before the session starts - THEME is built during collection.
QSETTINGS_SANDBOX = Path(tempfile.mkdtemp(prefix="aicc-qsettings-"))

# The other real user store, and the more expensive one. `QSettings` is not the
# only place this app keeps state: nine separate spots in `app/` resolve
# `%APPDATA%` on their own and write real files under it - the project list, the
# Google Drive settings, the LINE credentials, the lineup presets, the AI
# settings, the dashboard cache. The QSettings sandbox above does not cover a
# single one of them, because none of them go through Qt.
#
# That gap was not theoretical. On 2026-08-16 a test wrote
# `%APPDATA%\AIControlCenter\cloud_sync.json` with `device_id: "test-device"`
# and a `root_path` pointing into its own `pytest-of-<you>\pytest-22\...`
# temp directory. The temp directory was swept days later; the settings file
# was not. From then on the installed app opened with Google Drive sync
# "enabled", pointing at a folder that no longer existed - a blocking
# start-up diagnostic and a failed sync 750ms into every launch, for a feature
# the user had never turned on.
#
# Nothing went red for five days, because nothing was watching this door.
#
# One redirect closes all nine at once: every one of them reads the same
# environment variable, so point it somewhere disposable before any app module
# is imported. `tests/test_your_real_config_stays_yours.py` is what keeps this
# a mechanism rather than a comment.
REAL_APPDATA = os.environ.get("APPDATA", "")
APPDATA_SANDBOX = Path(tempfile.mkdtemp(prefix="aicc-appdata-"))
os.environ["APPDATA"] = str(APPDATA_SANDBOX)


def _remove_sandbox(target: str, prefix: str) -> None:
    r"""Delete a sandbox, and refuse to delete anything that is not one.

    `atexit.register(shutil.rmtree, str(SOME_PATH))` is a loaded gun pointed at
    whatever `SOME_PATH` happens to hold at import time. On 2026-08-21 a
    negative-control mutation - whose entire job is to change one line and see
    what turns red - pointed the `mkdtemp` line above at the real `%APPDATA%`.
    The tests went red exactly as intended. Then the process exited, and this
    handler deleted the user's whole roaming profile.

    A mutation tool will aim every line in this file at something unexpected;
    that is what it is for. So the destructive step is not allowed to trust its
    argument. It re-derives what a sandbox has to look like - under the system
    temp directory, carrying the prefix `mkdtemp` was given - and walks away
    from anything that does not match.

    Silent return, not an exception: this runs during interpreter shutdown,
    where a raise is swallowed into a stderr traceback nobody reads. The thing
    that must not happen is the delete, and not deleting is the safe outcome
    in every case - a leftover temp directory costs nothing.
    """
    path = Path(target).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if path == temp_root:
        return
    try:
        path.relative_to(temp_root)
    except ValueError:
        return  # not under the temp directory: not ours, so not deleted
    if not path.name.startswith(prefix):
        return  # not a directory mkdtemp made for us
    shutil.rmtree(path, ignore_errors=True)


atexit.register(_remove_sandbox, str(APPDATA_SANDBOX), "aicc-appdata-")

# The app's own config folder inside the real one. Files here are the user's,
# and the suite has no business writing any of them.
REAL_CONFIG_DIR = (
    Path(REAL_APPDATA) / "AIControlCenter" if REAL_APPDATA else None
)


def _config_fingerprint() -> dict[str, str]:
    """What the user's real config folder looks like right now.

    Content hashes, not timestamps: a test that rewrites a file with the same
    bytes has not actually taken anything away, and a mtime-only check would
    call that a breach and spend a real fix on it.
    """
    if REAL_CONFIG_DIR is None or not REAL_CONFIG_DIR.is_dir():
        return {}
    seen: dict[str, str] = {}
    for entry in sorted(REAL_CONFIG_DIR.glob("*.json")):
        try:
            seen[entry.name] = hashlib.sha256(entry.read_bytes()).hexdigest()
        except OSError:
            seen[entry.name] = "unreadable"
    return seen


CONFIG_FINGERPRINT_AT_START = _config_fingerprint()


def _tear_down_qt() -> None:
    r"""整場跑完之後，把 Qt 收乾淨——**跟產品用同一支**。

    2026-08-26 量到：`2057 passed` 印完之後，pytest 這個行程以 `0xC0000005`
    死掉（離開碼 `-1073741819`）。**測試全過，而離開碼是當機**——那正是這個
    專案最怕的訊號：綠燈跟紅燈在離開碼上分不出來。

    `NEXT_STEPS.md` 那條「PowerShell 下全過也會回非零 exit code，看最後一行
    不要看 exit code」一直是在替這個崩潰打圓場。**這一支是它的解藥。**

    收的是兩件事，兩件都在 `app/ui/shutdown.py` 的模組 docstring 裡有量測：

    * 還在跑的背景 `QThread`。它的擁有者是一個模組層的集合，所以面板被丟掉
      不會讓它停下來；活過 `~QApplication` 的話 Qt 直接中止行程。
    * **已經 `deleteLater()`、但那個刪除事件從來沒被送出去的元件。**
      `processEvents()` 刻意不處理 `DeferredDelete`，所以
      「`close()` + `deleteLater()` + 一百次 `processEvents()`」之後，
      那個視窗連同它九個 web 面板**一個都沒被刪掉**——它們會留到直譯器收尾，
      而那時候 QtWebEngine 已經在拆自己了。二分法縮到一條測試
      （`test_main_window_runtime.py::test_on_thread_finished_never_auto_opens_answer_dialog`）：
      `close()` 之後有沒有那一行 `deleteLater()`，就是離開碼 0 與 0xC0000005
      的差別，而兩邊的測試報告一模一樣。

    在 hook 裡而不是 fixture 裡：要收的那一刻是**整場結束、直譯器還沒開始
    收東西**的時候，而那只有 hook 到得了。

    PySide6 沒裝的話什麼都不用做（那台機器上 Qt 測試本來就全部不會跑）。
    """
    try:
        from PySide6.QtWidgets import QApplication

        from app.ui import shutdown
    except ImportError:
        return
    shutdown.tear_down(QApplication.instance())


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001, ANN201
    """Measure the real object, not a stand-in for it.

    Every other guard in this file checks a proxy: where a path resolves to,
    what a source file says. This one looks at the user's actual files after
    the whole run and asks whether they are still the bytes they were. It has
    to live in a hook rather than a test because a test cannot be scheduled
    last, and the breach it is looking for can happen in any test.

    It prints rather than raises: an exception here surfaces as pytest's
    INTERNALERROR, which buries the sentence that explains what happened.
    In this project the last line is the verdict - so put it there, loudly,
    and set the exit status too for anything reading that instead.

    收尾還有第二件事，見上面的 `_tear_down_qt()`（就定義在這一支之前）。
    """
    _tear_down_qt()

    after = _config_fingerprint()
    if after == CONFIG_FINGERPRINT_AT_START:
        return

    changed = sorted(
        set(after) | set(CONFIG_FINGERPRINT_AT_START)
    )
    damage = [
        name
        for name in changed
        if after.get(name) != CONFIG_FINGERPRINT_AT_START.get(name)
    ]
    session.exitstatus = 1
    print(
        "\n"
        + "=" * 72
        + "\nTHE TEST SUITE CHANGED YOUR REAL CONFIG\n"
        + f"  {REAL_CONFIG_DIR}\n"
        + "".join(f"    - {name}\n" for name in damage)
        + "Something reached past the %APPDATA% sandbox in tests/conftest.py.\n"
        + "This is the 2026-08-16 cloud_sync.json breach happening again.\n"
        + "=" * 72
    )


# Ways a test could reach around the sandbox and touch the real store anyway.
# The list lives here rather than inside the guard test so that the guard is
# free to scan its own source file without matching itself.
REAL_STORE_MARKERS: tuple[tuple[str, str], ...] = (
    ("winreg", "talks to the Windows registry directly"),
    ("NativeFormat", "asks QSettings for the real registry store"),
    ("Registry32Format", "asks QSettings for the real registry store"),
    ("Registry64Format", "asks QSettings for the real registry store"),
    ("setDefaultFormat", "re-points every QSettings in the process"),
    ("QSettings.setPath", "re-points a QSettings format somewhere else"),
    ("Path.home()", "reaches into the real user home directory"),
    ("expanduser", "resolves the real user home directory"),
)

# The call shape the sandbox recognises. Anything that names a format or a
# scope itself is choosing its own storage and walks straight past us, so app/
# must not contain one - see the guard test.
FORMAT_PINNING_PREFIXES: tuple[str, ...] = (
    "QSettings(QSettings.",
    "QSettings(Format.",
    "QSettings(Scope.",
)


def _install_qsettings_sandbox(sandbox: Path) -> None:
    from PySide6 import QtCore

    real = QtCore.QSettings

    class _SandboxedQSettings(real):  # type: ignore[misc, valid-type]
        """QSettings(organization, application), rerouted to an INI file.

        Only that call shape is rerouted. A caller that spells out a format or
        a scope has said where it wants its data, and is left alone - the guard
        test is what stops app code from doing that.
        """

        def __init__(self, *args, **kwargs) -> None:
            if args and isinstance(args[0], str):
                super().__init__(
                    real.Format.IniFormat,
                    real.Scope.UserScope,
                    *args,
                    **kwargs,
                )
            else:
                super().__init__(*args, **kwargs)

    real.setDefaultFormat(real.Format.IniFormat)
    for scope in (real.Scope.UserScope, real.Scope.SystemScope):
        real.setPath(real.Format.IniFormat, scope, str(sandbox))

    QtCore.QSettings = _SandboxedQSettings


try:
    _install_qsettings_sandbox(QSETTINGS_SANDBOX)
except ImportError:
    # No PySide6 on this machine: every Qt test skips itself, and nothing can
    # reach QSettings anyway. Nothing to sandbox.
    pass

atexit.register(_remove_sandbox, str(QSETTINGS_SANDBOX), "aicc-qsettings-")
