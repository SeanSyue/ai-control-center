"""git 的兩個出廠預設值，會把這一輪的產出弄不見、或弄成亂碼。

## 改壞的樣子（一）：`git add` 過就不算數了

`git diff` 不給參數比的是**工作區 vs 索引**。廠商的 CLI 邊做邊 `git add`
是常態，於是一格真的寫完了程式、順手 add 起來，`get_git_diff()` 回空字串。

使用者會看到：拍板那一格讀到「（這一輪沒有產出可以比對。）」，然後**照樣
蓋章、整輪 COMPLETED**；而同一輪的「變更檔案」那張單子上，那個檔案好好
列在上面。**兩張單子講兩件事，而兩張都看起來很正常。**

這跟未追蹤檔案那個洞是同一種病的另一個實例——修了未追蹤那一半，這一半
還活著。

## 改壞的樣子（二）：中文檔名變成跳脫字串

`core.quotepath` 出廠開著，`git status` 於是把 `自用驗收清單.md` 印成
`"\\350\\207\\252\\347\\224\\250..."`。

使用者會看到：「這一輪動到這些檔案」列出一串反斜線數字，**看不出來是哪一
個檔案**——而這個專案的檔名本來就是中文的。同一個字串還會被拿去跟排除清
單比對（`is_generated_path()`），跳脫過的路徑一條都比不中。

## 沒有 commit 過的專案

`git diff HEAD` 在還沒有任何一次 commit 的專案上會**直接失敗**，而失敗那
條路上整段新檔案報告不會被組出來。一個全新專案的第一輪產出必然全部是新
檔案——那一輪會退回一句錯誤訊息。所以那種專案要改跟 `--cached` 比。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.orchestrator import Orchestrator
from app.i18n import t


def write(path: Path, text: str) -> None:
    """一律寫成 UTF-8 的 `\\n`（`write_text()` 在 Windows 上會換成 `\\r\\n`）。"""
    path.write_bytes(text.encode("utf-8"))


def git(project: Path, *args: str) -> None:
    completed = subprocess.run(
        [
            "git",
            "-c",
            "user.email=aicc@example.com",
            "-c",
            "user.name=aicc",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, completed.stderr


def make_project(tmp_path: Path, *, commit: bool = True) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    write(project / ".aicc" / "task.md", "# 任務\n")
    write(project / "existing.py", "原本就有的一行\n")
    git(project, "init", "-q")
    if commit:
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "first")
    return project


def test_a_new_file_that_was_already_git_added_is_still_this_rounds_output(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    write(project / "staged_new.py", "這一輪寫的一行\n")
    # 廠商的 CLI 常常自己做這一步。
    git(project, "add", "staged_new.py")

    orchestrator = Orchestrator(project)
    diff = orchestrator.get_git_diff()

    assert "staged_new.py" in diff
    assert "這一輪寫的一行" in diff
    # 兩張單子要講同一件事。
    assert orchestrator.get_changed_files() == ["staged_new.py"]


def test_an_edit_that_was_already_git_added_is_still_this_rounds_output(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    write(project / "existing.py", "原本就有的一行\n這一輪加上去的一行\n")
    git(project, "add", "existing.py")

    diff = Orchestrator(project).get_git_diff()

    assert "+++ b/existing.py" in diff
    assert "+這一輪加上去的一行" in diff


def test_a_project_that_has_never_committed_still_reports_what_it_made(
    tmp_path: Path,
) -> None:
    # 全新專案的第一輪：一次 commit 都還沒有，產出必然全部是新檔案。
    project = make_project(tmp_path, commit=False)
    content = "第一輪寫出來的東西\n"
    write(project / "first_thing.py", content)

    diff = Orchestrator(project).get_git_diff()

    assert "GIT_DIFF_ERROR" not in diff
    assert (
        t(
            "git.new_file.header",
            path="first_thing.py",
            lines=1,
            chars=len(content),
        )
        in diff
    )
    assert "第一輪寫出來的東西" in diff


def test_a_chinese_file_name_stays_a_file_name_on_the_changed_list(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    write(project / "新的說明.md", "這一輪新寫的說明。\n")

    changed = Orchestrator(project).get_changed_files()

    assert changed == ["新的說明.md"], changed
    # 反斜線跳脫的路徑跟真檔名長得完全不一樣，而下游（提示詞、排除清單
    # 比對）拿到的就是這個字串。
    assert not any("\\3" in item for item in changed)


def test_a_renamed_file_is_listed_once_by_its_new_name(tmp_path: Path) -> None:
    """改名那一筆，機器讀的格式跟人讀的**左右相反**。

    人讀的是 `舊 -> 新`，`-z` 是「新\\0舊」。照人讀的那一種解析，這一輪的
    單子上會出現一個**已經不存在的檔名**，而且是兩筆。
    """
    project = make_project(tmp_path)
    git(project, "mv", "existing.py", "renamed.py")

    changed = Orchestrator(project).get_changed_files()

    assert changed == ["renamed.py"], changed
