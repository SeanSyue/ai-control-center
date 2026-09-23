"""模式檔整份讀不出來的時候，存一份新的模式**不會把它蓋掉**，而且他會被告知。

使用者會看到的原始症狀（2026-08-24 實跑出來的）：

    before : {"version": 1, "presets": [{"name": "我的三人組",     ← 檔案壞在這裡
    save() : 回報成功（畫面說「已儲存」）
    after  : {"version": 1, "presets": [{"name": "剪片線", ...}]}  ← 我的三人組沒了
    delete("我的三人組") : False                                    ← 「找不到那個模式」

`lineup-presets.json` 是 `%APPDATA%` **整台機器共用**的一份，裡面存的是他自己
排過、取過名字的工作線。它會壞掉的路：存檔存到一半斷電或當掉、防毒／同步工具
截斷、他自己手改過一個括號、或者另一個版本的 build 換了鍵名而 `version` 沒跳。

## 跟隔壁那一條的差別（為什麼三道門要三支）

已經有兩道門，而**它們兩個都擋不住這一種**：

| 門 | 問的是 | 遇到「整份讀不出來」 |
|---|---|---|
| `_refuse_if_newer()` | `version` 這個欄位比這一版新嗎 | 讀不到欄位 → `stored_version()` 回 0 → **開的** |
| `PresetEntryUnreadable` | 有幾筆讀不出來 | 一筆都數不到 → `lost` 是空的 → **開的** |
| `PresetFileUnreadable` | 這份檔案這一版讀得懂嗎 | 這一條 |

## 這幾條擋不住什麼

* **擋不住「壞掉的檔案自己修好」。**修完之後那份檔案原封不動躺在那裡，
  而使用者存不了新的模式，直到他自己處理那個檔案。這是刻意的：一份讀不出來
  的檔案，這一版沒有任何辦法把他存過的東西撈出來，**能做的只有不要動它**。
* **擋不住「訊息寫得夠不夠好」**，只擋得住「有沒有講」。
* **擋不住空檔案以外的部分壞掉**：`{"version":1,"presets":[...]}` 讀得出來
  而某一筆壞掉的那一種，是隔壁那一支
  （`test_a_mode_this_build_cannot_read_stays_on_disk.py`）在守。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.core.lineup import AgentSlot, Lineup
from app.services.lineup_presets import (
    LineupPresetStore,
    PresetFileUnreadable,
)

# 一份被截斷的模式檔——存檔存到一半斷電就長這樣。裡面看得見他存過的東西，
# 而 `json.loads` 讀不完它。
TRUNCATED = '{"version": 1, "presets": [{"name": "我的三人組", "slots": [{"role"'

# 讀得完、但這一版沒辦法從裡面撈出任何模式的那一種：鍵名換了而版本號沒跳。
WRONG_SHAPE = '{"version": 1, "modes": [{"name": "我的三人組", "slots": []}]}'


def _store(tmp_path: Path, text: str) -> LineupPresetStore:
    config = tmp_path / "appdata"
    config.mkdir(parents=True, exist_ok=True)
    (config / "lineup-presets.json").write_text(text, encoding="utf-8")
    return LineupPresetStore(config_dir=config)


def _one_seat() -> Lineup:
    return Lineup.of([AgentSlot("執行", "claude", duty="work")])


@pytest.mark.parametrize("text", [TRUNCATED, WRONG_SHAPE])
def test_saving_a_mode_does_not_wipe_a_file_this_build_cannot_parse(
    tmp_path: Path, text: str
) -> None:
    """他存一份新的模式，那份壞掉的檔案**一個位元都沒有被動過**。

    弄壞的樣子：`save()` 讀不到東西就當成「這台機器還沒存過模式」，寫一份
    全新的上去——他存過的每一份在那一刻從硬碟上消失，而畫面說的是「已儲存」。
    那是最貴的一種失敗：**沒有錯誤訊息，而且沒有第二次機會。**
    """
    store = _store(tmp_path, text)
    before = store.path.read_bytes()

    with pytest.raises(PresetFileUnreadable):
        store.save("剪片線", _one_seat())

    assert store.path.read_bytes() == before, (
        f"讀不出來的模式檔被蓋掉了：\n  before={before!r}\n  after ={store.path.read_bytes()!r}"
    )
    assert "我的三人組" in store.path.read_text(encoding="utf-8")


def test_deleting_from_an_unparsable_file_does_not_say_it_was_never_there(
    tmp_path: Path,
) -> None:
    """要刪一份看不見的模式時，得到的不是「找不到那個模式」。

    弄壞的樣子：`delete()` 回 `False`，而呼叫端只會把它翻成「沒有那個模式」。
    那句話在這種情況下是假的——它很可能就在那份檔案裡，只是這一版讀不完它。
    **用一句謊話回答一個誠實的問題**，比丟一個例外糟得多。
    """
    store = _store(tmp_path, TRUNCATED)

    with pytest.raises(PresetFileUnreadable):
        store.delete("我的三人組")


def test_the_error_says_which_file_and_where_it_broke(tmp_path: Path) -> None:
    """那句話裡有**檔案路徑**與**壞在哪裡**。

    少了這兩樣，使用者收到的是「讀不到檔案」四個字——他知道出事了，而完全
    不知道要去哪裡、動什麼。這個檔案住在 `%APPDATA%` 底下，路徑不寫出來的話
    他連它在哪都不知道。

    **這條擋不住訊息寫得好不好**，只擋得住「有沒有講到那兩件事」。
    """
    store = _store(tmp_path, TRUNCATED)

    with pytest.raises(PresetFileUnreadable) as caught:
        store.save("剪片線", _one_seat())

    said = str(caught.value)
    assert "lineup-presets.json" in said, said
    assert "line 1" in said, f"沒講壞在哪裡：{said}"


def test_reading_the_list_still_works_so_the_page_still_opens(tmp_path: Path) -> None:
    """檔案壞掉的時候，陣容頁**照樣打得開**，內建的那三張表格照樣在。

    這一條是上面每一條的邊界。把「讀不出來就丟例外」也放進讀的那條路的話，
    一份壞掉的模式檔會讓**整頁打不開**——使用者連他的陣容都看不到了，而那是
    拿一個更大的故障去換一個資料損失。

    **代價要講清楚**：清單上少了他存過的那幾份，而畫面上沒有一句話說為什麼。
    那一半要動頁面原始檔與 `app/i18n.py`，不在這一組手上。
    """
    store = _store(tmp_path, TRUNCATED)

    assert store.saved() == []
    names = [preset.name for preset in store.all()]
    assert len(names) == 3, f"內建的三張表格不見了：{names}"
    assert all(preset.built_in for preset in store.all())


def test_a_machine_with_no_modes_yet_can_still_save_its_first_one(
    tmp_path: Path,
) -> None:
    """**還沒有那個檔案**的時候，第一次存照樣存得進去。

    上面那幾條的反面，而且是必要的：「檔案不存在」與「檔案讀不出來」如果被
    折成同一件事，這一條會紅——而那表示新使用者**永遠存不了第一份模式**。
    少了這條，「一律丟例外」也會是綠的。
    """
    config = tmp_path / "appdata"
    store = LineupPresetStore(config_dir=config)
    assert not store.path.exists(), "前提壞了：這一台應該還沒有模式檔"

    store.save("剪片線", _one_seat())

    assert [p.name for p in store.saved()] == ["剪片線"]
    assert store.delete("剪片線") is True


def test_the_page_says_why_the_mode_could_not_be_saved(tmp_path, monkeypatch) -> None:
    """他按「存成模式」，畫面上**收到一句話**，而不是什麼都沒發生。

    弄壞的樣子：`PresetFileUnreadable` 是 `RuntimeError` 不是 `PresetError`，
    `_save_preset` 沒接的話它會直接穿出 `handle_action`、從頁面橋掉出去。
    使用者按下去：沒有訊息、沒有變化、也沒有存進去——而「按了沒反應」跟
    「存好了」在畫面上長得一模一樣，他接下來會照著一份不存在的模式做事。

    **擋不住訊息寫得好不好**，只擋得住有沒有講、以及有沒有講到那個檔案。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.ui.lineup_web_panel import LineupWebPanel

    store = _store(tmp_path, TRUNCATED)
    # 模式檔的位置是全機器共用的，測試要把面板自己那一份也指到臨時目錄去。
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata_root"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    panel = LineupWebPanel(project)
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    panel._presets = store

    said: list[str] = []
    panel.action_failed.connect(said.append)

    panel.handle_action("save_preset", "存不進去的一份")

    assert said, "按下去什麼都沒說（例外從頁面橋掉出去了）"
    assert "lineup-presets.json" in said[0], f"沒講是哪一個檔案：{said[0]}"
    assert "我的三人組" in store.path.read_text(encoding="utf-8"), "檔案還是被蓋掉了"
