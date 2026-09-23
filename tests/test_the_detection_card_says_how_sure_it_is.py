"""「AICC 查到的專案設定」：每一格都要講得出憑什麼。

`docs/門檻二-別人打得開.md`：

> 開一個新專案 → 精靈問**兩題半**，其餘用卡填好給他點「對」

這張卡上的東西**是猜的**，而這份檔案守的是「猜得出來的東西要說自己
是猜的」。兩句話長得一模一樣：

* 「測試指令：`pytest`」因為看到 `pytest.ini`——**他信得過**
* 「測試指令：`pytest`」因為大部分 Python 專案都這樣——**他應該要改**

除非卡片自己說出來，否則使用者分不出來。
"""
from __future__ import annotations

from app.services.project_detection import (
    CONFIDENCE_KEYS,
    GUESSED,
    LIKELY,
    SURE,
    UNKNOWN,
    detect,
    worth_checking,
)


def _find(findings, what):
    for f in findings:
        if f.what == what:
            return f
    raise AssertionError(f"卡片上沒有「{what}」這一格：{[f.what for f in findings]}")


def test_every_cell_says_what_it_is_and_why(tmp_path) -> None:
    """**每一格都要有名字、把握、憑什麼。**

    少了「憑什麼」，這張卡就變成一份不知道從哪來的設定——而使用者
    唯一能做的就是全部相信或全部重填。
    """
    empty = tmp_path / "空的"
    empty.mkdir()

    # **兩種資料夾都要走一次。**只驗空的那種，「查到證據」那幾條分支
    # 一次都不會執行——負向驗證是這樣抓到的：把「憑什麼」拿掉之後
    # D6 是綠的，因為那一行在空資料夾裡跑不到。
    filled = tmp_path / "有東西的"
    filled.mkdir()
    (filled / "pyproject.toml").write_text("", encoding="utf-8")
    (filled / "pytest.ini").write_text("", encoding="utf-8")
    (filled / ".git").mkdir()
    (filled / ".aicc").mkdir()

    for project in (empty, filled):
        for finding in detect(project):
            assert finding.what.strip(), f"{project.name}：有一格沒有名字"
            assert finding.because.strip(), (
                f"{project.name}：「{finding.what}」沒有說憑什麼"
            )
            assert finding.confidence in CONFIDENCE_KEYS, (
                f"「{finding.what}」的把握是 {finding.confidence!r}，"
                "而那不是四個值之一——有預設值的分類等於沒有分類"
            )


def test_the_project_name_is_never_blank_because_of_a_relative_path(tmp_path, monkeypatch) -> None:
    """`Path(".").name` 是**空字串**。

    卡片上一格空白的「專案名稱」看起來會像「AICC 查不到你的專案叫什麼」
    ——一個純粹由相對路徑造成的假象。這行 bug 是第一次跑起來就看到的。
    """
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    assert _find(detect(Path(".")), "專案名稱").value == tmp_path.name


def test_a_marker_file_makes_it_sure_not_a_guess(tmp_path) -> None:
    """看到證據就要說看到了。"""
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    cell = _find(detect(tmp_path), "怎麼確認做完了")
    assert cell.value == "pytest"
    assert cell.confidence in (SURE, LIKELY)
    assert "pytest.ini" in cell.because, "沒有說是哪個檔讓它這樣猜的"


def test_no_marker_means_unknown_not_a_made_up_command(tmp_path) -> None:
    """**沒有證據的時候不准填一個看起來很合理的指令。**

    這一格猜錯的代價很具體：一個永遠不會失敗的驗證指令，會讓每一次執行
    都拿到綠燈——而那正是【失效的檢查會繼續給綠燈】那條。
    """
    cell = _find(detect(tmp_path), "怎麼確認做完了")
    assert cell.value == "", f"沒有任何證據卻填了「{cell.value}」"
    assert cell.confidence is UNKNOWN or cell.confidence == UNKNOWN


def test_no_git_says_cannot_tell_not_says_none(tmp_path) -> None:
    """**「查不到」跟「沒有」是兩件事。**

    斷言「沒有版本控制」會讓他以為 AICC 檢查過了——而 repo 的根可能在
    上一層，或者他用的是別的工具。這個專案已經因為同一個誤會重寫過一次
    已經存在的文件。
    """
    cell = _find(detect(tmp_path), "版本控制")
    assert cell.confidence == UNKNOWN
    assert cell.value == ""
    assert "沒有" not in cell.value


def test_the_cells_he_should_look_at_are_singled_out(tmp_path) -> None:
    """全部平鋪的話，那格空的驗證指令會跟其他四格一樣不起眼。

    而它是唯一一格填錯會讓每次執行都拿到假綠燈的。
    """
    flagged = worth_checking(detect(tmp_path))
    assert any(f.what == "怎麼確認做完了" for f in flagged), (
        "空的驗證指令沒有被標出來"
    )

    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    flagged = worth_checking(detect(tmp_path))
    assert not any(f.what == "怎麼確認做完了" for f in flagged), (
        "查到證據了還在標它——每一格都標等於沒有標"
    )


def test_detection_runs_nothing_and_writes_nothing(tmp_path) -> None:
    """**偵測不准跑他專案裡的程式碼，也不准留下任何東西。**

    「跑一下 `pytest --collect-only` 就知道了」是一條很好走的滑坡，
    而那表示 AICC 在使用者按下同意之前就執行了他專案裡的程式。
    """
    import ast
    from pathlib import Path

    tree = ast.parse(
        Path("app/services/project_detection.py").read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            assert name.split(".")[0] not in ("subprocess", "os", "shutil", "socket"), (
                f"偵測開始 import {name} 了——它只該看看資料夾裡有什麼"
            )

    before = sorted(p.name for p in tmp_path.iterdir())
    detect(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == before, "偵測留下了東西"


def test_no_confidence_level_is_dead(tmp_path) -> None:
    """**四個等級都要有人產得出來。**

    一個沒有任何函式回得出來的等級，等於在說一件產品不會做的事——
    而它 2026-08-24 之前就是那樣：`GUESSED` 五支產生函式一條都產不出來
    （頁面設計那一輪查到的）。

    而它不只是「死掉的常數」：`worth_checking()` 只標 `GUESSED` 與
    `UNKNOWN`，所以「看到 pyproject.toml 就填 pytest」那一格**從來沒有
    被標起來過**——而那正是最值得他看一眼的一格。
    """
    produced = set()

    # 每一種標記檔各造一個資料夾，看它產出哪一級
    for marker in ("pytest.ini", "tox.ini", "pyproject.toml", "package.json", "Cargo.toml"):
        folder = tmp_path / marker.replace(".", "_")
        folder.mkdir()
        (folder / marker).write_text("", encoding="utf-8")
        for finding in detect(folder):
            produced.add(finding.confidence)

    empty = tmp_path / "空的"
    empty.mkdir()
    for finding in detect(empty):
        produced.add(finding.confidence)

    from app.services.project_detection import CONFIDENCE_KEYS

    dead = sorted(set(CONFIDENCE_KEYS) - produced)
    assert not dead, (
        f"這幾級沒有任何路產得出來：{dead}。"
        "一個沒有人產得出來的等級，等於在說一件產品不會做的事——"
        "要嘛讓它活起來，要嘛拿掉它。"
    )


def test_a_generic_marker_is_a_guess_not_a_likelihood(tmp_path) -> None:
    """`pyproject.toml` 只說「這是 Python 專案」，**一個字都沒說測試怎麼跑**。

    填 pytest 是照慣例，不是照證據——所以它要被標起來給他看一眼。
    改壞的樣子：那一格看起來跟「看到 pytest.ini」一樣可信。
    """
    from app.services.project_detection import GUESSED

    project = tmp_path / "python 專案"
    project.mkdir()
    (project / "pyproject.toml").write_text("", encoding="utf-8")

    cell = _find(detect(project), "怎麼確認做完了")
    assert cell.value == "pytest"
    assert cell.confidence == GUESSED
    assert cell.is_a_guess, "照慣例填的那一格沒有被標起來"
