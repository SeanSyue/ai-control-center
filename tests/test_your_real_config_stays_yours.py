r"""The suite must not read or write the config files the user's app actually uses.

`tests/test_your_real_settings_stay_yours.py` guards the Qt half of the user's
state. This file guards the other half, which is bigger and was unguarded until
2026-08-21: nine places in `app/` resolve `%APPDATA%` themselves, and eight of
them own real files under it - the project list, the Google Drive settings, the
LINE credentials, the lineup presets, the AI settings, the dashboard queue, the
remembered CLI paths, and the Claude sidebar index. None of them go through
`QSettings`, so none of them were covered.

那個「九」在 2026-08-24 之前寫的是「八」，而漏掉的第九個是這裡面最該被列管的
一個。掃描的判準當時是含右括號的字面子字串 `os.environ.get("APPDATA")`，而
`app/services/vendor_session_index.py:83` 寫的是 `os.environ.get("APPDATA", "")`
——多一個預設值，整支模組就從清單上消失了，`unaccounted` 與 `stale` 雙雙是空的，
測試訊息照樣宣稱「`app/` 裡每一個 resolve %APPDATA% 的模組都有人負責」。

它偏偏是唯一一個寫到**別家廠商資料夾**的（`%APPDATA%\Claude\claude-code-sessions`），
而收工時比對真實檔案的 `conftest._config_fingerprint()` 只雜湊
`%APPDATA%\AIControlCenter\*.json`。**字面掃描看不到它，收工比對也看不到它**
——正好是這份 docstring 底下那句 "Nothing would say a word" 說的形狀。
（今天實質保護還在：`conftest` 在任何 app 模組 import 之前就把 `APPDATA` 指向
沙箱，而它是在 `__init__` 當下讀環境變數的。壞掉的是「清單完整」這個斷言，
不是資料本身。）

**還沒補的那一半**：`conftest._config_fingerprint()` 的範圍仍然只有
`%APPDATA%\AIControlCenter\*.json`，所以這一支就算列管了，收工比對還是看不到
它動過使用者的檔。那要改 `conftest.py`，不在這一輪的範圍裡。

What that cost, measured rather than imagined: on 2026-08-16 a test left
`cloud_sync.json` reading `device_id: "test-device"` with `root_path` pointing
into `...\pytest-of-<you>\pytest-22\test_a_step_log_full_of_client0\drive`.
Windows swept the temp folder; the settings file stayed. Every launch after
that opened with Google Drive sync switched on and aimed at a folder that did
not exist - `startup_diagnostics.check_cloud_sync` reported `blocked`, and the
auto-sync timer fired 750ms in and failed. The user had never enabled Google
Drive sync at all.

It took five days to notice because nothing turned red. That is the whole
point of this file: the redirect in `conftest.py` is the fix, and these tests
are what stop the redirect from quietly going away again.

Deliberately no `importorskip`. These stores are plain `pathlib` and `json`;
there is no dependency here that could let this guard skip itself into silence.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable

from app.services import cli_discovery
from app.services.ai_settings import AISettingsStore
from app.services.cloud_sync import CloudSyncConfigStore
from app.services.line_notifier import LineNotifier
from app.services.lineup_presets import LineupPresetStore
from app.services.project_dashboard import DashboardQueueStore
from app.services.project_registry import ProjectRegistry
from app.services.vendor_session_index import ClaudeSidebarIndex

import conftest
from conftest import APPDATA_SANDBOX, REAL_APPDATA

TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent / "app"

# Every store that owns a file somewhere under `%APPDATA%`, keyed by the module
# it lives in. One list, read by three tests below - a second hand-written copy
# of it would be free to drift, and the drift would be invisible.
#
# 「the app's own folder」在 2026-08-24 之前是這張表的字面條件，而它把
# `vendor_session_index` 擋在門外：那一支寫的是 `%APPDATA%\Claude\`，別家廠商
# 的資料夾。**寫到誰的資料夾不影響它會不會寫壞使用者的東西**，所以條件改成
# 「在 `%APPDATA%` 底下有自己的檔」。
CONFIG_STORES: dict[str, Callable[[], Path]] = {
    "project_registry": lambda: ProjectRegistry().config_path,
    "cloud_sync": lambda: CloudSyncConfigStore().config_path,
    "line_notifier": lambda: LineNotifier().config_path,
    "lineup_presets": lambda: LineupPresetStore().config_dir,
    "ai_settings": lambda: AISettingsStore().global_path,
    "project_dashboard": lambda: DashboardQueueStore().path,
    # `remember_chosen_path()` 真的 write_text 到
    # `%APPDATA%\AIControlCenter\cli-paths.json`。它 2026-08-24 之前住在下面
    # 那張「什麼都不擁有」的表裡，理由寫著「writes nothing under the app's
    # own folder」——**那句話是假的**，而一個假理由比沒有理由更難發現。
    # 這裡用 `_choices_path()`（私有）是刻意的：那是唯一講得出落點的入口，
    # 而為了讓測試看得到就在 app/ 開一個公開函式，是讓被測的東西遷就檢查。
    "cli_discovery": lambda: cli_discovery._choices_path(),
    # 寫到 `%APPDATA%\Claude\claude-code-sessions\<org>\<workspace>\`。
    "vendor_session_index": lambda: ClaudeSidebarIndex().index_root,
}

# Modules that read `APPDATA` but own no file of their own. Each is here with a
# stated reason, because "it looked read-only to me" is how a writer ends up on
# this list by accident.
APPDATA_READERS_THAT_OWN_NOTHING: dict[str, str] = {
    "session_scanner": (
        "reads Claude Code's own transcript store; writes nothing there"
    ),
    "not_ours": (
        "它讀 `%APPDATA%` 是為了**判斷那是不是使用者的真資料夾**，"
        "不是為了在裡面放東西——它是那道拒絕刪除的門。"
        "讀不到重導的時候它會把整個 `%APPDATA%` 判成「不是我們的」，"
        "也就是**往嚴的那一邊倒**。"
    ),
}

# 「這個模組自己去問 `%APPDATA%` 在哪」長什麼樣。
#
# 用 regex 不用字面子字串，理由是實測出來的：2026-08-24 之前這裡比的是
# `'os.environ.get("APPDATA")'`——**含右括號**——所以
# `os.environ.get("APPDATA", "")` 這種多一個預設值的寫法整支隱形。差一個逗號
# 的兩種寫法，一種被列管、一種完全看不見，而畫面上是一樣的綠。
#
# `LOCALAPPDATA` 一起收：它同樣是使用者的個人資料夾，而
# `cli_discovery.py:105-106` 已經在讀它了。
RESOLVES_APPDATA = re.compile(r"""(environ\.get|environ\[|getenv)\(?\s*["'](LOCAL)?APPDATA""")


def _is_inside(path: Path | str, directory: Path) -> bool:
    try:
        Path(path).resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def test_the_environment_variable_points_at_the_sandbox() -> None:
    """上面兩張表裡的每一個模組都讀這同一個變數，所以這裡是唯一一個
    能檢查「重導本身有沒有生效」的地方。

    **這裡刻意不寫數字。**2026-08-25 覆核抓到：這一行原本寫的是
    「All nine call sites」——寫下來的那天是對的（八個 store ＋
    `session_scanner`，剛好九個），而 `not_ours` 加進來之後就變成十個模組、
    十二處呼叫，那句話沒有跟著改。**一個寫死在 docstring 裡的數字，每多一支
    模組就假一次，而它假掉的時候不會有任何東西紅**——那正是這一整則
    （FG15／16／18／19）在修的病，只是換成用數字犯。

    真的在數的是 `test_the_store_list_covers_every_place_that_reads_appdata`：
    它從程式碼裡撈，撈到的每一支都要在上面兩張表的其中一張裡。
    """
    current = os.environ.get("APPDATA", "")

    assert current, "APPDATA is unset - the stores fall back to the real home"
    assert _is_inside(current, APPDATA_SANDBOX), (
        f"APPDATA is {current!r}, which is not the sandbox "
        f"{str(APPDATA_SANDBOX)!r}"
    )
    if REAL_APPDATA:
        assert not _is_inside(APPDATA_SANDBOX, Path(REAL_APPDATA)), (
            "the sandbox was made inside the real roaming profile, so writing "
            "to it still writes into the user's own folder"
        )


def test_every_config_store_lands_in_the_sandbox() -> None:
    """Constructing each store and asking where it actually points.

    A regex over `app/` would pass the day someone adds a seventh store,
    because a store that does not exist yet cannot fail a scan. Building them
    is the thing that keeps working.
    """
    escapees = [
        f"{module} -> {build()}"
        for module, build in CONFIG_STORES.items()
        if not _is_inside(build(), APPDATA_SANDBOX)
    ]

    assert not escapees, (
        "these config stores resolve outside the test sandbox and write into "
        "the user's real profile:\n  " + "\n  ".join(escapees)
    )


def test_the_store_list_covers_every_place_that_reads_appdata() -> None:
    """Keeps `CONFIG_STORES` from rotting the day a seventh store is added.

    Naming the stores one at a time is what makes the check above real - but a
    hand-written list has a failure mode of its own: drop an entry and every
    remaining entry still passes, so the suite goes green while one store
    quietly writes into the user's profile. Nothing would say a word.

    So the list is not allowed to be the only record. Every module in `app/`
    that resolves `APPDATA` has to be accounted for, either as a store that
    gets built and checked, or as a reader with a written reason. A new one
    appears in neither, and this goes red until someone decides which it is.

    **它擋不住什麼**：擋不住一個模組用別的方式拿到那個資料夾——例如問
    `QStandardPaths`、或者從別人手上接一個已經算好的路徑進來。這裡認得的
    只有「自己去讀那個環境變數」這一種寫法（`RESOLVES_APPDATA`）。
    """
    readers = {
        source.stem
        for source in sorted(APP_DIR.rglob("*.py"))
        if RESOLVES_APPDATA.search(source.read_text(encoding="utf-8"))
    }

    accounted = set(CONFIG_STORES) | set(APPDATA_READERS_THAT_OWN_NOTHING)
    unaccounted = sorted(readers - accounted)
    stale = sorted(accounted - readers)

    assert not unaccounted, (
        "these modules resolve %APPDATA% and are in neither list, so nothing "
        "checks where they write:\n  " + "\n  ".join(unaccounted)
    )
    assert not stale, (
        "these are listed here but no longer resolve %APPDATA%, so the list "
        "has drifted from the code:\n  " + "\n  ".join(stale)
    )


def test_the_breach_watcher_is_still_installed() -> None:
    """Everything above is a proxy - it asks where a path resolves to.

    `conftest.pytest_sessionfinish` is the only thing that looks at the user's
    actual files after the run and compares the bytes. It cannot be a test,
    because a test cannot be scheduled last. So the test for it is that it is
    still there.
    """
    assert hasattr(conftest, "pytest_sessionfinish"), (
        "the end-of-run check that compares the user's real config files "
        "against their bytes at start-up has been removed from conftest.py"
    )
    assert hasattr(conftest, "CONFIG_FINGERPRINT_AT_START"), (
        "conftest.py no longer records what the user's config looked like "
        "before the run, so the end-of-run comparison has nothing to compare"
    )


def test_no_test_spells_out_the_apps_config_folder_by_hand() -> None:
    """The sandbox only covers code that reads `APPDATA` at call time. A test
    that writes the folder out as a string walks straight past it.

    The pair being looked for is a roaming profile *and* this app's own folder
    inside it - not "roaming" on its own. `test_startup_diagnostics.py` builds
    a fake roaming path under a user named `x` as input for the CLI-discovery
    check; that string is never opened and belongs to nobody. Failing it would
    teach the next person that this guard cries wolf, which is how a guard
    stops being read.

    Escaping by way of the real home directory is not checked here: it is
    already forbidden outright by `REAL_STORE_MARKERS` in the neighbouring
    settings guard, which bans both home-directory resolvers in this folder.
    """
    offences: list[str] = []
    # `rglob` 不是 `glob`：今天 `tests/` 底下沒有任何子資料夾放 .py（只有
    # `golden/` 裝樣本檔），所以這一改不影響任何結果。改的理由是同一天在
    # `tests/test_tools_can_print_what_they_say.py` 抓到的那個形狀——那支
    # 2026-08-11 寫下 `glob` 的時候 `tools/` 也是平的，08-22 多一個子資料夾
    # 之後就安靜地漏了兩支，**而報告上不會少一行**。
    for source in sorted(TESTS_DIR.rglob("*.py")):
        if "__pycache__" in source.parts:
            continue
        if source.name == Path(__file__).name:
            continue  # this file names the folder in order to forbid it
        text = source.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if "roaming" in lowered and "aicontrolcenter" in lowered:
                offences.append(f"{source.name}:{line_number}: {line.strip()}")

    assert not offences, (
        "these tests name the real config folder directly, so the sandbox "
        "cannot redirect them:\n  " + "\n  ".join(offences)
    )


def test_the_cleanup_refuses_anything_that_is_not_a_sandbox(tmp_path) -> None:
    r"""The most expensive line in `conftest.py` is the one that deletes.

    This is not a hypothetical. On 2026-08-21 a negative-control mutation
    pointed `APPDATA_SANDBOX` at the real `%APPDATA%` - which is exactly what a
    mutation tool is supposed to do, aim a line somewhere unexpected and see
    what turns red. The tests turned red. Then the process exited and the
    registered `shutil.rmtree` deleted the user's roaming profile, because the
    cleanup trusted whatever it had been handed.

    So the cleanup now re-derives what a sandbox must look like instead of
    trusting its argument, and this is the test that says so. Every case below
    is a folder that must survive: one standing in for a real profile, one
    inside the temp directory but not made by us, and the temp directory
    itself.
    """
    from conftest import _remove_sandbox

    import tempfile as _tempfile

    temp_root = Path(_tempfile.gettempdir()).resolve()

    pretend_profile = tmp_path / "Roaming"
    (pretend_profile / "Microsoft").mkdir(parents=True)
    (pretend_profile / "keep.json").write_text("{}", encoding="utf-8")

    someone_elses_temp_dir = Path(
        _tempfile.mkdtemp(prefix="not-ours-", dir=str(temp_root))
    )
    (someone_elses_temp_dir / "keep.txt").write_text("x", encoding="utf-8")

    try:
        _remove_sandbox(str(pretend_profile), "aicc-appdata-")
        assert pretend_profile.is_dir(), (
            "the cleanup deleted a folder outside the temp directory - this is "
            "the 2026-08-21 roaming-profile deletion happening again"
        )
        assert (pretend_profile / "keep.json").is_file()

        _remove_sandbox(str(someone_elses_temp_dir), "aicc-appdata-")
        assert someone_elses_temp_dir.is_dir(), (
            "the cleanup deleted a temp directory that carries someone else's "
            "prefix"
        )

        _remove_sandbox(str(temp_root), "")
        assert temp_root.is_dir(), "the cleanup deleted the whole temp directory"
    finally:
        shutil.rmtree(someone_elses_temp_dir, ignore_errors=True)


def test_the_cleanup_still_removes_a_real_sandbox() -> None:
    """The refusals above are only worth having if the cleanup still works.

    A guard that says no to everything passes every safety test in this file
    and leaves a temp directory behind on every run, which is the quiet way
    this ends up switched off again.
    """
    from conftest import _remove_sandbox

    import tempfile as _tempfile

    sandbox = Path(_tempfile.mkdtemp(prefix="aicc-appdata-"))
    (sandbox / "AIControlCenter").mkdir()
    (sandbox / "AIControlCenter" / "cloud_sync.json").write_text(
        "{}", encoding="utf-8"
    )

    _remove_sandbox(str(sandbox), "aicc-appdata-")

    assert not sandbox.exists(), (
        "the cleanup no longer removes its own sandbox, so every pytest run "
        "leaves one behind"
    )
