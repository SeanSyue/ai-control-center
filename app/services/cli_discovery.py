"""Find the provider CLIs on this machine, wherever they were installed.

`shutil.which` alone stopped being enough for two measured reasons: npm's
global directory is not on PATH until the shell restarts (and AICC is launched
from a shortcut, so it can hold an older environment than the terminal the user
just installed in), and the desktop apps ship their own copy in a place nobody
would think to look.

**Search order, and how much each layer can be trusted:**

1. `PATH` - authoritative when it is set up.
2. `npm prefix -g` - *asking npm where it installs*, rather than guessing.
   This is the layer that survives a different machine: whatever Node was
   installed by, npm knows its own answer.
3. Known bundled locations - **guesses**, reverse-engineered from one machine.
   They will be wrong somewhere: a different install method, a portable build,
   another OS. Kept because they cost nothing and help the common case, but
   nothing may depend on them.
4. Whatever the user chose by hand, remembered.

Layer 4 is the point. Every layer above it can fail on a machine nobody
anticipated, so "we could not find it" must never be the end of the
conversation - it has to become "then show me where it is", once, and then be
remembered. A discovery feature without that escape hatch just relocates the
dead end.

What this module deliberately does *not* do is decide anything about
credentials. It reports where each CLI is, what version it says it is, and
whether it reports itself signed in - signing in belongs to the vendor's own
flow (see SECURITY_MODEL.md's ban on scraping tokens).

Finding a CLI does not make it usable: on the machine this was written for,
both Claude copies were installed and both were signed out.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.i18n import t
from app.services.startup_diagnostics import (
    _run_cli,
    _signed_in,
    _tool_version,
    install_command,
)

_UNSET = object()
_NPM_ROOT: Path | None | object = _UNSET


@dataclass(frozen=True)
class CliCandidate:
    provider: str
    path: Path
    version: str
    signed_in: bool | None
    on_path: bool
    # The user pointed at this one by hand. Ranked above anything guessed:
    # they know their machine better than this module's heuristics do.
    picked: bool = False

    @property
    def usable(self) -> bool:
        """Runs and is signed in. `None` (cannot tell) counts as usable.

        A CLI whose sign-in state cannot be read is not evidence of a problem,
        and refusing to offer it would strand anyone whose CLI renamed the
        subcommand.
        """
        return bool(self.version) and self.signed_in is not False


def _npm_global_root() -> Path | None:
    """Where npm installs global binaries, according to npm.

    Layer 2, and the one that travels: it does not assume Node came from any
    particular installer. Cached for the process because it costs a subprocess
    and cannot change while AICC is running.
    """
    global _NPM_ROOT
    if _NPM_ROOT is not _UNSET:
        return _NPM_ROOT
    code, output = _run_cli("npm", "prefix", "-g")
    root = None
    if code == 0:
        candidate = Path(output.strip().splitlines()[0]) if output.strip() else None
        if candidate is not None and candidate.is_dir():
            root = candidate
    _NPM_ROOT = root
    return root


def _bundled_roots(provider: str) -> list[Path]:
    """Layer 3: where the desktop apps are *known* to keep a copy.

    Guesses, from one Windows machine. Wrong somewhere by construction - a
    different install method or another OS - which is why nothing depends on
    them and why layer 4 exists.
    """
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
    if provider == "claude":
        return [appdata / "Claude" / "claude-code", local / "AnthropicClaude"]
    return [Path(os.environ.get("CODEX_HOME", home / ".codex"))]


def _executables(root: Path, stem: str, depth: int = 1) -> list[Path]:
    """Launchable copies of `stem` under `root`, without the plumbing.

    Bounded depth and no `node_modules`, because an unbounded search returns
    the same installation five times: npm's shim, the package's own bin, and
    the platform sub-package's binary are all real files that all run. Offering
    those as five choices makes the user pick between things that are not
    different - the noise is the bug, not the finding.
    """
    if not root.is_dir():
        return []
    patterns = [f"{stem}{suffix}" for suffix in (".exe", ".cmd", ".bat", "")]
    best: dict[str, Path] = {}
    for level in range(depth + 1):
        prefix = "*/" * level
        for pattern in patterns:                 # preference order
            try:
                matches = list(root.glob(prefix + pattern))
            except OSError:
                continue
            for path in matches:
                if "node_modules" in path.parts or not path.is_file():
                    continue
                # One per directory. npm writes both `claude.CMD` and an
                # extensionless `claude` shell script side by side; they are
                # the same install, and listing both asks the user to choose
                # between two names for one thing.
                best.setdefault(str(path.parent).lower(), path)
    return list(best.values())


def discover(provider: str, chosen: Path | None = None) -> list[CliCandidate]:
    """Every copy of `provider`'s CLI this machine has, best candidate first.

    `chosen` is layer 4 - a path the user picked by hand. It is probed like any
    other, so a saved path that has since been uninstalled drops out of the
    list instead of being offered as if it still worked.

    Ordering puts signed-in before signed-out, then a user's own choice, then
    PATH, then everything else - so "the first usable one" is a defensible
    default rather than whichever the filesystem happened to yield first.
    """
    seen: set[str] = set()
    candidates: list[CliCandidate] = []

    def add(path: Path, on_path: bool, picked: bool = False) -> None:
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        version = _tool_version(str(path))
        if not version:
            # Probed, not merely existence-checked: a path that no longer runs
            # (uninstalled, or a stale shim) must drop out rather than be
            # offered as a choice that will fail later.
            return
        candidates.append(
            CliCandidate(
                provider=provider,
                path=path,
                # The same reader the checklist uses, pointed at this specific
                # binary - `shutil.which` returns an absolute path unchanged,
                # so one implementation covers both. A second copy of that
                # parsing here would be two answers that can disagree.
                signed_in=_signed_in(str(path), provider),
                version=version,
                on_path=on_path,
                picked=picked,
            )
        )

    # Layer 4 first so a hand-picked path wins ties against anything guessed.
    if chosen is not None:
        add(Path(chosen), False, picked=True)
    resolved = shutil.which(provider)                       # layer 1
    if resolved:
        add(Path(resolved), True)
    npm_root = _npm_global_root()                           # layer 2
    if npm_root is not None:
        # Top level only: that is where npm puts the shim.
        for path in _executables(npm_root, provider, depth=0):
            add(path, False)
    for root in _bundled_roots(provider):                   # layer 3
        # One level down, for the `<root>/<version>/claude.exe` layout.
        for path in _executables(root, provider, depth=1):
            add(path, False)

    candidates.sort(
        key=lambda c: (c.signed_in is False, not c.picked, not c.on_path)
    )
    return candidates


def searched_locations(provider: str) -> list[str]:
    """The places just looked in, in order, for a "we could not find it" message.

    Listing them is not decoration. "找不到指令" tells someone nothing they can
    act on; "we looked in these four places" lets them see immediately that the
    copy they know about was not among them, and go straight to pointing at it.
    """
    places = [t("discover.place.system_path")]
    npm_root = _npm_global_root()
    places.append(
        f"npm 的全域安裝位置（{npm_root}）" if npm_root else t("discover.place.npm_unknown")
    )
    for root in _bundled_roots(provider):
        places.append(f"{root}{'' if root.is_dir() else '（不存在）'}")
    return places


def how_to_find_it(provider: str) -> str:
    """What to tell someone who has it installed but we did not find it.

    The important half is the last line: their own machine can answer this
    question, and telling them how to ask beats any list of paths we can guess
    at - it is the one instruction that works on a machine nobody anticipated.
    """
    # `where.exe`, not `where`: in PowerShell `where` is an alias for
    # `Where-Object`, so the bare form reads an empty pipeline and prints
    # **nothing at all** - no output, no error, no exit code. Someone following
    # this instruction sees a blank line and concludes we were right that it is
    # not installed, when it is. A wrong answer that looks like confirmation is
    # worse than the error we were trying to avoid.
    finder = "where.exe" if os.name == "nt" else "which"
    return (
        f"如果你確定已經裝好了，可以在終端機執行 `{finder} {provider}`，"
        f"它會印出實際位置，再用「選擇位置」指給 AICC。"
    )


# `install_command` 搬到 `startup_diagnostics` 了——啟動檢查也要說同一句話，
# 而兩份副本裡只會有一份跟著套件改名。這裡保留名字，因為它是這個模組對外
# 介面的一部分。
__all__ = [
    "CliCandidate",
    "clear_chosen_path",
    "chosen_path",
    "discover",
    "discover_all",
    "how_to_find_it",
    "install_command",
    "remember_chosen_path",
    "searched_locations",
]


def discover_all() -> dict[str, list[CliCandidate]]:
    return {provider: discover(provider, chosen_path(provider)) for provider in ("claude", "codex")}


# ---- layer 4：使用者自己指定的位置 ------------------------------------------
#
# 這一層本來只有一個 `chosen` 參數，沒有任何地方存它——也就是「記住」那一半
# 從來不存在。而**沒有被記住的第四層等於沒有第四層**：每次啟動都要重新指路的
# 逃生口，第二次就沒有人會用。
#
# 存在 `%APPDATA%`，不進任何專案資料夾（ROADMAP P12 那條規則：專案底下的東西
# Git、匯出、同步都碰得到）。這裡存的是一個路徑不是憑證，但規則一樣適用——
# 而且路徑本身就會洩漏使用者的帳號名稱。
CHOICES_FILE = "cli-paths.json"


def _choices_path() -> Path:
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) / "AIControlCenter" if appdata else Path.home() / ".aicontrolcenter"
    return root / CHOICES_FILE


def _load_choices() -> dict[str, str]:
    try:
        raw = json.loads(_choices_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def chosen_path(provider: str) -> Path | None:
    """使用者上次指給 AICC 的位置，如果那個檔案還在的話。

    存在性在這裡就檢查掉：一個已經被移除的舊選擇，若原封不動傳給 `discover()`
    只會多一次注定失敗的探測。而使用者要看到的是「你指的那個不見了」，
    不是靜靜地退回到「找不到」。
    """
    raw = _load_choices().get(provider, "")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def remember_chosen_path(provider: str, path: Path) -> None:
    target = _choices_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    choices = _load_choices()
    choices[provider] = str(Path(path))
    target.write_text(
        json.dumps(choices, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def clear_chosen_path(provider: str) -> None:
    choices = _load_choices()
    if choices.pop(provider, None) is None:
        return
    target = _choices_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(choices, ensure_ascii=False, indent=2), encoding="utf-8"
    )
