"""把一行程式改壞，跑指定的測試，然後保證還原。

這個專案唯一真正的品質規則是「綠燈在被弄紅過之前不算證據」，而執行那條規則
一直是手動的：手改一行、跑測試、手改回來。手改回來那一步在 2026-08-10 出過事
——用 `git checkout -- <檔案>` 還原「剛剛故意弄壞的那一行」，把同一個檔案裡
**還沒 commit 的整段重構**一起丟掉了。

`git checkout --` 還原的是「最後一次 commit 的樣子」，不是「我剛才動手前的樣子」。
而做負向驗證的檔案，通常正是手上有未提交工作的那一個。

所以還原這件事要用複製，而且要在 `finally` 裡做、做完要斷言真的還原了。
這支工具就是那個形狀。

用法：

    # 單一破壞
    .\\.venv\\Scripts\\python.exe tools\\negative_control.py ^
        --file app/core/orchestrator.py ^
        --replace "success=result.success," --with "success=True," ^
        --test tests/test_step_log.py

    # 一整批（JSON 陣列，欄位同上）
    .\\.venv\\Scripts\\python.exe tools\\negative_control.py ^
        --from tools\\mutations\\mutations_cli_runner.json

    # 全部配方一次跑完
    .\\.venv\\Scripts\\python.exe tools\\negative_control.py --all

    # 只檢查配方有沒有腐爛，不跑測試（幾毫秒）
    .\\.venv\\Scripts\\python.exe tools\\negative_control.py --check

**配方住在 `tools/mutations/*.json`**，一個主題一份。它們曾經散在 repo 根目錄，
沒有人擁有、也沒有人維護，於是有四條在無人察覺的情況下腐爛掉——比不到目標字串，
那條負向驗證等於不存在。守著這件事的是 `--check`，以及呼叫同一個函式的
`tests/test_the_negative_control_recipes_still_bite.py`。

每一條會印出三種結果之一：

* **紅 ✓** —— 有測試抓到了。這是想要的。
* **綠 ✗ 沒有牙齒** —— 沒有任何測試變紅。**這條要處理**：不是斷言有洞，
  就是案例太順，或者第三種——破壞根本沒活到量測那一刻（例如改的是 inline
  style，而被測的程式碼會重新繪製那個元素）。
* **壞 ？ 測試根本沒跑起來** —— 語法壞了、字串找不到、或改到別的地方。
  **這不是綠燈也不是紅燈，是沒有結果。**

第三種是 2026-08-10 加的，因為工具自己踩到它要防的那個坑：原本用「輸出裡有沒有
`failed` 這個字」判定，而 collection error 印的是「1 error」——不含 `failed`，
所以**讀成綠燈**。工具於是宣稱「這條檢查沒有牙齒」，而事實是那一輪什麼都沒檢查。
把「沒改到」當成「沒抓到」，會讓人去刪一條其實有用的測試。現在改成看 pytest 的
離開碼：1 是測試失敗，其他非零都是「跑不起來」。

同一個理由，`--replace` 找不到字串時也算「壞」而不是跳過。
另外注意它只取代**第一個**符合的位置——要破壞的字串在檔案裡出現多次時，
請把 needle 寫長一點，否則會改到別的地方然後得到一個沒有意義的結果。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 判決符號是 ✓ / ✗，而 Windows 主控台預設是 cp950，編不出來就整支 traceback。
# 那個當掉發生在**跑完全部破壞之後、印總結的時候**：還原有做（在 finally 裡），
# 但判決一個都看不到，等於整輪白跑。這一行不是為了好看——是這支工具的輸出
# 本身就是它唯一的產物。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# pytest exit codes. 1 is "tests ran and some failed"; anything else non-zero
# means the run itself did not happen properly.
PYTEST_ALL_PASSED = 0
PYTEST_TESTS_FAILED = 1

# 一條破壞可以讓程式走到一個沒被 patch 的 modal 對話框，然後就永遠等下去。
# 這裡寬到不會誤殺（整套 GUI 測試檔約 2 秒，最慢的單檔在 10 秒內），
# 但短到不會讓人等著等著決定去砍 python——而砍掉的代價是還原不會執行。
RUN_TIMEOUT_SECONDS = 180
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


@dataclass(frozen=True)
class Mutation:
    file: str
    replace: str
    with_: str
    test: str
    label: str = ""

    @property
    def name(self) -> str:
        return self.label or f"{self.file}: {self.replace[:40]}"


# ---- 附註：這個破壞有沒有改變使用者看得到的東西 ------------------------------
#
# 判決還是紅／綠／壞三種，**這一段一個字都不會改變判決**。它加的是一句附註，
# 因為紅燈有兩種而工具原本分不出來：
#
#   1. 改壞了會被看到的行為 → 測試變紅。**這是想要的。**
#   2. 改壞了沒有人讀的東西 → 測試也變紅，而畫面一模一樣。
#
# 第二種最純粹的例子是 `MainWindow.decision_notification_policy()`：一個把設計
# 決策當 dict 回傳的函式，唯一的讀者是斷言那個 dict 的測試。把 `popup_ever`
# 改成 True，測試會紅，畫面上不會有任何一個像素不同。
#
# **「畫面沒變」不等於那條測試沒有價值**，這一點必須寫在這裡而不是只寫在報告裡：
# 安全掃描器、prompt 組裝、備份路徑，改壞了都不會出現在 `collect_state()` 裡，
# 而它們比畫面重要。所以這是一句「要不要去看看它在守什麼」的提示，不是判決。
UI_DIGEST = ROOT / "tools" / "ui_payload_digest.py"
DIGEST_TIMEOUT_SECONDS = 120
# 未動手的指紋，整輪只量一次。`main()` 填。
_BASELINE: dict[str, str | None] = {}


def _no_bytecode_env() -> dict[str, str]:
    """子行程的環境：**這一輪不准寫 `.pyc`。**

    一條破壞跑完之後留下的 `.pyc` 記的是「原始碼的秒級 mtime ＋ 位元組數」，
    而這支工具會在幾百毫秒之內把同一個檔案改好幾次。留下來的那一份於是有
    機會被下一條破壞讀成有效的——**下一條破壞跑的是上一條的程式碼**，然後
    工具回報一句「這條檢查沒有牙齒」。

    那個方向是危險的那一個：「沒有牙齒」讀起來像「這條測試沒在守什麼」，
    而下一步通常是去刪一條其實有用的測試。整輪不產生 `.pyc`，就沒有東西
    可以留到下一條。代價是每次匯入都重新編譯（實測整份配方多幾秒）。
    """
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def _ui_digest(root: Path) -> str | None:
    """Python 現在會推給畫面的一切，壓成一個指紋。量不到就回 None。

    另一個行程跑，因為破壞在磁碟上——同一個行程裡的模組已經匯入過了。
    """
    try:
        proc = subprocess.run(
            [str(PYTHON), str(UI_DIGEST), "--root", str(root)],
            cwd=ROOT,
            env=_no_bytecode_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DIGEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None
    last = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    value = last[0].strip()
    return value if len(value) == 64 and not value.startswith("UNAVAILABLE") else None


def _ui_effect(before: str | None, after: str | None) -> str:
    if before is None:
        return "畫面：沒有基準可比"
    if after is None:
        # 破壞讓 app 起不來也是一種「看得到的差別」，而且是最大的那種。
        return "畫面：量不到（這個狀態下 app 起不來）"
    if before != after:
        return "畫面：也變了"
    return "畫面：沒變 —— 這條守的不是畫面（去看看它在守什麼）"


def _drop_stale_bytecode(path: Path) -> None:
    """Throw away the `.pyc` cached for `path`, so the next run reads source.

    2026-08-21, measured: two mutations in one batch that each delete the
    same line (`never_started=True,` appears in three branches of
    `CliRunner.run`) produce files of **identical size**, and CPython
    validates a cached `.pyc` on (source mtime in whole seconds, source
    size). Applied close enough together in time, the second mutation was
    executed as the first one - and the tool reported 「綠 ✗ 沒有牙齒」
    about a check that was never actually exercised.

    That verdict is the dangerous direction: 「沒有牙齒」 is read as 「這條
    測試沒在守什麼」, and the next move is to delete a test that works. The
    same 「沒改到」 vs 「沒抓到」 confusion the exit-code rule above exists to
    prevent, arriving through the interpreter instead of through pytest.

    Cheap, so it is done unconditionally on both the mutate and the restore
    side rather than only when a collision looks likely.
    """
    cache = path.parent / "__pycache__"
    if not cache.is_dir():
        return
    # 2026-08-25：**只刪還是會漏。**同一份配方裡連續三條破壞同一個檔案
    # （`app/core/orchestrator.py`）的時候，其中一條大約每三輪就會回報一次
    # 「綠 ✗ 沒有牙齒」——而那條測試單獨對同一個破壞跑 12 次，12 次全紅。
    #
    # 量法：把 `__pycache__` 全清掉並帶 `PYTHONDONTWRITEBYTECODE=1` 再跑
    # 同一份配方六輪，13 條全紅、一次假綠都沒有。所以那個假綠不是斷言的洞，
    # 是這支工具自己餵給受測程式一份舊的 bytecode。
    #
    # 刪不掉不是沒發生（Windows 上 `.pyc` 被別的行程佔著就會 `OSError`，
    # 而下面那個 `except` 是靜音的），所以真正的止血在 `run_one`：
    # 兩個子行程都帶著 `PYTHONDONTWRITEBYTECODE=1` 跑，整輪破壞期間
    # **一個新的 `.pyc` 都不會產生**，也就沒有東西可以留到下一條破壞。
    for compiled in cache.glob(f"{path.stem}.*.pyc"):
        try:
            compiled.unlink()
        except OSError:
            # Best effort: a locked .pyc is not a reason to abandon the
            # mutation, and the size/mtime check may still catch it.
            pass


def run_one(
    mutation: Mutation, digest_root: Path | None = None
) -> tuple[str, str, str]:
    """Apply, test, restore. Returns (verdict, detail, ui_effect).

    Verdict is "red" (a test caught it), "green" (nothing did), or "broken"
    (pytest could not run - the mutation is not a fair test of anything).
    `ui_effect` is the annotation described above; empty when not measured.

    The restore is in `finally` and is checked, because a run interrupted
    halfway through leaves the working tree broken - and a broken tree after a
    tool whose whole job is "put it back" is worse than not having the tool.
    """
    path = ROOT / mutation.file
    if not path.is_file():
        return "broken", f"找不到檔案：{mutation.file}", ""

    original = path.read_text(encoding="utf-8")
    if mutation.replace not in original:
        # Deliberately not "skip": see the module docstring.
        return "broken", "找不到要破壞的字串（破壞沒套用，不是檢查沒牙齒）", ""

    holding = Path(tempfile.mkdtemp()) / path.name
    shutil.copy2(path, holding)
    try:
        path.write_text(
            original.replace(mutation.replace, mutation.with_, 1),
            encoding="utf-8",
        )
        _drop_stale_bytecode(path)
        # 破壞套上去之後、測試跑之前量指紋。順序不重要，但一定要在這個
        # `try` 裡面——放到 `finally` 之後就是量已經還原的程式碼。
        effect = ""
        if digest_root is not None:
            effect = _ui_effect(_BASELINE.get("digest"), _ui_digest(digest_root))
        try:
            proc = subprocess.run(
                [str(PYTHON), "-m", "pytest", mutation.test, "-q", "--no-header"],
                cwd=ROOT,
                env=_no_bytecode_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=RUN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            # 2026-08-10：一條破壞讓受測程式走到一個沒有被 patch 掉的 modal
            # 對話框，那一輪就永遠等在那裡。**而卡住的代價不只是等**——當時是把
            # python 直接砍掉才停下來，`taskkill /F` 不會讓底下的 `finally`
            # 有機會執行，於是破壞留在原始碼裡，要手動還原。
            #
            # 逾時算「壞」不算「綠」，理由跟 collection error 那條一樣：
            # 沒有跑完的那一輪什麼都沒量到，把它讀成「沒抓到」會讓人去刪一條
            # 其實有用的測試。
            return "broken", (
                f"超過 {RUN_TIMEOUT_SECONDS} 秒沒有結束——"
                "多半是破壞之後走到了一個沒被 patch 的對話框或輸入等待。"
            ), effect
        failures = [
            line
            for line in proc.stdout.splitlines()
            if line.startswith("FAILED")
        ]
        tail = proc.stdout.strip().splitlines()[-1:]
        detail = failures[0] if failures else " ".join(tail)

        # By exit code, not by grepping for the word "failed". pytest exits 1
        # when a test fails and 2/3/4 when it could not run the tests at all -
        # and a collection error prints 「1 error」, which contains no "failed"
        # and so read as **green**. That is the tool reporting 「這條檢查沒有
        # 牙齒」 about a run where nothing was checked: the same 「沒改到」 vs
        # 「沒抓到」 confusion this tool exists to make visible, one level up.
        if proc.returncode == PYTEST_TESTS_FAILED:
            return "red", detail, effect
        if proc.returncode == PYTEST_ALL_PASSED:
            return "green", detail, effect
        return "broken", f"exit {proc.returncode}｜{detail}", effect
    finally:
        shutil.copy2(holding, path)
        _drop_stale_bytecode(path)
        restored = path.read_text(encoding="utf-8")
        if restored != original:
            # Loud, because everything after this point is running against a
            # file nobody meant to change.
            print(
                f"!!! 還原失敗：{mutation.file} 現在跟動手前不一樣。"
                f"備份在 {holding}，馬上手動處理。",
                file=sys.stderr,
            )
            raise SystemExit(2)


RECIPES = ROOT / "tools" / "mutations"


def recipe_files() -> list[Path]:
    """每一份配方。一份都找不到也是一個問題，不是一個空的成功。"""
    return sorted(RECIPES.glob("mutations_*.json"))


def stray_recipes() -> list[Path]:
    """住在 tools/mutations/ 以外的配方。應該永遠是空的。

    上一次腐爛就是從這裡開始的：五份未追蹤的 JSON 躺在 repo 根目錄，
    沒有人擁有，於是沒有人維護。
    """
    return sorted(
        path for path in ROOT.glob("mutations_*.json") if path.parent != RECIPES
    )


def check_recipes(paths: "list[Path] | None" = None) -> list[str]:
    """配方有沒有腐爛。回傳問題清單；空清單代表每一條都還咬得到東西。

    腐爛長這樣：程式碼被重構了（換了縮排、改了訊息、搬了一行），配方裡的
    `replace` 於是比不到任何東西。工具那時給的判決是「壞 ？ 測試根本沒跑
    起來」——它**沒有說謊**，但那一行混在一整頁紅 ✓ 中間，長得像雜訊，而它
    真正的意思是「這條負向驗證從那天起什麼都沒有在守」。2026-08-21 實測：
    五份配方裡有四條已經是這個狀態，而且沒有人發現。

    所以這裡把它做成一條會變紅的規則，不是一句要人記得看的叮嚀。檢查五件事：

    1. 目標檔案還在。
    2. `replace` 在那個檔案裡**剛好出現一次**。零次＝腐爛；兩次以上也不行，
       因為 `run_one` 只換第一個符合的位置，破壞會落在沒有人預期的地方，
       然後給出一個沒有意義的判決。
    3. `with` 跟 `replace` 不一樣。一樣就是一條什麼都沒改的破壞——它必然是
       綠的，而那個綠燈不代表任何事。
    4. 要跑的那條測試真的存在（檔案在；有寫 `::名字` 的話那個名字也在）。
       測試被改名之後，pytest 會回 usage error，同樣是「沒有結果」被讀成
       「沒有牙齒」。
    5. 配方全部住在 `tools/mutations/`。散在別處的檔案沒有人擁有，而沒有人
       擁有的檔案就是這次腐爛的來源。
    """
    problems: list[str] = []

    # 只在檢查「這個 repo 真正的那幾份配方」時看散落的檔案。呼叫者自己指名
    # 要檢查哪幾份的時候（測試就是這樣用的），不該被 repo 根目錄的狀態影響
    # ——那會讓一條測不到重點的紅燈蓋掉真正的那一條。
    for stray in stray_recipes() if paths is None else []:
        problems.append(
            f"{stray.relative_to(ROOT).as_posix()}：配方沒有住在 "
            "tools/mutations/ 底下。散在別處的配方沒有人維護，"
            "這正是上一次腐爛的來源。"
        )

    paths = recipe_files() if paths is None else list(paths)
    if not paths:
        problems.append(
            "tools/mutations/ 底下一份配方都沒有——"
            "一個什麼都沒檢查的檢查會永遠是綠的。"
        )

    for recipe in paths:
        # 相對路徑好讀，但配方檔不一定在 repo 底下（測試會餵 tmp_path
        # 裡的假配方進來），所以出界時退回完整路徑而不是丟例外。
        try:
            where = recipe.relative_to(ROOT).as_posix()
        except ValueError:
            where = recipe.as_posix()
        try:
            items = json.loads(recipe.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{where}：讀不起來（{exc}）")
            continue
        if not items:
            problems.append(f"{where}：空的配方檔")
            continue
        for index, item in enumerate(items):
            head = f"{where} / {item.get('label') or f'#{index}'}"
            missing = [key for key in ("file", "replace", "test") if key not in item]
            if missing:
                problems.append(f"{head}：缺欄位 {missing}")
                continue

            target = ROOT / item["file"]
            if not target.is_file():
                problems.append(f"{head}：目標檔案不在了（{item['file']}）")
            else:
                found = target.read_text(encoding="utf-8").count(item["replace"])
                if found == 0:
                    problems.append(
                        f"{head}：在 {item['file']} 裡比不到要破壞的字串——"
                        "這條負向驗證現在什麼都沒有在守，請照現行程式碼更新它。"
                    )
                elif found > 1:
                    problems.append(
                        f"{head}：要破壞的字串在 {item['file']} 裡出現 {found} 次，"
                        "而工具只換第一個——把 needle 寫長到只符合一個地方。"
                    )

            if item.get("with", "") == item["replace"]:
                problems.append(
                    f"{head}：with 跟 replace 一樣，這條破壞什麼都沒改"
                )

            test_file, _, node = item["test"].partition("::")
            test_path = ROOT / test_file
            if not test_path.is_file():
                problems.append(f"{head}：要跑的測試檔不在了（{test_file}）")
            elif node:
                bare = node.split("[")[0]
                pattern = r"^\s*(?:async\s+)?def\s+" + re.escape(bare) + r"\s*\("
                haystack = test_path.read_text(encoding="utf-8")
                if re.search(pattern, haystack, re.M) is None:
                    problems.append(
                        f"{head}：{test_file} 裡已經沒有 {bare} 這條測試"
                        "（被改名或刪掉了，配方只會拿到 pytest 的 usage error）"
                    )

    return problems


def load_batch(path: Path) -> list[Mutation]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        Mutation(
            file=item["file"],
            replace=item["replace"],
            with_=item.get("with", ""),
            test=item["test"],
            label=item.get("label", ""),
        )
        for item in raw
    ]



# ---------------------------------------------------------------------------
# 一次只准一支在跑
# ---------------------------------------------------------------------------
#
# 這支工具的做法是「整檔複製 → 改壞 → 跑測試 → 還原」。兩支同時跑的時候，
# 第二支存下來的「原檔」**其實是第一支的破壞**，還原之後那個破壞就永久
# 留在樹上了——而它留下的樣子是「綠 ✗ 沒有牙齒」，而那正是會讓人去
# **刪掉一條有用的測試**的方向。
#
# 2026-08-27 這件事一天內真的發生兩次（`app/core/orchestrator.py:2277` 被
# 留在破壞狀態，兩條測試紅著、三條配方腐爛）。同型的前一次是 2026-08-10
# 那個 `git checkout --`。
#
# 所以：搶不到鎖就**停下來說清楚**，不要排隊也不要「應該沒事吧」照跑。
# 家在 `.aicc/runtime/`（`settings_homes.home.project.runtime_lock`）——那個家
# 本來就是為「開兩個視窗撞在一起」開的，這件事是同一件。**不要在 `.aicc/`
# 底下另外開一個位置**：那條「亂跑亂發散亂存」的守衛會擋，而它是對的。
LOCK_PATH = ROOT / ".aicc" / "runtime" / "negative_control.lock"


class AnotherRunHasTheTree(RuntimeError):
    """已經有一支在動這棵樹了。"""


@contextlib.contextmanager
def _the_only_one_touching_the_tree():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            who = LOCK_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            who = "（讀不到）"
        raise AnotherRunHasTheTree(
            "已經有另一支負向驗證在動這棵樹了：" + who + "\n"
            + "同時跑兩支會讓其中一支把另一支的破壞當成原檔存起來，"
            "**還原之後那個破壞會永久留在樹上**，" + "\n"
            + "而它留下的樣子是「綠 ✗ 沒有牙齒」——"
            "照著那句話讀，下一步就是刪掉一條有用的測試。" + "\n\n"
            + "等它跑完再跑。它已經不在了的話（機器當掉、視窗被關），"
            "手動刪掉這個檔案：" + "\n"
            + "  " + str(LOCK_PATH)
        ) from None
    try:
        os.write(fd, ("pid " + str(os.getpid())).encode("utf-8"))
        os.close(fd)
        yield
    finally:
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把一行改壞、跑測試、保證還原。綠燈在被弄紅過之前不算證據。"
    )
    parser.add_argument("--file", help="要動手的檔案（相對 repo 根目錄）")
    parser.add_argument("--replace", help="要被換掉的字串")
    parser.add_argument("--with", dest="with_", default="", help="換成什麼")
    parser.add_argument("--test", help="要跑的測試（pytest 路徑或 node id）")
    parser.add_argument("--label", default="", help="這條破壞的名字")
    parser.add_argument(
        "--from", dest="batch", type=Path, help="一整批破壞的 JSON 檔"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="跑 tools/mutations/ 底下的每一份配方",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只檢查配方有沒有腐爛（比不到目標字串、測試被改名），不跑測試",
    )
    parser.add_argument(
        "--no-ui-effect",
        action="store_true",
        help="不要量「畫面有沒有跟著變」（每條破壞多約半秒）",
    )
    args = parser.parse_args(argv)

    if args.check:
        # 這一段刻意在任何測試跑起來之前，因為它回答的是「等一下那些判決
        # 到底有沒有意義」。一條比不到目標的配方，跑出來的是「壞 ？」，
        # 而「壞 ？」被當成雜訊略過過整整一輪。
        problems = check_recipes()
        for problem in problems:
            print(f"腐爛 ✗  {problem}")
        if problems:
            print()
            print(
                f"{len(problems)} 條配方已經比不到它要破壞的東西。"
                "**這不是紅燈也不是綠燈，是那條負向驗證不存在了**——"
                "照現行程式碼把它更新回去，不要刪掉它。"
            )
            return 1
        print(f"配方檢查通過：{len(recipe_files())} 份，每一條都還咬得到東西。")
        return 0

    if args.all:
        mutations = [m for path in recipe_files() for m in load_batch(path)]
    elif args.batch:
        mutations = load_batch(args.batch)
    elif args.file and args.replace and args.test:
        mutations = [
            Mutation(args.file, args.replace, args.with_, args.test, args.label)
        ]
    else:
        parser.error(
            "要嘛給 --all / --from，要嘛給 --file / --replace / --test"
        )

    labels = {
        "red": "紅 ✓",
        "green": "綠 ✗ 沒有牙齒",
        "broken": "壞 ？ 測試根本沒跑起來",
    }
    tally = {"red": 0, "green": 0, "broken": 0}
    # 預設會量。一個要人記得打開的觀察，就是一個沒有人讀的觀察。
    digest_root: Path | None = None
    if not args.no_ui_effect:
        digest_root = Path(tempfile.mkdtemp(prefix="nc-ui-"))
        _BASELINE["digest"] = _ui_digest(digest_root)
        if _BASELINE["digest"] is None:
            print(
                "（量不到未動手的畫面指紋，這一輪不會有「畫面有沒有變」的附註。"
                "工作區可能本來就是壞的——先確認再往下讀判決。）"
            )

    blind = 0
    for mutation in mutations:
        verdict, detail, effect = run_one(mutation, digest_root)
        tally[verdict] += 1
        if verdict == "red" and effect.startswith("畫面：沒變"):
            blind += 1
        print(f"{labels[verdict]}  {mutation.name}")
        print(f"        {detail}")
        if effect:
            print(f"        {effect}")
        sys.stdout.flush()

    if tally["green"]:
        print()
        print(
            f"{tally['green']} 條破壞沒有讓任何測試變紅——這幾條是要處理的："
            "不是斷言有洞，就是案例太順，或者破壞根本沒活到量測那一刻。"
        )
    if blind:
        print()
        print(
            f"{blind} 條變紅了，但畫面一模一樣。**這不是缺點也不是判決**——"
            "安全掃描、prompt 組裝、備份路徑改壞了都不會出現在畫面上，"
            "那種紅燈是對的。它只是在說：去看看那條測試在守什麼，"
            "如果答案是「一個沒有人讀的值」，那條紅燈是假警報。"
        )
    if tally["broken"]:
        print()
        print(
            f"{tally['broken']} 條連測試都沒跑起來（語法壞掉、字串找不到、"
            "改到別的地方）。**這不是綠燈也不是紅燈，是沒有結果**——"
            "把它當成綠燈會讓人去刪一條其實有用的測試。"
        )
    return 1 if tally["green"] or tally["broken"] else 0


if __name__ == "__main__":
    # 鎖包在最外層，`--check` 也包進去：那條路會 import 產品程式碼，
    # 而另一支正在把產品改壞——它讀到的會是破壞後的樣子。
    try:
        with _the_only_one_touching_the_tree():
            raise SystemExit(main())
    except AnotherRunHasTheTree as clash:
        print(str(clash), file=sys.stderr)
        # **3，不是 2。**argparse 的「參數打錯」就是 2——
        # 共用一個離開碼等於「你打錯了」跟「有人在跑」分不出來。
        raise SystemExit(3) from None
