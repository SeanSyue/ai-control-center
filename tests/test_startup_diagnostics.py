"""What the checklist says, and — as much — what it stays quiet about.

The value of a start-up checklist is that people read it. That only holds if it
speaks up when something is wrong and says nothing when nothing is, so several
of these tests assert silence: an unconfigured optional integration is not a
warning, and a clean machine does not get a dialog on every launch.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.services import startup_diagnostics as sd


def _nothing_anywhere(monkeypatch) -> None:
    """PATH 沒有，其他三層也沒有。

    兩層都要擋掉。只擋 `shutil.which` 的話，`_not_on_path` 會去問
    `cli_discovery.discover()`，而那個函式讀的是真實機器——這台開發機的
    `%APPDATA%\\Claude\\claude-code` 底下就有一份，於是測試結果取決於跑它的
    人裝了什麼。**這條測試本來就是這樣壞的**，接上探索之後才浮出來。
    """
    from app.services import cli_discovery

    monkeypatch.setattr(sd.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli_discovery, "discover", lambda provider, chosen=None: [])


def test_a_missing_cli_blocks_and_says_how_to_get_it(monkeypatch) -> None:
    _nothing_anywhere(monkeypatch)

    check = sd.check_claude()

    assert check.level is sd.Level.BLOCKED
    assert check.blocking
    assert "npm install" in check.fix


def test_a_missing_cli_says_where_it_looked(monkeypatch) -> None:
    """「找不到指令」不是一個可以行動的句子。

    列出找過的地方，使用者才能立刻看出自己知道的那一份不在裡面，然後直接
    去指路——而不是照著訊息重裝一次、得到一樣的結果。
    """
    _nothing_anywhere(monkeypatch)

    check = sd.check_claude()

    assert "找過這些地方" in check.fix
    assert "系統 PATH" in check.fix
    # 最後那一句才是真正在任何機器上都有效的指示：問你自己的機器。
    assert "where.exe" in check.fix or "which" in check.fix


def test_a_cli_installed_but_off_path_is_not_told_to_reinstall(monkeypatch) -> None:
    """ROADMAP P12 記著的那次實機事故。

    `discover_all()` 找得到 `codex-cli 0.147.0`，但那個 shell 的 PATH 沒有 node
    目錄。舊訊息叫人去 `npm install`——**照著做一次，PATH 還是一樣，訊息也還是
    一樣**。一個很有自信的錯誤指示，比「不知道」更難脫身。
    """
    from pathlib import Path as _Path

    from app.services import cli_discovery

    monkeypatch.setattr(sd.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        cli_discovery,
        "discover",
        lambda provider, chosen=None: [
            cli_discovery.CliCandidate(
                provider=provider,
                path=_Path(r"C:\Users\x\AppData\Roaming\Claude\claude-code\claude.exe"),
                version="2.1.226",
                signed_in=True,
                on_path=False,
            )
        ],
    )

    check = sd.check_claude()

    assert check.level is sd.Level.BLOCKED, "看不到就是跑不了，這仍然是擋住的"
    assert "不在 PATH 上" in check.detail, check.detail
    assert "claude.exe" in check.fix, check.fix
    # 最重要的一條：不可以再叫人去重裝一個已經裝好的東西。
    assert "npm install" not in check.fix, check.fix


def test_an_off_path_copy_that_is_signed_out_says_so(monkeypatch) -> None:
    # 找到了不等於能用。裝好但登出是這個專案量過的真實失敗。
    from pathlib import Path as _Path

    from app.services import cli_discovery

    monkeypatch.setattr(sd.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        cli_discovery,
        "discover",
        lambda provider, chosen=None: [
            cli_discovery.CliCandidate(
                provider=provider,
                path=_Path(r"C:\x\claude.exe"),
                version="2.1.226",
                signed_in=False,
                on_path=False,
            )
        ],
    )

    check = sd.check_claude()

    assert "尚未登入" in check.detail, check.detail


@pytest.mark.skipif(os.name != "nt", reason="the .cmd shim problem is Windows-only")
def test_a_cmd_shim_is_run_the_way_the_workflow_runs_it(tmp_path, monkeypatch) -> None:
    """`npm install -g` on Windows produces a .cmd, and it has to work.

    This is the test that was missing, which is why the bug shipped: every
    other test in this file stubs `_tool_version`, so the one function that has
    to agree with CliRunner about how to launch a CLI was never actually run.
    Found by installing the real CLIs and watching the checklist call a working
    install broken - and tell the user to reinstall, which produces the same
    shim.

    A real .cmd is created rather than mocked, because the failure is in
    CreateProcess, not in anything Python can be asked about: passing a .cmd as
    argv[0] with no shell raises WinError 2 on a file that exists and runs.
    """
    shim = tmp_path / "faketool.cmd"
    shim.write_text("@echo off\r\necho faketool 9.9.9\r\n", encoding="utf-8")
    monkeypatch.setattr(sd.shutil, "which", lambda name: str(shim))

    assert sd._tool_version("faketool") == "faketool 9.9.9"


@pytest.mark.skipif(os.name != "nt", reason="the .cmd shim problem is Windows-only")
def test_a_shim_that_really_is_broken_still_reports_nothing(
    tmp_path, monkeypatch
) -> None:
    # The other side: running things through a shell must not turn a genuine
    # failure into a pass. A shim that exits non-zero is still broken.
    #
    # It prints before failing on purpose. A silent failure would pass this
    # test even with the exit-code check removed - there would be nothing to
    # mistake for a version - so the test would not be guarding the check.
    shim = tmp_path / "brokentool.cmd"
    shim.write_text(
        "@echo off\r\necho brokentool 1.0.0\r\nexit /b 1\r\n", encoding="utf-8"
    )
    monkeypatch.setattr(sd.shutil, "which", lambda name: str(shim))

    assert sd._tool_version("brokentool") == ""


def test_an_installed_but_signed_out_cli_blocks(monkeypatch) -> None:
    # The failure this catches is silent otherwise: `--version` works, the
    # checklist goes green, and every run then dies partway through with
    # "Not logged in · Please run /login". Measured on this machine - the
    # workflow reached the CLI, paid for the setup, and failed there.
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\bin\claude.cmd")
    monkeypatch.setattr(sd, "_tool_version", lambda executable: "claude 2.1.226")
    monkeypatch.setattr(sd, "_signed_in", lambda executable: False)

    check = sd.check_claude()

    assert check.level is sd.Level.BLOCKED
    assert "尚未登入" in check.detail
    # 2026-08-24「會員優先」拍板之後這裡是 `setup-token`（需要訂閱的那條路），
    # 不再是 `auth login`。指令的**唯一來源**是 `credential_entry`——
    # 這裡跟著它問，不要再寫死一份（那正是這次改壞的東西）。
    from app.services.credential_entry import route_for

    assert route_for("claude").typed in check.fix


def test_the_sign_in_instruction_runs_under_a_restricted_execution_policy(
    monkeypatch,
) -> None:
    # The bug this catches was found by a user pasting our own instruction into
    # an administrator PowerShell and getting a red UnauthorizedAccess wall:
    # npm ships `claude.ps1` beside `claude.cmd`, PowerShell prefers the
    # `.ps1`, and the default policy on Windows client refuses to load it. So
    # `claude auth login` - the string we printed - is the one spelling that
    # cannot work in the shell Windows 11 opens by default.
    #
    # The assertion is on the extension rather than on "auth login" appearing
    # somewhere, because that looser check passed throughout the whole bug.
    monkeypatch.setattr(sd.os, "name", "nt")
    monkeypatch.setattr(sd.shutil, "which", lambda name: rf"C:\node\{name}.CMD")
    monkeypatch.setattr(sd, "_tool_version", lambda executable: "claude 2.1.226")
    monkeypatch.setattr(sd, "_signed_in", lambda executable: False)

    assert "claude.cmd setup-token" in sd.check_claude().fix


def test_the_install_instruction_names_npms_own_shim(monkeypatch) -> None:
    # npm is an npm-installed shim too, so `npm install -g ...` fails for the
    # same reason - and it is printed to someone who has nothing working yet.
    # The situation it is printed in: no claude, but npm is there to install it.
    monkeypatch.setattr(sd.os, "name", "nt")
    monkeypatch.setattr(
        sd.shutil,
        "which",
        lambda name: None if name == "claude" else rf"C:\node\{name}.CMD",
    )

    check = sd.check_claude()

    assert check.detail == "找不到指令"
    assert "npm.cmd install -g" in check.fix


def test_the_extension_is_the_resolved_one_not_a_hardcoded_cmd(monkeypatch) -> None:
    # Guards the fix against its own overcorrection. `.cmd` is right only for
    # the npm shim; an install that puts a real claude.exe on PATH would make
    # `.cmd` the broken spelling, so this has to follow what PATH resolves to.
    monkeypatch.setattr(sd.os, "name", "nt")

    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\Program Files\claude.exe")
    assert sd.typeable_name("claude") == "claude.exe"

    # PATHEXT is upper case and `which` echoes it back; shouting it in the UI
    # reads like an error message.
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\node\claude.CMD")
    assert sd.typeable_name("claude") == "claude.cmd"


def test_nothing_found_means_no_extension_is_invented(monkeypatch) -> None:
    monkeypatch.setattr(sd.os, "name", "nt")
    monkeypatch.setattr(sd.shutil, "which", lambda name: None)

    assert sd.typeable_name("claude") == "claude"


def test_a_cli_whose_login_state_cannot_be_read_is_not_called_signed_out(
    monkeypatch,
) -> None:
    # None means "cannot tell" - a renamed subcommand must not turn into an
    # accusation that the user is logged out. Wrong advice given confidently is
    # the thing this file exists to avoid.
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\bin\claude.cmd")
    monkeypatch.setattr(sd, "_tool_version", lambda executable: "claude 2.1.226")
    monkeypatch.setattr(sd, "_signed_in", lambda executable: None)

    assert sd.check_claude().level is sd.Level.OK


def test_login_state_is_read_from_what_each_cli_actually_prints(monkeypatch) -> None:
    # Both real formats, captured from the CLIs themselves:
    #   claude auth status -> {"loggedIn": false, ...}
    #   codex  login status -> "Logged in using ChatGPT"
    # The needles are whitespace-free because the haystack is stripped; writing
    # them with spaces made a logged-out CLI report "cannot tell", which is the
    # wrong answer that looks most like a right one.
    samples = {
        ("claude", 1): '{\n  "loggedIn": false,\n  "authMethod": "none"\n}',
        ("codex", 0): "Logged in using ChatGPT",
        ("claude", 0): '{\n  "loggedIn": true,\n  "authMethod": "oauth"\n}',
    }

    for (exe, code), output in samples.items():
        monkeypatch.setattr(sd, "_run_cli", lambda e, *a, _o=output, _c=code: (_c, _o))
        expected = "false" not in output.lower()
        assert sd._signed_in(exe) is expected, f"{exe}: {output!r}"


def test_a_cli_that_is_present_but_will_not_run_still_blocks(monkeypatch) -> None:
    # A stale shim left behind by an uninstalled npm package resolves on PATH
    # and then errors. Checking only for presence would call that healthy, and
    # the user would be told everything is fine while nothing works.
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\fake\claude.cmd")
    monkeypatch.setattr(sd, "_tool_version", lambda executable: "")

    check = sd.check_claude()

    assert check.level is sd.Level.BLOCKED
    assert "無法執行" in check.detail


def test_a_working_cli_reports_its_version(monkeypatch) -> None:
    monkeypatch.setattr(sd.shutil, "which", lambda name: r"C:\bin\claude.exe")
    monkeypatch.setattr(sd, "_tool_version", lambda executable: "claude 2.1.0")
    # Stubbed as well, or this test spawns a real subprocess against a path
    # that does not exist and its result depends on whether the developer
    # happens to be signed in. It passed either way by accident; a negative
    # control on the sign-in branch is what surfaced it.
    monkeypatch.setattr(sd, "_signed_in", lambda executable: True)

    check = sd.check_claude()

    assert check.level is sd.Level.OK
    assert check.detail == "claude 2.1.0"
    assert not check.blocking


def test_missing_git_is_not_treated_as_a_failure(monkeypatch) -> None:
    # AICC never runs git itself; the CLIs use it inside a repository. Flagging
    # its absence would put a red mark on a machine that works fine.
    monkeypatch.setattr(sd.shutil, "which", lambda name: None)

    check = sd.check_git()

    assert check.level is sd.Level.OFF
    assert not check.blocking


def test_an_unconfigured_optional_integration_is_off_not_broken(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.line_notifier.LineNotifier.is_configured", lambda self: False
    )

    check = sd.check_line()

    assert check.level is sd.Level.OFF
    assert not check.blocking


def test_a_sync_folder_that_has_gone_missing_does_block(tmp_path, monkeypatch) -> None:
    # Different from never having configured one: the user set this up and it
    # has since broken, so syncing will fail silently until they are told.
    from app.services.cloud_sync import CloudSyncConfigStore, CloudSyncSettings

    gone = str(tmp_path / "not-here")
    monkeypatch.setattr(
        CloudSyncConfigStore, "load", lambda self: CloudSyncSettings(root_path=gone)
    )

    check = sd.check_cloud_sync()

    assert check.level is sd.Level.BLOCKED
    assert gone in check.detail


def test_a_check_that_raises_becomes_a_row_rather_than_a_crash(monkeypatch) -> None:
    def explode() -> sd.Check:
        raise RuntimeError("檢查本身寫壞了")

    monkeypatch.setattr(sd, "CHECKS", (explode,))

    results = sd.run_diagnostics()

    assert len(results) == 1
    assert results[0].blocking


def test_every_check_returns_a_row(monkeypatch) -> None:
    # Whatever this machine has installed, the checklist is never partial - a
    # missing row reads as "not checked", which is the one thing it must not
    # imply.
    monkeypatch.setattr(sd.shutil, "which", lambda name: None)

    results = sd.run_diagnostics()

    assert len(results) == len(sd.CHECKS)
    assert {c.key for c in results} == {
        "claude", "codex", "git", "python", "transcripts", "line", "cloud"
    }


# ---- the dialog's showing rule -------------------------------------------

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import diagnostics_dialog as dd  # noqa: E402


def _settings(tmp_path: Path, monkeypatch) -> QSettings:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    QApplication.instance() or QApplication([])
    settings = QSettings("Sean", "AI Control Center")
    settings.remove(dd.SETTINGS_KEY)
    return settings


def _ok(key: str) -> sd.Check:
    return sd.Check(key, key, sd.Level.OK, "fine")


def _blocked(key: str) -> sd.Check:
    return sd.Check(key, key, sd.Level.BLOCKED, "missing")


def test_the_checklist_appears_on_a_first_run(tmp_path, monkeypatch) -> None:
    _settings(tmp_path, monkeypatch)

    assert dd.should_show([_ok("claude")]) is True


def test_a_clean_machine_stops_showing_it_once_dismissed(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    settings.setValue(dd.SETTINGS_KEY, True)

    assert dd.should_show([_ok("claude")]) is False


def test_a_problem_shows_it_even_after_being_dismissed(tmp_path, monkeypatch) -> None:
    # The dismissal means "do not nag me when everything is fine", not "never
    # tell me anything again".
    settings = _settings(tmp_path, monkeypatch)
    settings.setValue(dd.SETTINGS_KEY, True)

    assert dd.should_show([_ok("claude"), _blocked("codex")]) is True


def test_the_stored_flag_survives_being_read_back_as_a_string(
    tmp_path, monkeypatch
) -> None:
    # QSettings hands back "false" as a string on Windows, and a bare truth
    # test on that is True - which would silence the checklist for someone who
    # asked to keep seeing it.
    settings = _settings(tmp_path, monkeypatch)
    settings.setValue(dd.SETTINGS_KEY, "false")

    assert dd.should_show([_ok("claude")]) is True


# ---- 本機對話格式（ROADMAP P0「Provider 格式改版要優雅降級」） -------------


def _jsonl(directory, name, rows):
    import json

    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )
    return path


def _scanner_over(tmp_path, monkeypatch, codex=None):
    """把兩家的來源都指到 tmp_path。

    只蓋 `_claude_roots` 的話，Codex 那半會讀到**跑測試這台機器上真實的**
    session 檔——於是「空機器」那條在開發機上永遠不成立，而在 CI 上又會通過。
    格式健康檢查擴充到兩家之後才浮出來：測試的答案取決於誰在跑它。
    """
    from app.services.session_scanner import SessionScanner

    monkeypatch.setattr(
        SessionScanner, "_claude_roots", staticmethod(lambda: [tmp_path])
    )
    monkeypatch.setattr(
        SessionScanner,
        "_codex_roots",
        staticmethod(lambda: [codex if codex is not None else tmp_path / "無"]),
    )
    return SessionScanner()


READABLE = {
    "type": "user",
    "message": {"role": "user", "content": "做一件事"},
    "sessionId": "s1",
}
# Three shapes a provider format change plausibly produces. None of them
# raises today - they all just yield nothing, which is the problem.
CONTENT_BECAME_AN_OBJECT = {
    "type": "user",
    "message": {"role": "user", "content": {"blocks": [{"v": "做一件事"}]}},
}
WRAPPED_IN_AN_ENVELOPE = {
    "v": 2,
    "event": {"type": "user", "message": {"role": "user", "content": "做一件事"}},
}


# Codex 的紀錄是另一個資料夾、另一種形狀（一行一則訊息，沒有 Claude 那層
# `message` 信封），所以要用它自己的樣子測。
CODEX_READABLE = {"role": "user", "content": "做一件事", "timestamp": "2026-08-10"}
CODEX_CHANGED = {"payload": {"speaker": "user", "text": "做一件事"}}


def test_a_codex_format_change_is_caught_too(tmp_path, monkeypatch) -> None:
    """這個檢查本來只看 Claude。

    Codex 改版的那天，會發生的正是這個檢查存在要防止的事，只是發生在另一半上：
    對話清單空掉，而畫面說不出為什麼。
    """
    import app.services.startup_diagnostics as sd

    codex = tmp_path / "codex"
    codex.mkdir()
    _scanner_over(tmp_path, monkeypatch, codex=codex)
    for index in range(4):
        _jsonl(codex, f"c{index}.jsonl", [CODEX_CHANGED])

    check = sd.check_transcript_format()

    assert check.level is sd.Level.BLOCKED
    assert "Codex" in check.fix, check.fix
    assert "不是你的對話不見了" in check.fix


def test_a_healthy_codex_folder_is_not_flagged(tmp_path, monkeypatch) -> None:
    # 反方向：Codex 那半不可以「一律看不懂」。
    import app.services.startup_diagnostics as sd

    codex = tmp_path / "codex"
    codex.mkdir()
    _scanner_over(tmp_path, monkeypatch, codex=codex)
    for index in range(4):
        _jsonl(codex, f"c{index}.jsonl", [CODEX_READABLE])

    check = sd.check_transcript_format()

    assert check.level is sd.Level.OK, check
    # 2026-08-24：這裡原本釘的是「Codex」，而 app 其他地方寫「Codex CLI」
    # ——同一個東西在同一個 app 裡兩個名字。顯示名收成一份之後，
    # 這條改成問那一份，而不是釘一個字面值。
    from app.services.providers import display_name

    assert f"{display_name('codex')} 4/4" in check.detail, check.detail


def test_one_provider_breaking_is_not_averaged_away(tmp_path, monkeypatch) -> None:
    """一家好一家壞，不可以被平均成「還行」。

    「一半的對話不見了」跟「全部不見了」對使用者是同一種恐慌，而只有前者會被
    合併計算藏起來。所以兩家分開算，訊息也要指名是哪一家。
    """
    import app.services.startup_diagnostics as sd

    codex = tmp_path / "codex"
    codex.mkdir()
    _scanner_over(tmp_path, monkeypatch, codex=codex)
    for index in range(4):
        _jsonl(tmp_path, f"a{index}.jsonl", [READABLE])          # Claude 正常
        _jsonl(codex, f"c{index}.jsonl", [CODEX_CHANGED])        # Codex 壞了

    check = sd.check_transcript_format()

    assert check.level is sd.Level.BLOCKED, check
    assert "Codex" in check.fix
    assert "Claude Code" not in check.fix, "把沒壞的那家也講成壞了"


def test_an_unfamiliar_format_never_raises(tmp_path, monkeypatch) -> None:
    """The 降級 half, which already worked - pinned so it keeps working."""
    scanner = _scanner_over(tmp_path, monkeypatch)
    for index, row in enumerate(
        (CONTENT_BECAME_AN_OBJECT, WRAPPED_IN_AN_ENVELOPE)
    ):
        path = _jsonl(tmp_path, f"a{index}.jsonl", [row])
        assert scanner._parse_claude_messages(path, "s") == []


def test_a_format_change_is_told_apart_from_an_empty_machine(
    tmp_path, monkeypatch
) -> None:
    """Both look like 「你沒有對話」 on screen. Only one is true.

    This is the whole point: an unfamiliar shape yields zero messages, and
    zero messages is exactly what a fresh machine looks like. Without this,
    the day Claude Code changes its transcript format the library empties and
    the app says nothing - the user concludes their conversations are gone.
    """
    scanner = _scanner_over(tmp_path, monkeypatch)
    for index in range(4):
        _jsonl(tmp_path, f"x{index}.jsonl", [CONTENT_BECAME_AN_OBJECT])

    health = scanner.format_health()

    assert health.checked == 4
    assert health.understood == 0
    assert health.looks_changed is True

    empty = _scanner_over(tmp_path / "nothing-here", monkeypatch)
    assert empty.format_health().checked == 0
    assert empty.format_health().looks_changed is False


def test_one_odd_file_is_not_a_format_change(tmp_path, monkeypatch) -> None:
    """A session with no user turn is ordinary.

    A warning that fires on one file is a warning people learn to dismiss,
    and then it is worth nothing on the day it is right.
    """
    scanner = _scanner_over(tmp_path, monkeypatch)
    _jsonl(tmp_path, "odd.jsonl", [CONTENT_BECAME_AN_OBJECT])
    for index in range(4):
        _jsonl(tmp_path, f"ok{index}.jsonl", [READABLE])

    health = scanner.format_health()

    assert health.understood == 4
    assert health.looks_changed is False


def test_the_check_says_what_to_do_and_what_not_to_do(
    tmp_path, monkeypatch
) -> None:
    """The instruction matters more than the diagnosis here.

    A user whose library just emptied will reinstall, or re-import, or delete
    and start over - and every one of those makes it worse. The fix line has
    to say 「先不要重裝」 out loud.
    """
    import app.services.startup_diagnostics as sd

    _scanner_over(tmp_path, monkeypatch)
    for index in range(4):
        _jsonl(tmp_path, f"x{index}.jsonl", [WRAPPED_IN_AN_ENVELOPE])

    check = sd.check_transcript_format()

    assert check.level is sd.Level.BLOCKED
    assert "不是你的對話不見了" in check.fix
    assert "先不要重裝" in check.fix


def test_a_fresh_machine_is_off_not_blocked(tmp_path, monkeypatch) -> None:
    # Nobody has run Claude Code here yet. A checklist that flags a fresh
    # install teaches people to ignore it - the same rule the optional
    # integrations follow.
    import app.services.startup_diagnostics as sd

    _scanner_over(tmp_path / "empty", monkeypatch)

    check = sd.check_transcript_format()

    assert check.level is sd.Level.OFF
    assert check.blocking is False


def test_a_row_that_is_not_an_object_is_skipped_not_raised(
    tmp_path, monkeypatch
) -> None:
    """A top-level array or bare value per line - the JSONL shape changing.

    Found by negative control: the two 「unfamiliar format」 cases above are
    both dicts, so the `isinstance(entry, dict)` guard never ran and turning
    it into a raise left everything green. That guard is the difference
    between an empty library and a crash on launch.
    """
    scanner = _scanner_over(tmp_path, monkeypatch)
    path = tmp_path / "arrays.jsonl"
    path.write_text(
        '[{"type": "user"}]\n"just a string"\n42\nnull\n', encoding="utf-8"
    )

    assert scanner._parse_claude_messages(path, "s") == []


def test_a_file_with_no_readable_json_is_not_counted_against_the_format(
    tmp_path, monkeypatch
) -> None:
    """Truncated or binary is a different problem, and not evidence either way.

    Counted in, a folder with a few damaged files would push the ratio over
    and announce a format change that did not happen - and this warning is
    only worth having if it is right the one time it fires.
    """
    scanner = _scanner_over(tmp_path, monkeypatch)
    for index in range(4):
        _jsonl(tmp_path, f"ok{index}.jsonl", [READABLE])
    for index in range(6):
        (tmp_path / f"junk{index}.jsonl").write_text(
            "\x00\x01 not json at all\n{broken\n", encoding="utf-8"
        )

    health = scanner.format_health(sample=20)

    assert health.checked == 4
    assert health.understood == 4
    assert health.looks_changed is False


def test_the_message_extractor_itself_tolerates_a_non_object(
    tmp_path, monkeypatch
) -> None:
    """Asserted on the unit, because two guards were shielding each other.

    `_parse_claude_messages` filters non-dicts, and `_extract_message` filters
    them again. Removing either one alone changes no observable behaviour, so
    a negative control on either came back green - not because the protection
    is missing, but because the other half was covering. Testing the extractor
    directly is what makes its guard provable.
    """
    from app.services.session_scanner import SessionScanner

    scanner = SessionScanner()
    for value in ([{"type": "user"}], "just a string", 42, None, ("a", "b")):
        assert scanner._extract_message(value) == ("", "")
