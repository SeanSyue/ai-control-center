"""沒有單元測試的工作，也要有人說得出「做完了」是什麼意思。

`docs/AI協作手冊.md` 第六節：

> 上面所有的規矩都預設「跑個指令就知道對不對」。但**大部分辦公室工作不是
> 這樣**——圖好不好看、文章寫得好不好、簡報有沒有質感，都沒有單元測試。
>
> **這是 AICC 從「幫工程師跑測試」變成「幫辦公室的人做事」的關鍵一步。**

2026-08-24 之前，那種專案在**派工之前**就撞牆：`declared()` 問「有沒有說過
要怎麼驗」，沒有 `verify_command` 就不讓它開始——而畫面上講的是
「請去設定 verify_command」，一句對剪片的人完全沒有意義的話。

## 這份檔案守的兩件事，方向剛好相反

1. **有質化標準的案子跑得動**（不然客群做的事 AICC 一件都接不了）
2. **兩種都沒有的案子照樣不准跑**（不然每一次執行都拿到綠燈，
   而使用者要到第十次才發現沒有人在看）

第二件比第一件重要。**「沒宣告」如果可以靜靜地變成「那就算過吧」**，
那就是【失效的檢查會繼續給綠燈】那條，只是換一個入口。
"""
from __future__ import annotations

import json

import pytest

from app.core.prompt_builder import build_acceptance_block
from app.services import qualitative_acceptance as qa


def _good_rubric_dict() -> dict:
    return {
        "dimensions": [
            {
                "name": "開頭有沒有勾子",
                "examples": [
                    {"good": False, "text": "開頭那句是「在這個快速變遷的時代」"},
                    {"good": True, "text": "開頭直接給一個具體的數字或場景"},
                ],
            }
        ]
    }


def _write(project, rubric: dict | None) -> None:
    aicc = project / ".aicc"
    aicc.mkdir(parents=True, exist_ok=True)
    data = {} if rubric is None else {qa.ACCEPTANCE_KEY: rubric}
    (aicc / "project.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def test_a_dimension_with_one_example_is_rejected() -> None:
    """**手冊寫死的數字，不是我挑的。**

    > 每個維度要給多個案例。**只給一個，產出會全部長成那一個樣子。**
    """
    rubric = qa.parse(
        {
            "dimensions": [
                {"name": "語氣", "examples": [{"good": False, "text": "不要用驚嘆號結尾"}]}
            ]
        }
    )
    problems = qa.problems_with(rubric)
    assert problems, "只給一個案例卻放行了"
    assert any("只有 1 個案例" in p for p in problems), problems


def test_a_dimension_with_no_counter_example_is_rejected() -> None:
    """手冊：**反例要點名**。

    > 「不准用破折號連接兩個短句」比「避免 AI 味」有用一百倍
    """
    rubric = qa.parse(
        {
            "dimensions": [
                {
                    "name": "語氣",
                    "examples": [
                        {"good": True, "text": "句子短，一句講一件事"},
                        {"good": True, "text": "用具體的名詞不用抽象的形容"},
                    ],
                }
            ]
        }
    )
    assert any("沒有反例" in p for p in qa.problems_with(rubric))


def test_an_abstract_example_is_rejected() -> None:
    """「不好」不是案例，是感想。

    這一條擋的是**一句話講不完的東西被寫成一句話**——而那正是手冊點名
    沒有用的那一種。
    """
    rubric = qa.parse(
        {
            "dimensions": [
                {
                    "name": "語氣",
                    "examples": [
                        {"good": False, "text": "不好"},
                        {"good": True, "text": "句子短，一句講一件事"},
                    ],
                }
            ]
        }
    )
    assert any("太短" in p for p in qa.problems_with(rubric))


def test_a_good_rubric_has_no_problems() -> None:
    """前提檢查：一份寫得好的標準要過得了。

    過不了的話上面每一條都是恆真的——一個什麼都擋的檢查跟一個什麼都不擋的
    一樣沒用。
    """
    assert qa.problems_with(qa.parse(_good_rubric_dict())) == []


def test_a_broken_shape_raises_rather_than_returning_an_empty_rubric() -> None:
    """**形狀不對丟例外，不回一份空的。**

    回空的話，一個打錯字的設定檔會變成「這個專案沒有驗收標準」，而畫面上
    跟「他還沒設定」一模一樣。
    """
    with pytest.raises(qa.RubricProblem):
        qa.parse("這不是一個物件")
    with pytest.raises(qa.RubricProblem):
        qa.parse({"dimensions": "也不是陣列"})


def test_a_missing_good_flag_means_counter_example() -> None:
    """`good` 沒寫的時候當**反例**。

    手冊要的是點名不准做什麼，而漏寫的那一個比較可能是那種。反過來預設成
    正例的話，一份漏寫的標準會變成「沒有任何反例」——而那正是它沒有用的
    那個形狀。
    """
    rubric = qa.parse({"dimensions": [{"name": "x", "examples": [{"text": "一段夠長的案例文字"}]}]})
    assert rubric.dimensions[0].counter_examples


def test_the_first_few_runs_ask_the_user_to_check_it() -> None:
    """手冊第 6 步：**前幾輪要人工檢查**。

    一份沒有被人核對過的標準，跟一個永遠回綠燈的測試是同一種東西。
    """
    fresh = qa.parse(_good_rubric_dict())
    assert fresh.still_learning
    assert "核對" in build_acceptance_block(fresh)

    seasoned = qa.parse({**_good_rubric_dict(), "runs_so_far": qa.STILL_LEARNING_RUNS})
    assert not seasoned.still_learning
    assert "核對" not in build_acceptance_block(seasoned)


def test_the_examples_reach_the_prompt_verbatim() -> None:
    """**不准摘要。**

    手冊第 4 步整段都在講這件事：抽象描述沒有用，「不准用破折號連接兩個
    短句」才有用——而摘要做的正好是把具體案例壓回抽象描述。
    """
    prompt = build_acceptance_block(qa.parse(_good_rubric_dict()))
    for dimension in qa.parse(_good_rubric_dict()).dimensions:
        assert dimension.name in prompt
        for example in dimension.examples:
            assert example.text in prompt, f"「{example.text}」沒有逐字進到提示詞裡"


def test_the_prompt_says_which_ones_are_forbidden() -> None:
    """正例反例要分得出來。分不出來的話，反例會被當成範本照抄。"""
    prompt = build_acceptance_block(qa.parse(_good_rubric_dict()))
    assert "不准這樣" in prompt
    assert "這樣是對的" in prompt


# --- 接上引擎那道門 --------------------------------------------------------

def test_a_project_with_a_rubric_gets_past_the_gate(tmp_path) -> None:
    """有質化標準的案子**跑得動**。

    改壞的樣子就是它 2026-08-24 之前的樣子：剪片的案子在派工之前撞牆，
    而畫面上叫他去設定一個他不需要的東西。
    """
    project = tmp_path / "剪片"
    _write(project, _good_rubric_dict())
    rubric = qa.read(project)
    assert rubric is not None
    assert qa.problems_with(rubric) == []


def test_a_project_with_neither_still_stops(tmp_path) -> None:
    """**這一條比上面那條重要。**

    兩種都沒有的專案不准跑。「沒宣告」如果可以靜靜地變成「那就算過吧」，
    每一次執行都會拿到綠燈，而使用者要到第十次才發現沒有人在看。
    """
    project = tmp_path / "空的"
    _write(project, None)
    assert qa.read(project) is None


def test_a_broken_rubric_is_worse_than_none_so_it_also_stops(tmp_path) -> None:
    """**有一份壞掉的標準比沒有更糟**：它看起來像已經設定好了。"""
    project = tmp_path / "壞的"
    _write(project, {"dimensions": [{"name": "語氣", "examples": [{"good": False, "text": "不好"}]}]})
    rubric = qa.read(project)
    assert rubric is not None
    assert qa.problems_with(rubric), "一份擋不住的標準卻放行了"


def test_the_rubric_reaches_the_reviewing_seat(tmp_path) -> None:
    """**標準要真的送到會用它的那一格。**

    只擋門不送出去的話，這整件事只是一道新的牆——審查那一格照樣不知道
    什麼叫做得夠好。
    """
    from app.core import prompt_builder

    text = build_acceptance_block(qa.parse(_good_rubric_dict()))
    prompt = prompt_builder.build_review_prompt(
        review_package={"task": "把這段影片剪到三分鐘"},
        acceptance_rubric=text,
    )
    assert "開頭有沒有勾子" in prompt
    assert "在這個快速變遷的時代" in prompt, "案例沒有逐字進到審查提示詞裡"


def test_no_rubric_adds_nothing_to_the_prompt() -> None:
    """沒有標準的時候**一個字元都不加**。

    加一句「請依一般標準判斷」那種場面話，會讓它以為自己有標準可以依。
    而多加一個空行也不行：金標快照不該因為一個沒用到的功能而改，
    不然下一次真的改了 prompt 的人會以為那個 diff 是雜訊。
    """
    from app.core import prompt_builder

    package = {"task": "隨便"}
    without = prompt_builder.build_review_prompt(review_package=package)
    empty = prompt_builder.build_review_prompt(review_package=package, acceptance_rubric="")
    assert without == empty

    # **上面那兩個永遠相同**（同一條分支），所以它抓不到「那條分支塞了東西」。
    # 真正咬得住的是金標快照——它 2026-08-24 就抓過一個多出來的空行。
    # 這裡再加一條獨立的：沒有標準的時候不准出現「標準」這個詞。
    assert "驗收標準" not in without
    assert "一般標準" not in without


# --- 存得進去、而且會記得用過幾次 ------------------------------------------

def test_a_rubric_can_actually_be_saved(tmp_path) -> None:
    """**這在 2026-08-24 之前不存在。**

    引擎讀得懂、擋得住、送得出去，但**沒有任何地方寫得進去**——使用者只能
    自己開 `.aicc/project.json` 手寫。一個只讀得到的設定等於沒有設定。
    """
    from app.services.project_registry import ProjectRegistry

    project = tmp_path / "案子"
    project.mkdir()
    ProjectRegistry.initialize_project(project)

    qa.save(project, qa.parse(_good_rubric_dict()))
    back = qa.read(project)
    assert back is not None
    assert [d.name for d in back.dimensions] == ["開頭有沒有勾子"]


def test_saving_does_not_wipe_the_other_settings(tmp_path) -> None:
    """`.aicc/project.json` 是共用的：敏感度、驗證指令都在同一個檔。

    整段讀改寫要握鎖，不然兩個視窗同時存會有一邊的設定**憑空消失**——
    而消失的那一邊不會報錯。
    """
    from app.services.project_registry import ProjectRegistry

    project = tmp_path / "案子"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    ProjectRegistry.set_sensitivity(project, "sensitive_kyc")

    qa.save(project, qa.parse(_good_rubric_dict()))

    assert ProjectRegistry.get_sensitivity(project) == "sensitive_kyc"


def test_a_broken_rubric_is_refused_at_the_door(tmp_path) -> None:
    """**寫壞的不進檔案。**

    一份寫壞的標準比沒有更糟——它看起來像已經設定好了。所以驗過才寫，
    不是「先存起來，跑的時候再說」。
    """
    from app.services.project_registry import ProjectRegistry

    project = tmp_path / "案子"
    project.mkdir()
    ProjectRegistry.initialize_project(project)

    bad = qa.parse({"dimensions": [{"name": "語氣", "examples": [{"good": False, "text": "不好"}]}]})
    with pytest.raises(qa.RubricProblem):
        qa.save(project, bad)
    assert qa.read(project) is None, "壞掉的標準被寫進去了"


def test_the_reminder_stops_after_a_few_runs(tmp_path) -> None:
    """**這個洞是我自己挖的。**

    `still_learning` 定義了、讀了、印出來了，而 2026-08-24 之前
    **沒有任何地方會加那個次數**——於是它永遠是 `True`，那句「請你自己核對」
    每一次都出現。一個永遠不會消失的提醒，使用者第三次看到就開始跳過它。
    """
    from app.services.project_registry import ProjectRegistry

    project = tmp_path / "案子"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    qa.save(project, qa.parse(_good_rubric_dict()))

    assert qa.read(project).still_learning
    for _ in range(qa.STILL_LEARNING_RUNS):
        qa.record_run(project)
    assert not qa.read(project).still_learning


def test_the_starter_is_something_you_can_actually_edit() -> None:
    """給第一次設定的人的起點**不是空白，也不是佔位符**。

    「維度一／案例一」那種東西會被原封不動留在那裡——而那時候這份標準
    看起來設定好了，實際上什麼都沒說。
    """
    assert qa.problems_with(qa.starter()) == []
    for dimension in qa.starter().dimensions:
        assert "維度" not in dimension.name
        for example in dimension.examples:
            assert "案例" not in example.text
            assert len(example.text) >= 12, f"「{example.text}」太短，照抄改不動"
