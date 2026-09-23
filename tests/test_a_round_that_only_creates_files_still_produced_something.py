"""只新建檔案的一輪，不是一輪沒有人做事。

改壞的樣子：`get_git_diff()` 只跑 `git diff`，而 `git diff` 看不到 git 還不
認得的檔案。一格真的做了事、產出**全部是新建檔案**的時候（開一個新模組、
寫一份新文件——這是最常見的一種產出），它回空字串。

使用者會看到：拍板那一格讀到「（這一輪沒有產出可以比對。）」，然後**照樣
蓋章、整輪 COMPLETED**。他打開資料夾，新檔案好好躺在那裡——真的做了事跟
完全沒做事，在最後一關長得一模一樣。

另一半守相反的方向：`.aicc/knowledge/` 那八份與 `.aicc/project-brief.md`
是 **AICC 自己**每一輪在寫的東西。專案把 `.aicc/` 一起進版控的時候，它們會
混進「這一輪做出來的東西」，於是拍板那一格對著引擎自己的筆記蓋章。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.core import orchestrator as orchestrator_module
from app.core.orchestrator import Orchestrator
from app.i18n import t


def write(path: Path, text: str) -> None:
    """一律寫成 UTF-8 的 `\\n`。

    `write_text()` 在 Windows 上會把 `\\n` 換成 `\\r\\n`，而這幾個測試會去比
    「它說這個檔案幾個字」——換行被偷偷加長一個字元的話，比對會在一個跟這
    件事無關的地方失敗。
    """
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


def make_project(tmp_path: Path) -> Path:
    """一個已經 commit 過一次的專案：有追蹤中的檔案，才分得出「改」跟「新增」。"""
    project = tmp_path / "project"
    (project / ".aicc" / "knowledge").mkdir(parents=True)
    (project / ".aicc" / "conflicts").mkdir()
    write(project / ".aicc" / "task.md", "# 任務\n新建檔案也是產出。\n")
    write(project / ".aicc" / "knowledge" / "DECISIONS.md", "已經定案的事。\n")
    # `.aicc/` **進了版控**的專案——這一整條規則就是為它存在的。
    # AICC 自己會寫一份 `.aicc/.gitignore`（runs/、timeline/、conflicts/…），
    # 但那份只擋得住還沒有被追蹤的檔案：先 commit 過 `.aicc/` 的專案，
    # 這些檔案照樣每一輪出現在 `git diff` 裡。所以這裡刻意在那份
    # `.gitignore` 出生之前就把它們 commit 進去。
    write(project / ".aicc" / "conflicts" / "one.json", "{}\n")
    write(project / "existing.py", "原本就有的一行\n")
    git(project, "init", "-q")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "first")
    return project


def test_a_file_that_did_not_exist_before_is_part_of_what_this_round_made(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    content = "def 新功能():\n    return 42\n"
    write(project / "new_module.py", content)

    diff = Orchestrator(project).get_git_diff()

    # 檔名、內容、以及「這是新增的」都要在同一段文字裡：拍板那一格讀的是
    # 這一個字串，沒有第二個欄位可以去看。
    assert (
        t(
            "git.new_file.header",
            path="new_module.py",
            lines=2,
            chars=len(content),
        )
        in diff
    )
    assert "return 42" in diff


def test_the_stamping_seat_can_tell_a_new_file_from_an_edited_one(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    write(project / "existing.py", "原本就有的一行\n這一輪加上去的一行\n")
    write(project / "brand_new.py", "整個檔案都是這一輪寫的\n")

    diff = Orchestrator(project).get_git_diff()

    # 改過的走 diff。
    assert "+++ b/existing.py" in diff
    assert "+這一輪加上去的一行" in diff

    # 新增的走新增那一段——而**改過的不准出現在那一段裡**，不然「新增了
    # 一個檔案」跟「改了一行」在最後一關又混成同一件事。
    new_section = diff[diff.index(t("git.new_files.header", count=1)):]
    assert "brand_new.py" in new_section
    assert "整個檔案都是這一輪寫的" in new_section
    assert "existing.py" not in new_section


def test_a_new_file_named_in_chinese_still_shows_its_content(
    tmp_path: Path,
) -> None:
    # git 預設會把非 ASCII 檔名印成 `"\346\226\260..."`，而那個字串接下來
    # 要拿去讀檔——讀不到的話，一份真的寫出來的文件會變成一行「讀不到」。
    project = make_project(tmp_path)
    write(project / "新的說明.md", "這一輪新寫的說明。\n")

    diff = Orchestrator(project).get_git_diff()

    assert "新的說明.md" in diff
    assert "這一輪新寫的說明。" in diff


def test_what_aicc_writes_about_itself_is_not_what_this_round_produced(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    # 引擎自己每一輪都會動的東西：一份是追蹤中的（走 diff），
    # 兩份是新建的（走新增那一段），一份在只有其中一邊排掉過的資料夾裡。
    write(
        project / ".aicc" / "knowledge" / "DECISIONS.md",
        "已經定案的事。\n引擎這一輪自己補的一句。\n",
    )
    write(
        project / ".aicc" / "knowledge" / "NEXT_STEPS.md",
        "引擎自己寫的下一步。\n",
    )
    write(project / ".aicc" / "project-brief.md", "引擎自己寫的計畫書。\n")
    write(project / ".aicc" / "conflicts" / "one.json", "{\"衝突\": 1}\n")
    # 真的有人做的事。
    write(project / "real_work.py", "有人真的寫了這一行\n")

    orchestrator = Orchestrator(project)
    diff = orchestrator.get_git_diff()

    assert "real_work.py" in diff
    assert "有人真的寫了這一行" in diff
    for engine_own in (
        "DECISIONS.md",
        "NEXT_STEPS.md",
        "project-brief.md",
        "conflicts",
    ):
        assert engine_own not in diff

    # 兩邊要看到**同一件事**。清單各抄一份的時候，「變更檔案」與 diff 會
    # 講兩件不同的事，而兩張單子都看起來很正常。
    assert orchestrator.get_changed_files() == ["real_work.py"]


def test_a_long_new_file_says_how_much_it_left_out(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    kept = orchestrator_module.NEW_FILE_MAX_LINES
    total = kept + 200
    write(
        project / "long_new.py",
        "".join(f"第 {number} 行\n" for number in range(1, total + 1)),
    )

    diff = Orchestrator(project).get_git_diff()

    assert "第 1 行" in diff
    # 真的截了……
    assert f"第 {kept + 1} 行" not in diff
    # ……而且在同一段文字裡講了截掉多少。少了這一句，「只放了前面幾行」
    # 跟「這個檔案就這麼短」在下游長得一模一樣。
    assert (
        t("git.new_file.cut_lines", shown=kept, dropped=total - kept)
        in diff
    )


def test_when_the_whole_section_runs_out_of_room_it_still_names_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = make_project(tmp_path)
    write(project / "a_first.py", "早到的內容\n" * 20)
    later = "晚到的內容\n"
    write(project / "z_later.py", later)
    monkeypatch.setattr(orchestrator_module, "NEW_FILE_TOTAL_CHARS", 40)

    diff = Orchestrator(project).get_git_diff()

    # 排在後面的檔案內容放不下了——**但它的存在不准消失**，
    # 而且要講得出為什麼沒有內容。
    assert "晚到的內容" not in diff
    assert (
        t("git.new_file.no_room", path="z_later.py", chars=len(later)) in diff
    )
