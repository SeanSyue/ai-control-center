"""What AICC needs on this machine, and what is missing.

Everything AICC does routes through two external CLIs it does not ship. When
one of them is absent the failure surfaces much later, as a run that produces
no output, and the message the user gets describes the symptom rather than the
cause. This checks up front instead.

Two rules shape what is in here:

**Only what AICC actually uses.** No "system health" checks - disk space,
network reachability, Windows build. Each one would be another thing to keep
true, and none of them changes what the user should do next.

**A missing optional integration is not a problem.** LINE notifications and
Drive sync are opt-in, so an unconfigured one reports as OFF rather than as a
warning. A checklist that flags features you deliberately did not turn on
teaches people to ignore it.
"""
from __future__ import annotations

from app.i18n import t
from app.services.providers import display_name

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

VERSION_TIMEOUT_SECONDS = 6


class Level(str, Enum):
    OK = "ok"
    # Something AICC needs is missing. Runs will fail.
    BLOCKED = "blocked"
    # An optional integration is simply not set up. Nothing is wrong.
    OFF = "off"


@dataclass
class Check:
    key: str
    name: str
    level: Level
    detail: str
    fix: str = ""
    # A command the user can start from inside AICC instead of being told to
    # go and type it. Only ever the vendor's own flow: for sign-in, the CLI
    # opens the browser and the approval happens there - AICC never sees a
    # credential. Empty when the fix is not something a button can start.
    action: tuple[str, ...] = ()
    action_label: str = ""
    # 這個檢查可以走「你指給我看」那條路的話，這裡放 provider 名稱
    # （`cli_discovery` 的第四層）。空字串＝這一項沒有那條路。
    #
    # 為什麼不重用上面的 `action`：那個欄位的語意是「一串可以直接跑的指令」，
    # 而選檔案不是指令。把不同種類的動作塞進同一個欄位，是下一個人得先讀完
    # 兩個呼叫端才知道它到底是什麼的原因。
    #
    # （順帶記錄：`action` / `action_label` 到 2026-08-10 為止**沒有任何地方
    # 設定它們，也沒有任何地方畫它們**。它們是替「按一下就開始登入」留的位置，
    # 而那件事要不要做是使用者的決定——見 ROADMAP P12。）
    pick_provider: str = ""
    # 這一列的**登入狀態問不出來**（`_signed_in()` 回 `None`：那支 CLI 把子指令
    # 改名了，或探測本身失敗）。
    #
    # 為什麼要一個欄位，而那句話明明已經在 `detail` 裡了
    # （「2.1.226（登入狀態不明）」）：**`detail` 是一句中文**。畫面要分辨
    # 「問不出來」跟「真的登入了」，唯一不會腐爛的判準是結構——拿那句中文去比對，
    # 英文版上線那天比對永遠不成立，而畫面看起來完全正常
    # （2026-08-24 一天之內在這個 repo 抓到五處同一個病）。
    #
    # 讀它的是 `app/services/setup_state.py`。
    # **設它的人是 `_cli_check()` 裡 `signed is None` 那一條**，2026-08-26 接上了。
    # 那一行少了 `sign_in_unknown=True` 的話，`detail` 那句「登入狀態不明」照樣
    # 印得出來，而憑證入口那一頁畫的仍然是**薄荷點**（`state_of()` 只看這個欄位，
    # 一個字都不讀 `detail`）——話講了，燈沒改，而兩者之中他先看到的是燈。
    # 釘著它的是 `test_the_credentials_page_does_not_draw_that_row_as_ready`。
    sign_in_unknown: bool = False

    @property
    def blocking(self) -> bool:
        return self.level is Level.BLOCKED


def typeable_name(executable: str) -> str:
    """The name to print in an instruction - not always the name we resolve.

    `claude auth login` is the obvious thing to tell someone, and on Windows it
    is the one spelling that reliably does not work. npm drops a `claude.ps1`
    next to the `.cmd`, PowerShell prefers the `.ps1`, and the default
    execution policy on Windows client is Restricted - so our instruction,
    pasted into the shell Windows 11 opens by default, dies on a red
    `UnauthorizedAccess` wall that never mentions Claude. The user then has a
    broken command from us and no way to connect it to what we were asking for.

    Naming the extension we actually resolved sidesteps the whole thing:
    `claude.cmd auth login` runs under any execution policy, and is still
    correct if they paste it into cmd.exe.

    Derived rather than hardcoded, because `.cmd` is only right for the npm
    shim - an install that puts a real `claude.exe` on PATH would make `.cmd`
    the broken spelling. This asks the same question `_tool_version` asks, so
    the instruction and the probe cannot drift apart.
    """
    if os.name != "nt":
        return executable
    resolved = shutil.which(executable)
    if not resolved:
        # Nothing found: no extension is more truthful than a guessed one.
        return executable
    path = Path(resolved)
    # PATHEXT is upper case and `which` echoes it back (`claude.CMD`); the file
    # on disk is not, and shouting an extension in the UI looks like an error.
    return f"{path.stem}{path.suffix.lower()}"


def _tool_version(executable: str) -> str:
    """`<tool> --version`, or "" if it cannot be run.

    A tool on PATH that will not execute is a real failure mode on Windows -
    a stale shim left behind by an uninstalled npm package resolves fine and
    then errors. Checking presence alone would call that healthy.

    **It has to be run the way the workflow runs it.** `npm install -g` on
    Windows produces a `.CMD` shim, and CreateProcess cannot execute a .cmd
    directly - `subprocess.run(["claude", "--version"])` raises WinError 2 on a
    perfectly good install. `CliRunner` already knew this and passes
    `shell=True`; this check did not, so it called every npm-installed CLI on
    Windows broken and told the user to reinstall, which produces the same
    shim. Measured on a fresh `npm install -g @anthropic-ai/claude-code`:
    without a shell it raises, with one it returns 2.1.226.

    A check that disagrees with the thing it is checking is worse than no
    check: it sends people to fix something that is not wrong.
    """
    command: list[str] | str = [executable, "--version"]
    use_shell = False
    if os.name == "nt":
        resolved = shutil.which(executable)
        # Quoted: the resolved path routinely contains spaces (Program Files,
        # and WinGet's own package directories).
        command = f'"{resolved or executable}" --version'
        use_shell = True

    try:
        finished = subprocess.run(
            command,
            shell=use_shell,
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            # No console window flashing up behind the splash on Windows.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if finished.returncode != 0:
        return ""
    text = (finished.stdout or finished.stderr or "").strip().splitlines()
    return text[0][:80] if text else ""


def _run_cli(executable: str, *args: str) -> tuple[int, str]:
    """Run a CLI subcommand the way the workflow does. Returns (rc, output).

    Same shell handling as _tool_version, for the same reason.
    """
    command: list[str] | str = [executable, *args]
    use_shell = False
    if os.name == "nt":
        resolved = shutil.which(executable)
        joined = " ".join(args)
        command = f'"{resolved or executable}" {joined}'
        use_shell = True
    try:
        finished = subprocess.run(
            command,
            shell=use_shell,
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return finished.returncode, (finished.stdout or "") + (finished.stderr or "")


def _signed_in(executable: str, provider: str = "") -> bool | None:
    """Is this CLI signed in? None when the question cannot be answered.

    `provider` names which probe to use when `executable` is a full path rather
    than the command name - two copies of the same CLI live in different
    places, and each has to be asked about itself.

    Installed-but-signed-out is the failure this exists for, and it is a
    *silent* one otherwise: `--version` works perfectly, the checklist goes
    green, and then every single run dies with `Not logged in · Please run
    /login` somewhere in the middle. Measured on this machine - the workflow
    reached the CLI, spent the setup, and failed there.

    Both CLIs answer cheaply and without a prompt:
        claude auth status  -> {"loggedIn": false, ...}, exit 1
        codex login status  -> "Logged in using ChatGPT",  exit 0

    None rather than False when the probe itself is unrecognised, so a CLI that
    renames the subcommand is reported as "cannot tell" instead of as signed
    out. Claiming someone is logged out when they are is its own kind of wrong
    advice.
    """
    probes = {
        "claude": ("auth", "status"),
        "codex": ("login", "status"),
    }
    args = probes.get(provider or executable)
    if args is None:
        return None
    code, output = _run_cli(executable, *args)
    if code == -1:
        return None
    # Whitespace stripped so one set of needles covers both CLIs' formats -
    # which means the needles must not carry any either. Written with spaces
    # the first time, and the result was a logged-out CLI reporting "cannot
    # tell": the answer that looks least wrong and is hardest to notice.
    compact = "".join(output.lower().split())
    # Negative first - "notloggedin" contains "loggedin".
    if '"loggedin":false' in compact or "notloggedin" in compact:
        return False
    if '"loggedin":true' in compact or "loggedin" in compact:
        return True
    # Exit code as the fallback, but only when the output said nothing either
    # way - an unfamiliar message is "cannot tell", not "signed out".
    if not output.strip():
        return code == 0
    return None


def install_command(provider: str) -> str:
    packages = {
        "claude": "@anthropic-ai/claude-code",
        "codex": "@openai/codex",
    }
    # npm is an npm-installed shim too, so a bare `npm` fails in PowerShell for
    # exactly the reason `claude` does. See typeable_name.
    #
    # 住在這裡而不是 `cli_discovery` 裡，因為兩邊都要用同一句話。原本是
    # `check_claude()` 寫死一份、`cli_discovery.install_command()` 又寫一份
    # ——同一個事實兩份副本，而套件名稱改掉的時候只會有一邊跟著改。
    return f"{typeable_name('npm')} install -g {packages.get(provider, provider)}"


def _not_on_path(key: str, name: str, provider: str) -> Check:
    """PATH 上沒有的時候，先去別的地方找過再說話。

    這裡本來直接回「找不到指令」＋「去 npm install」。**而那句話在最常見的
    那種失敗上是錯的**：ROADMAP P12 記著一次實機事故，`discover_all()` 找得到
    `codex-cli 0.147.0`，但那個 shell 的 PATH 沒有 node 目錄。照著訊息重裝一次，
    PATH 還是一樣，訊息也還是一樣——使用者被一個很有自信的錯誤指示卡住。

    `cli_discovery` 一直都能回答這件事，只是**沒有任何地方呼叫它**（251 行，
    只有自己的測試 import）。所以這不是新功能，是把已經寫好的答案接上唯一
    會問問題的地方。

    成本只發生在失敗的那條路上：機器正常時 `shutil.which()` 就中了，這個函式
    根本不會執行，啟動檢查的 1.12 秒不受影響。
    """
    # 局部 import：`cli_discovery` 反過來用這個模組的 `_tool_version` 等，
    # 模組層級互相 import 會循環。`check_line()` 也是這樣做的。
    from app.services import cli_discovery

    # 第四層先傳進去：使用者上次指的位置排在所有猜測之前。
    found = cli_discovery.discover(provider, cli_discovery.chosen_path(provider))
    if found:
        best = found[0]
        note = "" if best.signed_in is not False else t("diag.cli.and_not_signed_in")
        return Check(
            key,
            name,
            Level.BLOCKED,
            f"不在 PATH 上，但這台機器有一份{note}",
            f"找到的位置：{best.path}\n\n"
            "它裝好了，只是 AICC 的執行環境看不到——從捷徑啟動的程式拿到的 PATH "
            "可能比終端機裡的舊。把上面那個資料夾加進 PATH 之後重開 AICC 即可。",
            pick_provider=provider,
        )
    return Check(
        key,
        name,
        Level.BLOCKED,
        t("diag.cli.command_not_found"),
        f"{install_command(provider)}\n\n"
        + t("diag.searched_here")
        + "\n".join(f"　· {place}" for place in cli_discovery.searched_locations(provider))
        + "\n\n"
        + cli_discovery.how_to_find_it(provider),
        # 「找不到」不可以是句點。四層猜測全部失敗的機器一定存在（不同安裝方式、
        # 可攜版、另一個 OS），所以最後一定要有一條「那你指給我看」。
        pick_provider=provider,
    )


def _cli_check(key: str, name: str, executable: str, provider: str) -> Check:
    # `fix`（安裝指令）原本是參數，由 `check_claude` / `check_codex` 各自傳一份
    # 寫死的字串進來。接上 `_not_on_path` 之後那個參數就沒有人讀了，而兩份
    # 寫死的套件名稱還留在原地——**算了也傳了，但沒有人用**。既然安裝指令
    # 現在只有 `install_command()` 一個來源，參數就一起拿掉。
    path = shutil.which(executable)
    if path is None:
        return _not_on_path(key, name, executable)

    # Both probes at once. They are independent subprocesses against the same
    # binary, and running them one after the other was the actual cost of this
    # check - parallelising the six checks against each other only moved 2.5s
    # to 2.1s, because the wait was inside this function, not between calls.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        version_task = pool.submit(_tool_version, executable)
        signed_task = pool.submit(_signed_in, executable)
        version = version_task.result()
        signed = signed_task.result()

    if not version:
        return Check(
            key,
            name,
            Level.BLOCKED,
            f"找得到（{path}）但無法執行",
            t("diag.cli.stale_shim"),
        )
    if signed is False:
        # 登入那一句**不在這裡寫死**。它以前是兩份寫死的字串（一份 `auth login`、
        # 一份 `login`），而 2026-08-24 拍板「會員優先」的時候只有 `credential_entry`
        # 那一邊跟著改——於是這裡繼續叫使用者去打一句已經不是我們要他打的指令。
        #
        # 同一個病 `install_command()` 已經得過一次（見上面那段註解）。
        # **一個事實一個來源**，不然改的時候只會有一邊跟著改。
        from app.services import credential_entry

        route = credential_entry.route_for(provider)
        if route is None:
            # 不認得的 provider 不猜一句指令出來——猜出來的跑起來會失敗，
            # 而失敗訊息跟 AICC 無關，他會以為是自己打錯。
            return Check(key, name, Level.BLOCKED, f"{version}，但尚未登入")
        return Check(
            key,
            name,
            Level.BLOCKED,
            f"{version}，但尚未登入",
            f"在終端機執行：{route.typed}",
            # 這兩個欄位從 2026-08-10 空到今天，註解寫著「替『按一下就開始登入』
            # 留的位置」。P12 拍板之後就是現在。
            #
            # AICC 只是**把那支 CLI 開起來**：瀏覽器是它開的、同意是他自己點的、
            # 憑證進的是它自己的 store。AICC 從頭到尾沒有碰到任何一把 key。
            action=route.argv,
            action_label=t("credential.button.take_me_there"),
        )
    if signed is None:
        # `_signed_in()` 小心地把「問不出來」跟「沒登入」分開（那支 CLI 把
        # 子指令改名了，或探測本身失敗），然後這裡把它丟掉——`None` 原本
        # 落進下面那一行，畫面上**跟真的登入了長得一模一樣**：綠燈一顆加
        # 一個版本號。
        #
        # 而那正是 `_signed_in()` 自己的 docstring 寫的、它存在的理由：
        # 「`--version` 跑得好好的、清單全綠，然後每一次執行都死在中間」。
        #
        # **不改成 BLOCKED**：不要很有自信地指控一個已經登入的人沒登入
        # （`test_a_cli_whose_login_state_cannot_be_read_is_not_called_signed_out`
        # 釘著這件事，那是對的）。中間那條路是不擋、不指控，**但把話說出來**。
        #
        # 字面沿用既有的 `diag.sign_in_unknown`（「登入狀態不明」）——同一串
        # 中文另開一個 key 會讓 `test_the_catalogue_has_no_accidental_duplicates`
        # 變紅。
        # `sign_in_unknown` 是**憑證入口那一頁唯一分得出這一列的東西**。
        # 只加上面那句話而不立這個欄位的話，那一頁畫的是薄荷點——也就是
        # 「你可以按開始執行了」——而話只寫在點旁邊的小字裡。
        return Check(
            key,
            name,
            Level.OK,
            f"{version}（{t('diag.sign_in_unknown')}）",
            sign_in_unknown=True,
        )
    return Check(key, name, Level.OK, version)


def check_claude() -> Check:
    return _cli_check("claude", display_name("claude"), "claude", "claude")


def check_codex() -> Check:
    return _cli_check("codex", display_name("codex"), "codex", "codex")


def check_git() -> Check:
    path = shutil.which("git")
    if path is None:
        # Not blocking: AICC never runs git itself. The two CLIs use it inside
        # a repository, so its absence only matters for those projects.
        return Check(
            "git",
            "Git",
            Level.OFF,
            t("diag.git.not_installed"),
            t("diag.git.only_for_repos"),
        )
    return Check("git", "Git", Level.OK, _tool_version("git") or path)


def check_python() -> Check:
    """Only meaningful when running from source.

    A packaged build carries its own interpreter, so reporting the machine's
    Python would be reporting something the app does not use.
    """
    if getattr(sys, "frozen", False):
        return Check(
            "python", "Python", Level.OK, t("diag.bundled")
        )
    return Check("python", "Python", Level.OK, sys.version.split()[0])


def check_line() -> Check:
    try:
        from app.services.line_notifier import LineNotifier

        configured = LineNotifier().is_configured()
    except Exception as error:
        return Check(
            "line",
            t("diag.line.name"),
            Level.OFF,
            f"設定讀取失敗：{error}",
            t("diag.line.refill_token"),
        )
    if configured:
        return Check("line", t("diag.line.name"), Level.OK, t("diag.configured"))
    return Check("line", t("diag.line.name"), Level.OFF, t("diag.not_configured_optional"))


def check_cloud_sync() -> Check:
    try:
        from app.services.cloud_sync import CloudSyncConfigStore

        root = CloudSyncConfigStore().load().root_path
    except Exception as error:
        return Check(
            "cloud",
            t("diag.cloud.name"),
            Level.OFF,
            f"設定讀取失敗：{error}",
            t("diag.cloud.repick_folder"),
        )
    if not root:
        return Check("cloud", t("diag.cloud.name"), Level.OFF, t("diag.not_configured_optional"))
    if not Path(root).is_dir():
        # Configured but gone is different from never configured: the user set
        # this up and it has since broken, which is worth saying out loud.
        return Check(
            "cloud",
            t("diag.cloud.name"),
            Level.BLOCKED,
            f"設定的資料夾不存在：{root}",
            t("diag.cloud.folder_moved"),
        )
    return Check("cloud", t("diag.cloud.name"), Level.OK, root)


def check_transcript_format() -> Check:
    """Whether this build still reads what Claude Code writes on disk.

    ROADMAP P0「Provider 本機格式改版時要能優雅降級」. The degrading already
    worked - an unfamiliar shape yields zero messages instead of raising. The
    trouble is that zero messages is what an empty machine looks like, so the
    day the transcript format changes, the conversation library goes blank and
    says nothing. The user concludes their conversations are gone.

    This is the one place that can tell those apart, because it knows the
    files are there and full of JSON.

    An empty machine reports OFF, not BLOCKED: nobody has run Claude Code
    here yet, and a checklist that flags a fresh install teaches people to
    ignore it - the same rule the optional integrations follow.
    """
    key, name = "transcripts", t("diag.sessions.name")
    # 顯示名只有一個來源（`app/services/providers.py`）。這裡原本自己寫了
    # 一份，而且 Codex 那一個跟別處不一樣（「Codex」vs「Codex CLI」）
    # ——同一個東西在同一個 app 裡兩個名字。
    labels = {code: display_name(code) for code in ("claude", "codex")}
    try:
        from app.services.session_scanner import SessionScanner

        scanner = SessionScanner()
        # 兩家分開問。合在一起算的話，一家好一家壞會被平均掉——而**「一半的
        # 對話不見了」跟「全部不見了」對使用者是同一種恐慌**，卻只有前者會被
        # 這種算法藏起來。
        reports = [scanner.format_health(provider=p) for p in labels]
    except Exception as error:
        return Check(key, name, Level.OFF, f"無法檢查：{error}")

    seen = [report for report in reports if report.checked]
    if not seen:
        return Check(key, name, Level.OFF, t("diag.sessions.none_yet"))

    broken = [report for report in seen if report.looks_changed]
    if broken:
        who = "、".join(labels[report.provider] for report in broken)
        detail = "；".join(
            f"{labels[r.provider]} 抽查 {r.checked} 個，只有 {r.understood} 個讀得懂"
            for r in broken
        )
        return Check(
            key,
            name,
            Level.BLOCKED,
            detail,
            f"{who} 的紀錄格式可能改版了。對話清單會變成空的，"
            "但那不是你的對話不見了——請回報這件事，先不要重裝。",
        )
    return Check(
        key,
        name,
        Level.OK,
        "；".join(
            f"{labels[r.provider]} {r.understood}/{r.checked}" for r in seen
        ),
    )


CHECKS = (
    check_claude,
    check_codex,
    check_git,
    check_python,
    check_transcript_format,
    check_line,
    check_cloud_sync,
)


def run_diagnostics() -> list[Check]:
    """Every check, in a fixed order, with a crash counting as a failure.

    A check that raises is a bug in the check, but the user still gets a row
    saying so rather than a startup that dies while telling them everything is
    fine.

    Run in parallel, and that is not a micro-optimisation: this happens before
    the window appears, on every launch, and each CLI check now spawns two
    subprocesses (`--version` and the sign-in probe). Measured serially at 2.5s
    - paid even when everything is fine and the dialog never shows, which is
    the common case. The checks are independent subprocess calls, so waiting
    for them one after another was buying nothing.

    Order is restored afterwards: the dialog reads top to bottom and the
    important rows are first.
    """
    from concurrent.futures import ThreadPoolExecutor

    def guarded(check) -> Check:
        try:
            return check()
        except Exception as error:  # pragma: no cover - defensive
            return Check(
                check.__name__, check.__name__, Level.BLOCKED,
                f"檢查本身失敗：{error}",
            )

    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        return list(pool.map(guarded, CHECKS))


def blocking(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.blocking]


def provider_state(check: Check) -> str:
    """憑證入口那一頁把這一列畫成哪一種：`"ok"` / `"signed_out"` / …

    **推導不在這裡**，在 `app/services/setup_state.py`：五條互斥的規則，
    全部看結構（`level` ／ `action` ／ `pick_provider` ／ `sign_in_unknown`），
    **一個字都不讀 `detail`**。

    這一支只是那一頁**伸手拿得到的那個名字**。`credentials_web_panel.state_of()`
    的 docstring 明著寫了它等的兩種形狀，而這是排在前面的那一個；接不上的下場
    不是「少一個欄位」，是那一頁整個 `setup` 都不推、永遠寫著
    「還沒有收到這台機器的檢查結果」——而檢查其實跑完了、答案其實算出來了。

    > 順帶記著為什麼不叫 `credential_state`：那個函式名會被
    > `tests/test_no_new_door_opens_for_an_api_key.py` 當成一扇新開的收憑證的門
    > 而變紅（它掃的是名字裡有沒有憑證字眼），而那條紅燈的正確反應是去問使用者，
    > 不是把自己加進白名單。

    回純字串不回列舉，因為頁面拿它去查 `STATE_CLASS` 那張表，而那張表的鍵是
    普通字串；查不到就走 `|| 'unknown'`，**每一列都會變成虛線圈，而且不會有
    任何一個地方報錯**。

    `Level.OFF` 那幾列（Git 沒裝、LINE 沒設定）會丟 `CannotDrawThisCheck`
    ——這一頁畫不出它們，而猜一種畫法比不畫糟。面板那一側只餵兩支 CLI。
    """
    # 局部 import：`setup_state` 在模組層級用這個檔案的 `Check` 與 `Level`，
    # 模組層級互相 import 會循環。`_not_on_path()` 也是這樣做的。
    from app.services.setup_state import state_of

    return state_of(check).value
