"""Finding the provider CLIs wherever they were installed.

The behaviour under test is mostly about what is *not* returned: an unbounded
search finds the same installation four times, and a list of four identical
choices is worse than one answer.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services import cli_discovery as cd
from app.services import startup_diagnostics as sd


def _fake_cli(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("@echo off\r\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _no_npm(monkeypatch):
    # Layer 2 spawns npm; these tests are about the search, not about npm.
    monkeypatch.setattr(cd, "_npm_global_root", lambda: None)


def _stub_probes(monkeypatch, version="cli 1.0", signed=True):
    monkeypatch.setattr(cd, "_tool_version", lambda executable: version)
    monkeypatch.setattr(cd, "_signed_in", lambda executable, provider="": signed)


def test_one_copy_per_directory_not_one_per_file_extension(tmp_path) -> None:
    # npm writes `claude.CMD` and an extensionless `claude` shell script side by
    # side. They are the same install; offering both asks the user to choose
    # between two names for one thing.
    root = tmp_path / "bin"
    _fake_cli(root / "claude.cmd")
    _fake_cli(root / "claude")

    found = cd._executables(root, "claude", depth=0)

    assert len(found) == 1


def test_the_package_internals_are_not_offered_as_choices(tmp_path) -> None:
    # The same binary again, three levels down inside node_modules. Real files
    # that really run - and useless as a choice.
    root = tmp_path / "bin"
    _fake_cli(root / "claude.cmd")
    _fake_cli(root / "node_modules" / "@anthropic-ai" / "claude-code" / "claude.exe")

    found = cd._executables(root, "claude", depth=3)

    assert [p.name for p in found] == ["claude.cmd"]


def test_a_bundled_copy_one_level_down_is_found(tmp_path) -> None:
    # `%APPDATA%\Claude\claude-code\<version>\claude.exe` - the layout that
    # made "找不到指令" wrong for someone who had only the desktop app.
    root = tmp_path / "claude-code"
    _fake_cli(root / "2.1.221" / "claude.exe")

    assert len(cd._executables(root, "claude", depth=1)) == 1
    assert cd._executables(root, "claude", depth=0) == []


def test_a_path_that_no_longer_runs_is_dropped(tmp_path, monkeypatch) -> None:
    # A remembered choice whose CLI has since been uninstalled must disappear,
    # not be offered as something that will fail later.
    monkeypatch.setattr(cd.shutil, "which", lambda name: None)
    monkeypatch.setattr(cd, "_bundled_roots", lambda provider: [])
    monkeypatch.setattr(cd, "_tool_version", lambda executable: "")

    assert cd.discover("claude", chosen=tmp_path / "gone" / "claude.exe") == []


def test_a_hand_picked_path_comes_first(tmp_path, monkeypatch) -> None:
    # Two layers hold this up: `chosen` is probed before anything else, and the
    # sort key also ranks `picked` ahead of guesses. Removing the sort term
    # alone changes nothing - insertion order already put it first - so this
    # asserts the outcome rather than either mechanism.
    picked = _fake_cli(tmp_path / "mine" / "claude.exe")
    guessed_root = tmp_path / "guessed"
    _fake_cli(guessed_root / "claude.exe")
    on_path = _fake_cli(tmp_path / "onpath" / "claude.exe")
    monkeypatch.setattr(cd.shutil, "which", lambda name: str(on_path))
    monkeypatch.setattr(cd, "_bundled_roots", lambda provider: [guessed_root])
    _stub_probes(monkeypatch)

    found = cd.discover("claude", chosen=picked)

    assert found[0].path == picked
    assert found[0].picked is True
    assert len(found) == 3, "其餘候選仍應列出，只是排在後面"


def test_a_signed_out_copy_ranks_below_a_signed_in_one(tmp_path, monkeypatch) -> None:
    root = tmp_path / "bundled"
    _fake_cli(root / "claude.exe")
    on_path = _fake_cli(tmp_path / "onpath" / "claude.exe")
    monkeypatch.setattr(cd.shutil, "which", lambda name: str(on_path))
    monkeypatch.setattr(cd, "_bundled_roots", lambda provider: [root])
    monkeypatch.setattr(cd, "_tool_version", lambda executable: "cli 1.0")
    # The one on PATH is signed out; the bundled one is not.
    monkeypatch.setattr(
        cd, "_signed_in",
        lambda executable, provider="": "onpath" not in str(executable).lower(),
    )

    found = cd.discover("claude")

    assert found[0].signed_in is True
    assert found[0].usable is True
    assert found[-1].signed_in is False


def test_a_cli_whose_sign_in_cannot_be_read_still_counts_as_usable(
    tmp_path, monkeypatch
) -> None:
    _fake_cli(tmp_path / "bin" / "claude.exe")
    monkeypatch.setattr(cd.shutil, "which", lambda name: str(tmp_path / "bin" / "claude.exe"))
    monkeypatch.setattr(cd, "_bundled_roots", lambda provider: [])
    _stub_probes(monkeypatch, signed=None)

    assert cd.discover("claude")[0].usable is True


def test_the_not_found_message_can_name_where_it_looked() -> None:
    # "找不到指令" is not actionable. Someone who knows they installed it needs
    # to see that the place they installed it was not among those searched.
    places = cd.searched_locations("claude")

    assert any("PATH" in p for p in places)
    assert any("npm" in p for p in places)
    assert len(places) >= 3


def test_there_is_an_instruction_for_asking_the_machine_itself() -> None:
    # The one instruction that works on a machine nobody anticipated - but only
    # spelled `where.exe`. In PowerShell `where` is an alias for `Where-Object`,
    # so the bare form reads an empty pipeline and prints nothing: no output, no
    # error, no exit code. Someone following it sees a blank line and concludes
    # we were right that it is not installed, when it is.
    #
    # This assertion previously read `"where claude" in guidance` and was green
    # for as long as the bug existed. A test can hold a bug in place.
    guidance = cd.how_to_find_it("claude")

    assert (
        ("where.exe claude" in guidance)
        if os.name == "nt"
        else ("which claude" in guidance)
    )
    assert "選擇位置" in guidance


def test_the_install_command_names_the_right_package() -> None:
    assert "@anthropic-ai/claude-code" in cd.install_command("claude")
    assert "@openai/codex" in cd.install_command("codex")


def test_the_install_command_is_runnable_in_powershell(monkeypatch) -> None:
    # Same failure as `claude auth login`: npm's own launcher is a shim with a
    # `.ps1` beside it. Patched on `sd`, not `cd` - typeable_name reads the os
    # and shutil bound in its own module.
    monkeypatch.setattr(sd.os, "name", "nt")
    monkeypatch.setattr(sd.shutil, "which", lambda name: rf"C:\node\{name}.CMD")

    assert cd.install_command("claude").startswith("npm.cmd install -g ")
