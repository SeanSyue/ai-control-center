"""沒有單元測試的工作，怎麼知道做完了。

## 為什麼非有不可

`docs/AI協作手冊.md` 第六節：

> 上面所有的規矩都預設「跑個指令就知道對不對」。但**大部分辦公室工作不是
> 這樣**——圖好不好看、文章寫得好不好、簡報有沒有質感，都沒有單元測試。
>
> **這是 AICC 從「幫工程師跑測試」變成「幫辦公室的人做事」的關鍵一步。**

而今天的 AICC 在那種專案上會**在派工之前就停住**：`steps/verification.declared()`
問「這個專案有沒有說過要怎麼驗」，沒有 `verify_command` 就不讓它開始。
對一個剪片的案子來說，那是一道沒有出口的牆。

## 這一份不是「跳過驗證」，是**另一種驗證**

**一個沒有標準的案子照樣不准跑。**「有預設值的分類等於沒有分類」在這裡的
樣子是：如果「沒宣告」可以靜靜地變成「那就算過吧」，那麼每一次執行都會
拿到綠燈，而使用者要到第十次才發現沒有人在看。

所以：`verify_command` **或** 這一份，兩個都沒有還是停。

## 手冊那六步裡，哪幾步是機器的

| 步 | 誰做 |
|---|---|
| 1 先讓 AI 跑一輪，測出基準 | 人 |
| 2 每個產出親自看，把皺眉的**具體理由**寫下來 | 人 |
| 3 把理由**分類成幾個維度** | 人 |
| 4 每個維度寫**具體案例**，反例要點名 | 人 |
| 5 **每個維度要給多個案例** | **機器擋**（見 `MINIMUM_EXAMPLES`）|
| 6 餵給審查方，**前幾輪要人工檢查** | **機器提醒**（見 `still_learning`）|

第 5 步是機器擋得住的，因為它是一個數得出來的東西，而它的後果很具體：

> **只給一個，產出會全部長成那一個樣子。**

第 6 步機器擋不住，但可以**不讓人忘記**——一份沒有被人核對過的標準，
跟一個永遠回綠燈的測試是同一種東西。

## 它住在哪

`.aicc/project.json`，跟 `verify_command` 同一個檔——因為它回答的是**同一個
問題**（「這個專案怎麼驗」），而【依功能分類】那條要的正是這個。
**不開新檔**（`DECISIONS` 170）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.i18n import t

ACCEPTANCE_KEY = "acceptance"
DIMENSIONS_KEY = "dimensions"
APPROVAL_KEY = "approval"

# 每個維度至少要幾個案例。**手冊寫死的數字，不是我挑的**：
#
# > 每個維度要給多個案例。**只給一個，產出會全部長成那一個樣子。**
MINIMUM_EXAMPLES = 2

# 一個維度至少要有一個**反例**。手冊：
#
# > 反例要點名：「不准用破折號連接兩個短句」比「避免 AI 味」有用一百倍
MINIMUM_COUNTER_EXAMPLES = 1

# 太短的案例一定是抽象描述。「不好」「太長」「怪怪的」都在這個長度以下，
# 而它們正是手冊點名沒有用的那一種。
#
# 這不是在量文筆，是在擋**一句話講不完的東西被寫成一句話**。
MINIMUM_EXAMPLE_LENGTH = 8

# 用這份標準跑過幾次之前，都要提醒使用者自己看一眼。手冊第 6 步：
#
# > 餵給審查方跑跑看，**前幾輪要人工檢查**它的判斷跟你眼睛看到的一不一致
STILL_LEARNING_RUNS = 3


class RubricProblem(ValueError):
    """這份標準有問題，而且要講給使用者聽——**不是靜靜地忽略它**。"""


@dataclass(frozen=True)
class Example:
    """一個具體案例。

    `good=False` 是**反例**，而反例才是有用的那一半：
    「不准用破折號連接兩個短句」比「避免 AI 味」有用一百倍。
    """

    good: bool
    text: str


@dataclass(frozen=True)
class Dimension:
    """一個維度，例如「開頭有沒有勾子」。"""

    name: str
    examples: tuple[Example, ...]

    @property
    def counter_examples(self) -> tuple[Example, ...]:
        return tuple(e for e in self.examples if not e.good)


@dataclass(frozen=True)
class Approval:
    """使用者看過這一份、而且說可以。

    `digest` 是**當時那份內容的指紋**，不是一個布林。這一格才是這整個
    機制唯一有牙齒的地方：核准之後有人改了一個維度，指紋就對不上，
    那份標準自動退回「還沒有人過目」。

    否則「他核准過」會變成一張永久通行證——核准的時候有兩個維度，
    半年後變成十個，而畫面上照樣寫著「已核准」。
    """

    at: str
    digest: str
    # 他當時說的話，**原話**。不是「已核准」四個字——那句話會告訴後來的人
    # 他核准的到底是什麼，以及他有沒有附條件。
    note: str = ""


@dataclass(frozen=True)
class Rubric:
    """這個專案怎麼算「做得夠好」。"""

    dimensions: tuple[Dimension, ...]
    # 已經用它跑過幾次。手冊第 6 步要的那個計數。
    runs_so_far: int = 0
    # 使用者過目的紀錄。**`None` 是預設，而且預設就是不算數。**
    approval: Approval | None = None

    @property
    def digest(self) -> str:
        """這一份內容的指紋。**只含維度，不含跑過幾次。**

        含次數的話，跑一次就會讓核准失效——而跑一次沒有改動任何一條標準。
        """
        payload = json.dumps(
            [
                {
                    "name": d.name,
                    "examples": [{"good": e.good, "text": e.text} for e in d.examples],
                }
                for d in self.dimensions
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def still_learning(self) -> bool:
        """還在前幾輪——**使用者要自己核對它判得對不對**。

        一份沒有被人核對過的標準，跟一個永遠回綠燈的測試是同一種東西。

        跟 `approved` 是**兩件事**：`approved` 問「他看過這幾條標準沒有」，
        這一個問「他核對過它**判出來的結果**沒有」。看過標準不等於看過它
        判得準不準——手冊那六步裡，那是第 3 步跟第 6 步。
        """
        return self.runs_so_far < STILL_LEARNING_RUNS

    @property
    def approved(self) -> bool:
        """使用者過目過**這一版**沒有。

        三種 `False` 的理由，而它們**要分得出來**（見 `approval_state()`）：
        沒有人看過／看過但之後被改了／根本沒有標準。
        一句籠統的「未核准」會讓「我改了一個字」跟「我從來沒看過」長得一樣。
        """
        return self.approval is not None and self.approval.digest == self.digest


# 那份標準現在的身分。**三種，沒有預設值**——
# 【有預設值的分類等於沒有分類】。
NEVER_LOOKED_AT = "never_looked_at"
CHANGED_SINCE = "changed_since"
APPROVED = "approved"


def approval_state(rubric: Rubric) -> str:
    """這份標準算不算數，以及為什麼不算。"""
    if rubric.approval is None:
        return NEVER_LOOKED_AT
    if rubric.approval.digest != rubric.digest:
        return CHANGED_SINCE
    return APPROVED


def why_it_does_not_count(rubric: Rubric) -> str:
    """不算數的話，講給使用者聽的那一句。算數就回空字串。"""
    state = approval_state(rubric)
    if state == APPROVED:
        return ""
    if state == CHANGED_SINCE:
        return t("rubric.changed_since_approval", at=rubric.approval.at)
    return t("rubric.never_looked_at")


def _problems_with(dimension: Dimension) -> list[str]:
    problems: list[str] = []
    if not dimension.name.strip():
        problems.append(t("rubric.problem.no_name"))
        return problems

    if len(dimension.examples) < MINIMUM_EXAMPLES:
        problems.append(
            t(
                "rubric.problem.too_few_examples",
                name=dimension.name,
                count=len(dimension.examples),
                minimum=MINIMUM_EXAMPLES,
            )
        )
    if len(dimension.counter_examples) < MINIMUM_COUNTER_EXAMPLES:
        problems.append(
            t("rubric.problem.no_counter_example", name=dimension.name)
        )
    for example in dimension.examples:
        if len(example.text.strip()) < MINIMUM_EXAMPLE_LENGTH:
            problems.append(
                t(
                    "rubric.problem.too_abstract",
                    name=dimension.name,
                    text=example.text,
                )
            )
    return problems


def problems_with(rubric: Rubric) -> list[str]:
    """這份標準哪裡不行。**回清單不是回布林**——他要知道是哪一個維度。"""
    if not rubric.dimensions:
        return [t("rubric.problem.no_dimensions")]
    problems: list[str] = []
    for dimension in rubric.dimensions:
        problems.extend(_problems_with(dimension))
    return problems


def parse(raw: object) -> Rubric:
    """從 `.aicc/project.json` 讀出來的東西變成一份標準。

    **形狀不對就丟例外，不回一份空的。**回空的話，一個打錯字的設定檔會
    變成「這個專案沒有驗收標準」，而畫面上跟「他還沒設定」一模一樣。
    """
    if not isinstance(raw, dict):
        raise RubricProblem(
            t(
                "rubric.problem.not_an_object",
                key=ACCEPTANCE_KEY,
                kind=type(raw).__name__,
            )
        )
    declared = raw.get(DIMENSIONS_KEY)
    if not isinstance(declared, list):
        raise RubricProblem(
            t("rubric.problem.not_a_list", key=f"{ACCEPTANCE_KEY}.{DIMENSIONS_KEY}")
        )

    dimensions: list[Dimension] = []
    for index, item in enumerate(declared):
        if not isinstance(item, dict):
            raise RubricProblem(
                t("rubric.problem.dimension_not_an_object", index=index + 1)
            )
        name = str(item.get("name", "")).strip()
        raw_examples = item.get("examples")
        if not isinstance(raw_examples, list):
            raise RubricProblem(
                t("rubric.problem.examples_not_a_list", name=name or index + 1)
            )
        examples: list[Example] = []
        for entry in raw_examples:
            if not isinstance(entry, dict):
                raise RubricProblem(
                t("rubric.problem.example_not_an_object", name=name)
            )
            # `good` 沒寫的話**當成反例**，而不是正例：手冊要的是點名不准做
            # 什麼，而漏寫的那一個比較可能是那種。
            examples.append(
                Example(good=bool(entry.get("good", False)), text=str(entry.get("text", "")))
            )
        dimensions.append(Dimension(name=name, examples=tuple(examples)))

    raw_approval = raw.get(APPROVAL_KEY)
    approval = None
    if isinstance(raw_approval, dict):
        # **形狀不對就當成沒有核准**，不丟例外。這裡跟其他欄位相反，
        # 理由是方向：壞掉的核准紀錄要退到「還沒有人過目」（比較嚴的那邊），
        # 而丟例外會讓整份標準讀不出來，畫面上變成「這個專案沒有標準」
        # ——那是**比較鬆**的結果，因為它會被讀成「還沒設定」而不是「不算數」。
        digest = str(raw_approval.get("digest", "")).strip()
        if digest:
            approval = Approval(
                at=str(raw_approval.get("at", "")),
                digest=digest,
                note=str(raw_approval.get("note", "")),
            )

    return Rubric(
        dimensions=tuple(dimensions),
        runs_so_far=int(raw.get("runs_so_far", 0) or 0),
        approval=approval,
    )


def read(project_path: Path) -> Rubric | None:
    """這個專案有沒有質化驗收標準。**沒有就是 `None`，不是一份空的。**"""
    settings = Path(project_path) / ".aicc" / "project.json"
    if not settings.is_file():
        return None
    try:
        raw = json.loads(settings.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        # 讀不動的設定檔不是「沒有標準」——那一題由 `project_verification`
        # 那一邊回答，它會給一句看得懂的話。這裡只說「我沒有拿到」。
        return None
    if not isinstance(raw, dict) or ACCEPTANCE_KEY not in raw:
        return None
    return parse(raw[ACCEPTANCE_KEY])


def to_dict(rubric: Rubric) -> dict:
    """存回 `.aicc/project.json` 的形狀。"""
    return {
        DIMENSIONS_KEY: [
            {
                "name": dimension.name,
                "examples": [
                    {"good": example.good, "text": example.text}
                    for example in dimension.examples
                ],
            }
            for dimension in rubric.dimensions
        ],
        "runs_so_far": rubric.runs_so_far,
        **(
            {
                APPROVAL_KEY: {
                    "at": rubric.approval.at,
                    "digest": rubric.approval.digest,
                    "note": rubric.approval.note,
                }
            }
            if rubric.approval is not None
            else {}
        ),
    }


def save(project_path: Path, rubric: Rubric) -> None:
    """把標準寫回專案設定。**寫壞的擋在門口，不進檔案。**

    一份寫壞的標準比沒有更糟——它看起來像已經設定好了。所以驗過才寫，
    而不是「先存起來，跑的時候再說」。

    `.aicc/project.json` 是共用的：敏感度、驗證指令都在同一個檔。所以整段
    讀改寫要**握鎖**，不然兩個視窗同時存會有一邊的設定憑空消失。
    """
    problems = problems_with(rubric)
    if problems:
        raise RubricProblem("；".join(problems))

    from app.services.project_registry import ProjectRegistry

    project = Path(project_path).expanduser().resolve()
    with ProjectRegistry.metadata_write_lock():
        target = ProjectRegistry._project_metadata_path(project)
        data = ProjectRegistry._read_project_metadata(target)
        existing = data.get(ACCEPTANCE_KEY)
        written = to_dict(rubric)
        # **核准那一格由檔案上原本那一份說了算，不是由呼叫端送進來的
        # 那一份說了算。**呼叫端可以組一個 `Rubric(approval=...)` 出來，
        # 而如果這裡照收，`save()` 就變成第二個寫得出核准的入口——
        # 那正好把 `approve()` 那道門繞過去。
        #
        # 保留舊的那一格是對的，不是漏的：內容改了的話指紋自然對不上，
        # `approved` 會自己變成 `False`，而使用者看到的是
        # 「這份標準過目之後被改過」——**比「核准不見了」有用得多**。
        if isinstance(existing, dict) and isinstance(existing.get(APPROVAL_KEY), dict):
            written[APPROVAL_KEY] = existing[APPROVAL_KEY]
        else:
            written.pop(APPROVAL_KEY, None)
        data[ACCEPTANCE_KEY] = written
        ProjectRegistry._write_project_metadata(target, data)


def approve(project_path: Path, *, note: str, now: str) -> Rubric:
    """使用者說「這份標準可以」。回存好之後的那一份。

    ## 這支是**唯一**寫得出核准的地方，而那是刻意的

    `save()` 寫不出核准——它連 `approval` 那一格都不會動（見它自己的說明）。
    所以「起草一份標準」跟「核准一份標準」是兩個分開的動作，
    **AI 起草不會順手產生一份已核准的標準**。

    ## 它擋得住什麼、擋不住什麼

    **擋得住**：一份沒有人看過的標準被拿去當驗收依據（`approved` 是
    `False`，那條路直接停）。也擋得住核准之後偷偷改內容（指紋對不上）。

    **擋不住**：一個決定要騙人的呼叫端直接呼叫這一支。機器分不出
    「使用者按了那顆按鈕」跟「程式自己呼叫了這個函式」——
    **那一題只有畫面那一側關得起來**（那顆按鈕是唯一的呼叫端）。

    寫下來是因為【每一條檢查都講得出改壞的樣子】：這一條防的是
    「**沒有人看過，而且沒有人看得出來沒有人看過**」，不是防偽造。

    `now` 是傳進來的，不是這裡取的——時間從外面給，測試才量得到。
    """
    from app.services.project_registry import ProjectRegistry

    project = Path(project_path).expanduser().resolve()
    with ProjectRegistry.metadata_write_lock():
        target = ProjectRegistry._project_metadata_path(project)
        data = ProjectRegistry._read_project_metadata(target)
        acceptance = data.get(ACCEPTANCE_KEY)
        if not isinstance(acceptance, dict):
            raise RubricProblem(t("rubric.problem.nothing_to_approve"))
        rubric = parse(acceptance)
        problems = problems_with(rubric)
        if problems:
            # **壞掉的標準不准被核准。**核准一份不合格的標準，等於用他的名字
            # 替一個機器已經說不行的東西背書。
            raise RubricProblem("；".join(problems))
        acceptance[APPROVAL_KEY] = {
            "at": now,
            "digest": rubric.digest,
            "note": note,
        }
        data[ACCEPTANCE_KEY] = acceptance
        ProjectRegistry._write_project_metadata(target, data)
    stored = read(project)
    assert stored is not None
    return stored


def record_run(project_path: Path) -> None:
    """這份標準又被用過一次。

    **沒有這一段的話，`still_learning` 永遠是 `True`**，於是「請你自己核對
    我判得對不對」那句話每一次都出現——而一個永遠不會消失的提醒，
    使用者第三次看到就開始跳過它。

    （這個洞是 2026-08-24 我自己寫完 `still_learning` 之後忘了接的：
    那個欄位定義了、讀了、印出來了，**沒有任何地方會加它**。）
    """
    from app.services.project_registry import ProjectRegistry

    project = Path(project_path).expanduser().resolve()
    with ProjectRegistry.metadata_write_lock():
        target = ProjectRegistry._project_metadata_path(project)
        data = ProjectRegistry._read_project_metadata(target)
        acceptance = data.get(ACCEPTANCE_KEY)
        if not isinstance(acceptance, dict):
            return          # 沒有標準就沒有次數可以加
        acceptance["runs_so_far"] = int(acceptance.get("runs_so_far", 0) or 0) + 1
        data[ACCEPTANCE_KEY] = acceptance
        ProjectRegistry._write_project_metadata(target, data)


# 給第一次設定的人的起點。**不是空白**：手冊第六節的六步裡，前四步都是
# 人做的，而一張空表格對一個沒做過這件事的人沒有任何提示。
#
# 兩個維度、每個維度一正一反，而且**是真的可以照抄改的句子**，不是
# 「維度一」「案例一」那種佔位符——佔位符會被原封不動留在那裡。


def starter() -> Rubric:
    """給第一次設定的人的起點。

    **是函式不是常數**：常數會在 import 的當下把那幾句話取出來凍住，
    而 `tests/test_no_new_hardcoded_chinese.py` 有一條專門擋這件事
    ——2026-08-24 我在同一天用同一個形狀犯過三次。

    **不是空白**：手冊第六節那六步裡，前四步都是人做的，而一張空表格對一個
    沒做過這件事的人沒有任何提示。

    **也不是佔位符**：「維度一／案例一」那種東西會被原封不動留在那裡，
    而那時候這份標準看起來設定好了、實際上什麼都沒說。所以這裡放的是
    **真的可以照抄改的句子**。
    """
    return Rubric(
        dimensions=(
            Dimension(
                name=t("rubric.starter.hook.name"),
                examples=(
                    Example(False, t("rubric.starter.hook.bad")),
                    Example(True, t("rubric.starter.hook.good")),
                ),
            ),
            Dimension(
                name=t("rubric.starter.complete.name"),
                examples=(
                    Example(False, t("rubric.starter.complete.bad")),
                    Example(True, t("rubric.starter.complete.good")),
                ),
            ),
        )
    )
