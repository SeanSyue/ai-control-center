"""「AICC 查到的專案設定」：不問他就知道的那幾件事。

## 這張卡為什麼存在

`docs/門檻二-別人打得開.md`：

> 開一個新專案 → 精靈問**兩題半**，其餘用卡填好給他點「對」

也就是說：**能查出來的事情不要問他**。他知道這個資料夾裡有什麼嗎？
知道。但要他打字告訴 AICC「這是一個 Python 專案、測試指令是 pytest」，
是把一件機器一秒鐘做得完的事退回給他。

名字是老闆挑的（2026-08-24）。他否決過我三個名字，理由是
**「我根本不知道我在看的是甚麼表格和資訊」**——名字要先回答
「這是什麼」，好處是第二層。

## 每一格都要講得出「憑什麼」

`Finding.because` 不是裝飾。這張卡上的東西**是猜的**，而使用者要能
一眼看出哪一格值得懷疑：

* 「測試指令：`pytest`」——憑什麼？因為看到 `pytest.ini`。**這個他信得過。**
* 「測試指令：`pytest`」——憑什麼？因為大部分 Python 專案都這樣。
  **這個他應該要改。**

兩者長得一模一樣，除非卡片自己說出來。

## 「查不到」跟「沒有」是兩件事

`Finding.value` 是空字串的時候，卡片要畫的是**「AICC 查不到」**，
不是「沒有」。這個專案已經因為那個誤會重寫過一次已經存在的文件。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.i18n import t

# 猜的把握。**三個值，沒有預設**——有預設值的分類等於沒有分類。
SURE = "sure"          # 看到了證據，這件事就是這樣
LIKELY = "likely"      # 有證據但不只一種解釋
GUESSED = "guessed"    # 沒有證據，照慣例填的
UNKNOWN = "unknown"    # 查不到。**不是「沒有」。**

# 把握 → 介面文字的 key。**存 key 不存字**：這個字典在模組載入的當下就
# 建好了，如果它存的是取出來的字，那些字就永遠是 import 那一刻的語言。
# （今天早上 `LoginRoute.typed` 才因為同一個形狀被抓到過一次。）
CONFIDENCE_KEYS = {
    SURE: "detect.confidence.sure",
    LIKELY: "detect.confidence.likely",
    GUESSED: "detect.confidence.guessed",
    UNKNOWN: "detect.confidence.unknown",
}


def confidence_label(confidence: str) -> str:
    """「查到了」／「看起來是」／「猜的」／「查不到」。

    查不到的把握**丟例外**，不是回一個空字串——卡片上一格沒有把握標示的
    資料，看起來就跟「查到了」一樣可信。
    """
    return t(CONFIDENCE_KEYS[confidence])


@dataclass(frozen=True)
class Finding:
    """卡片上的一格。"""

    what: str          # 這是什麼（「測試指令」）
    value: str         # 查到的值。空字串＝查不到
    because: str       # 憑什麼——**證據，不是理由**
    confidence: str

    @property
    def is_a_guess(self) -> bool:
        """使用者應不應該特別看這一格。"""
        return self.confidence in (GUESSED, UNKNOWN)


# 一個標記檔對到一組猜測。順序有意義：**先比對到的贏**，
# 所以專屬設定檔要排在通用的前面（`pytest.ini` 比 `pyproject.toml` 明確）。
# 把握分三種，而**分界線是「這個檔案有沒有指名測試怎麼跑」**：
#
# * `pytest.ini` / `Cargo.toml` / `go.mod` / `pom.xml` → **SURE**。
#   這些檔案本身就是那個測試工具的設定檔，看到它就是看到答案。
# * `tox.ini` → **LIKELY**。它講的是「怎麼建環境跑測試」，而底下幾乎都是
#   pytest，但不保證。
# * `pyproject.toml` / `package.json` / `Gemfile` → **GUESSED**。
#   它們只說「這是 Python／JS／Ruby 專案」，**一個字都沒說測試怎麼跑**。
#   填 pytest 是照慣例，不是照證據。
#
# 這個分界線 2026-08-24 才畫出來——在那之前那三個通用檔案標成 LIKELY，
# 而 `GUESSED` 這一級**五支函式一條都產不出來**（頁面設計那一輪查到的）。
# 一個沒有人產得出來的等級，等於在說一件產品不會做的事。
#
# 而它不只是「死掉的常數」：`worth_checking()` 只標 GUESSED 與 UNKNOWN，
# 所以「看到 pyproject.toml 就填 pytest」那一格**從來沒有被標起來過**
# ——而那正是最值得他看一眼的一格（填錯會讓每次執行都拿到假綠燈）。
_TEST_MARKERS: tuple[tuple[str, str, str], ...] = (
    ("pytest.ini", "pytest", SURE),
    ("tox.ini", "pytest", LIKELY),
    ("pyproject.toml", "pytest", GUESSED),
    ("package.json", "npm test", GUESSED),
    ("Cargo.toml", "cargo test", SURE),
    ("go.mod", "go test ./...", SURE),
    ("Gemfile", "bundle exec rspec", GUESSED),
    ("pom.xml", "mvn test", SURE),
)

_LANGUAGE_MARKERS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "Python"),
    ("requirements.txt", "Python"),
    ("package.json", "JavaScript / TypeScript"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("Gemfile", "Ruby"),
    ("pom.xml", "Java"),
)


def _name(project: Path) -> Finding:
    """資料夾的名字。**這一格永遠是 SURE**——它不是猜的。

    先 `resolve()`：`Path(".").name` 是**空字串**，而卡片上一格空白的
    「專案名稱」看起來會像「AICC 查不到你的專案叫什麼」——一個純粹由
    相對路徑造成的假象。（這行 bug 是第一次跑起來就看到的。）
    """
    resolved = project.expanduser().resolve()
    return Finding(
        t("detect.cell.name"),
        resolved.name,
        t("detect.because.folder_is_called", path=resolved),
        SURE,
    )


def _version_control(project: Path) -> Finding:
    if (project / ".git").exists():
        return Finding(
            t("detect.cell.version_control"),
            "Git",
            t("detect.because.has_git"),
            SURE,
        )
    # **不回「沒有版本控制」**——回「查不到」。他可能用的是別的，
    # 或者 repo 的根在上一層。斷言「沒有」會讓他以為 AICC 檢查過了。
    return Finding(
        t("detect.cell.version_control"),
        "",
        t("detect.because.no_git"),
        UNKNOWN,
    )


def _language(project: Path) -> Finding:
    for marker, language in _LANGUAGE_MARKERS:
        if (project / marker).is_file():
            return Finding(
                t("detect.cell.language"),
                language,
                t("detect.because.saw_marker", marker=marker),
                LIKELY,
            )
    return Finding(
        t("detect.cell.language"), "", t("detect.because.no_known_marker"), UNKNOWN
    )


def _verify_command(project: Path) -> Finding:
    """怎麼確認「做完了」。**這一格最值得他看**。

    AICC 跑完之後靠它判斷成功失敗，而猜錯的代價很具體：一個永遠不會
    失敗的驗證指令，會讓每一次執行都拿到綠燈。
    """
    for marker, command, confidence in _TEST_MARKERS:
        if (project / marker).is_file():
            return Finding(
                t("detect.cell.verify"),
                command,
                t("detect.because.saw_marker", marker=marker),
                confidence,
            )
    return Finding(
        t("detect.cell.verify"), "", t("detect.because.no_test_config"), UNKNOWN
    )


def _already_a_project(project: Path) -> Finding:
    if (project / ".aicc").is_dir():
        return Finding(
            t("detect.cell.used_before"),
            t("detect.value.used_before"),
            t("detect.because.has_aicc"),
            SURE,
        )
    return Finding(
        t("detect.cell.used_before"),
        t("detect.value.not_used_before"),
        t("detect.because.no_aicc"),
        SURE,
    )


def detect(project_path: Path) -> list[Finding]:
    """這張卡上的每一格。

    **不碰網路、不跑任何指令、不寫任何檔**——它只是看看資料夾裡有什麼。
    跑指令來偵測是一條很好走的滑坡（「跑一下 `pytest --collect-only` 就知道
    了」），而那表示 AICC 在使用者按下同意之前就執行了他專案裡的程式碼。
    """
    project = Path(project_path)
    return [
        _name(project),
        _version_control(project),
        _language(project),
        _verify_command(project),
        _already_a_project(project),
    ]


def worth_checking(findings: list[Finding]) -> list[Finding]:
    """哪幾格他應該自己看一眼。

    畫面用這個決定要不要把某一格標起來——**全部平鋪的話，那格空的
    驗證指令會跟其他四格一樣不起眼**，而它是唯一一格填錯會讓每次執行
    都拿到假綠燈的。
    """
    return [f for f in findings if f.is_a_guess]
