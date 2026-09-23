"""規則分三層：每一格只收到「底座＋自己那塊」。

定案原文（2026-08-21）：

> 產品底座（內建、改不掉）／專案規則（使用者自己訂）／角色積木（跟角色綁）。
> 每個 AI 只收到「**底座＋自己的角色積木**」。
> 否決：把全部規則塞給每個角色。**不變式：不准全量下發。**

## 判準不是「重要／不重要」

那個判準沒有人講得清楚，而且每個人講出來的不一樣。這裡用的判準是
**這一份 AI 會不會自己去讀**：

| | 怎麼送 | 為什麼 |
|---|---|---|
| **不准做的事** | **逐字送進提示詞** | **AI 不會主動去讀一份限制它的檔案。**點名一份規則書，等於相信它會自己去找理由不做某件事 |
| **背景與已定案** | **點名 ＋ 索引，它自己去讀** | 它做這件事的時候真的需要，所以它會去讀；而且讀到的是**當下最新的**那一份，不是派工那一刻的快照 |

## 跟「不准截斷」那條定案不牴觸

一個講**完整性**（送出去的那一份不准被剪掉半條安全規則），一個講**選擇**
（哪幾份該送）。這裡只做選擇；被選中的那幾份照樣一個字都不切。
"""
from __future__ import annotations

from app.i18n import t

from app.core.lineup import DUTY_APPROVE, DUTY_PLAN, DUTY_REVIEW, DUTY_WORK

# ---------------------------------------------------------------------------
# 三層
# ---------------------------------------------------------------------------

# 產品底座：內建、改不掉。**它不是檔案**——是寫在
# `prompt_builder.claude_safety_instruction()` 與 `GATE_FLOOR_CLAUSES` 裡的
# 那幾句，每一個專案都一樣，使用者改不掉。
#
# 定案（2026-08-21）的不變式：**底座只放保護使用者的，不放限制使用者的。**
# 「不對外發佈」那種**本專案的紀律**屬於專案規則層，不屬於這裡。
LAYER_BASE = "base"
# 專案規則：這個專案的檔案，`.aicc/knowledge/` 底下那幾份。
#
# **`SECURITY_MODEL.md` 與 `OPEN_QUESTIONS.md` 也在這一層**——2026-08-23
# L2 那一版把它們標成底座，那是錯的：它們是**每個專案自己一份**的檔案，
# 換一個專案內容就不一樣。它們特別的地方不是層級，是**送法**（逐字，
# 不是點名），而送法跟層級是兩件事。
LAYER_PROJECT = "project"
# 角色積木：跟**職責**走。人格那一半在 `prompt_builder.STEP_PERSONAS`，
# 記憶那一半是底下的點名。
LAYER_ROLE = "role"

# 產品底座住在哪。寫成常數是為了讓「底座不准放本專案的紀律」那條檢查
# 有一個明確的對象——沒有對象的檢查只能靠人記得去看。
BASE_LIVES_IN = (
    "app/core/prompt_builder.py",
    "app/services/ai_settings.py",
)

# 這幾個詞出現在**產品底座**裡就是越界：它們是這個專案自己的紀律，
# 不是每一個使用者都需要的東西。
#
# 判準的原文：「如果有任何一種使用者需要做這件事，它就不該在底座。」
# 展開來就是——**底座只放保護使用者的，不放限制使用者的。**
# `NOT_IN_THE_BASE` 搬去 `app/core/prompt_markers.py` 了（2026-08-24）：
# 它是**拿來比對底座提示詞的**，跟「剝掉開場白」那一組同一種東西——
# 翻掉它就比不到任何東西，而那條檢查會安靜地變成恆真。
from app.core.prompt_markers import NOT_IN_THE_BASE  # noqa: E402

# 存 key 不存字：模組層取出來的話，那幾個字會在 import 的當下凍住。
LAYER_LABEL_KEYS = {
    LAYER_BASE: "layer.base",
    LAYER_PROJECT: "layer.project",
    LAYER_ROLE: "layer.role",
}


def layer_label(layer: str) -> str:
    """那一層在畫面上叫什麼。"""
    from app.i18n import t

    return t(LAYER_LABEL_KEYS[layer])

# ---------------------------------------------------------------------------
# 底座：逐字送進每一格的提示詞
# ---------------------------------------------------------------------------

# 這兩份是「**不准做的事**」，而 AI 不會主動去讀一份限制它的檔案。
#
# 它們本來就在「不准截斷」名單上，而那條名單的理由跟這裡是同一個：
# **被截斷的安全規則讀起來跟完整的一模一樣**，而清單型的硬界線少一項就等於
# 沒有那一項。
PUSHED_VERBATIM = (
    "SECURITY_MODEL.md",
    "OPEN_QUESTIONS.md",
)
# 舊名字。2026-08-23 改掉，因為它把「送法」講成了「層級」——這兩份是專案的
# 檔案（每個專案自己一份），不是產品底座。留一個別名是為了不讓外面的呼叫端
# 在同一次改動裡跟著壞。
BASE_FILES = PUSHED_VERBATIM

# 使用者自己訂的那一份。逐字送，理由跟底座同一個——它也是「不准做的事」。
#
# 放在 `.aicc/knowledge/` 跟其餘八份一起，是為了共用既有的編輯介面
# （共享記憶那一頁的下拉是照 `MEMORY_FILES` 長出來的）。多開一個地方
# 等於多一個使用者要記得去看的地方，而他不會記得。
PROJECT_RULES_FILE = "PROJECT_RULES.md"

# ---------------------------------------------------------------------------
# 角色積木：跟職責走，不是跟角色名走
# ---------------------------------------------------------------------------

# **跟職責走不跟名字走。**使用者把那一格取名「實作」之後，寫死 `author` 的
# 東西會全部失效——這個專案已經因為同一件事修過四次。
_READ_BEFORE_STARTING = {
    DUTY_PLAN: ("DECISIONS.md", "NEXT_STEPS.md"),
    DUTY_WORK: ("DECISIONS.md", "ARCHITECTURE.md"),
    DUTY_REVIEW: ("DECISIONS.md", "ARCHITECTURE.md"),
    DUTY_APPROVE: ("DECISIONS.md", "NEXT_STEPS.md"),
}

# 職責認不出來的時候給什麼。**不是空的**：認不出來就給最保守的那一份
# （已定案的事），因為「不知道這一格在做什麼」跟「這一格不需要背景」是兩件
# 完全不同的事，而空的那個選項會讓它們長得一樣。
_FALLBACK = ("DECISIONS.md",)


def pushed_files(has_project_rules: bool = False) -> tuple[str, ...]:
    """逐字送進提示詞的那幾份。"""
    files = list(BASE_FILES)
    if has_project_rules:
        files.append(PROJECT_RULES_FILE)
    return tuple(files)


def read_before_starting(duty: str) -> tuple[str, ...]:
    """這一格開工之前要自己去讀的那幾份。

    回傳的是**點名**，不是內容。點名這一步存在的理由：短指標唯一的風險是
    「AI 可能不去讀」，只放索引不點名等於把那個風險留著。
    """
    return _READ_BEFORE_STARTING.get(duty, _FALLBACK)


def layer_of(file_name: str) -> str:
    """這一份屬於哪一層。

    **`.aicc/knowledge/` 底下的每一份都是專案規則層**，包括那兩份逐字送的
    ——它們每個專案自己一份，換個專案內容就不一樣。**產品底座不是檔案。**
    """
    return LAYER_PROJECT


# ---------------------------------------------------------------------------
# 索引：沒有被逐字送的那幾份，一份一行，講它裡面放什麼
# ---------------------------------------------------------------------------

# `WHAT_IS_INSIDE` 搬去 `app/core/prompt_text.py` 了（2026-08-24）。
#
# 那幾句是**送給 AI 看的**，跟介面文字照不同的理由改：介面翻成英文的那天，
# 它們不該跟著變——那會讓 AICC 突然改用英文跟兩支 CLI 講話，而那是一個
# **該被決定的事，不是介面翻譯的副作用**。
from app.core.prompt_text import WHAT_IS_INSIDE  # noqa: E402


def pointer_section(
    duty: str, folder: str, available: tuple[str, ...]
) -> str:
    """索引 ＋ 點名。**點名那一行是這一段存在的理由。**

    只給索引不點名，等於把「AI 可能不去讀」那個風險原封不動留著——它會看到
    一張清單，然後照它原本就想做的事去做。點名之後那件事變成「它跳過了一個
    明確的指示」，而那是事後查得到的。

    `available` 是這個專案**真的有**的那幾份。列出不存在的檔案，AI 會去讀，
    讀不到，然後開始猜為什麼——那一輪就浪費在一個我們自己造出來的謎題上。
    """
    listed = [
        name
        for name in WHAT_IS_INSIDE
        if name in available and name not in pushed_files(True)
    ]
    if not listed:
        return ""

    lines = [
        f"這個專案的長期記憶是純文字，放在 `{folder}`：",
        "",
    ]
    width = max(len(name) for name in listed)
    for name in listed:
        lines.append(f"  {name.ljust(width)}  {WHAT_IS_INSIDE[name]}")

    must_read = [name for name in read_before_starting(duty) if name in available]
    if must_read:
        lines.append("")
        lines.append(
            t("rules.read_before_starting") + "、".join(must_read) + "。**"
        )
    return "\n".join(lines)
