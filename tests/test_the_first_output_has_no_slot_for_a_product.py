"""第一步交的是做法，而且那份提示詞不准假設產出是程式。

兩條定案，一條講形狀、一條講對象：

> 【執行角色第一次交做法，不交成品】不管產出是程式、文件、圖片還是別的，
> 第一次只能交：我理解的需求／我打算怎麼做／我不確定的地方。
> **否決：寫成規則叫它先別動手（那是叮嚀，會漏）。**
> 不變式：**第一步的輸出根本沒有「成品」那一格。**

> 【不是所有使用者都寫程式】可能是文件、可能是圖片、可能是有的沒的。

被否決的那個做法正是舊版在用的：提示詞叫它做，然後在輸出規則裡補一句
「不要輸出程式碼補丁」。那是叮嚀——一句可以被忽略、被繞過、而且**被忽略的
時候沒有任何東西會變紅**的話。

現在的形狀是：那份提示詞裡**根本沒有一格叫成品**。它要的是四格
（我理解的需求／我打算怎麼做／我不確定的地方／我判斷不該做的），沒有第五格。
"""
from __future__ import annotations

from app.core.prompt_builder import build_work_plan_prompt

# 一個跟程式完全無關的案子。這一份提示詞要接得住它。
FILM = "把三小時的婚禮毛片剪成八分鐘的精華，配樂要自己挑。"


def plan_prompt(task: str = FILM) -> str:
    return build_work_plan_prompt(
        task=task,
        knowledge_context="",
        output_instruction="輸出精簡。",
    )


def test_the_output_has_four_slots_and_none_of_them_is_a_product() -> None:
    """四格都要在，而且沒有第五格。

    「不要交成品」如果只是一句規則，它就是叮嚀——會漏，而且漏的時候沒有東西
    會變紅。所以這裡量的是**輸出的形狀**：它要的四樣東西裡，沒有一樣是成品。
    """
    text = plan_prompt()

    for slot in (
        "我理解的需求",
        "我打算怎麼做",
        "我不確定的地方",
        "我判斷不該做的",
    ):
        assert slot in text, f"少了「{slot}」那一格"

    assert "你的產出是一份做法，不是成品" in text


def test_the_uncertain_slot_may_not_be_left_blank_for_free() -> None:
    """「我不確定的地方」空著要有理由。

    不講這句話的話，那一格永遠是空的——而一件事情從頭到尾沒有任何不確定，
    通常代表還沒想到那麼細。空白會被讀成「都想清楚了」。
    """
    assert "這一格空著要有理由" in plan_prompt()


def test_nothing_in_it_assumes_the_product_is_code() -> None:
    """剪片、寫文件、做圖的案子照這份提示詞跑，不該被要求交出它沒有的東西。

    這些詞每一個都是舊版真的寫過的。留著任何一個，非程式的案子第一格就會
    歪掉——而歪掉的樣子是它照著一個不存在的前提認真回答。
    """
    text = plan_prompt()

    for word in ("程式碼", "補丁", "開發工程師", "實作以下任務", "檔案或區域"):
        assert word not in text, (
            f"提示詞裡還留著「{word}」——這一格可能在規劃一支影片的剪法、"
            "一份文件的章節、一張圖的構圖"
        )


def test_the_persona_does_not_hand_it_a_job_title_it_may_not_have() -> None:
    """開場白也一樣。

    「你是此專案的**施工**規劃者」對剪片的案子沒有意義，而開場白是整份提示詞
    裡最有份量的一句——模型會照著它扮演。
    """
    text = plan_prompt()

    assert "你是這件事的規劃者。" in text
    assert "施工" not in text


def test_the_read_only_rail_is_still_welded_on() -> None:
    """去程式化不准把安全那半一起拿掉。

    `StepPersona` 把開場白拆成 identity 與 rails 兩半，就是為了這件事：
    改「你是誰」的時候，**改不到「你不准做什麼」**。這一條守著那個切線。
    """
    text = plan_prompt()

    assert "不得修改任何檔案" in text
    assert "不得執行 Git 或建立 commit" in text
