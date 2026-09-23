"""模式清單少一份的時候，那一份**還在檔案裡**。

使用者會看到的原始症狀（2026-08-24 掃描實跑出來的）：

    file before      : ['五節點', 'keeper']
    saved() sees     : ['keeper']
    save() returned  : 全新的一份（沒有例外 → 畫面回報「已儲存」）
    file after       : ['keeper', '全新的一份']      ← 五節點從硬碟上消失了
    delete('五節點') : False                          ← 「找不到那個模式」

模式清單上少一份，沒有任何錯誤訊息；他按儲存，畫面說「已儲存」；而那一份
原本在檔案裡的模式，存完之後檔案裡沒有了。

會走到這裡的不是壞掉的檔案，是**同一個版本、某一筆這一版讀不出來**：
AICC 多支援一個廠牌而 `FORMAT_VERSION` 沒跟著跳、使用者自己手改過一筆、
或者另一台機器上的 build 寫的（`lineup-presets.json` 是 `%APPDATA%` 全機器
共用的）。`_refuse_if_newer()` 只擋得住「整份檔案是新版寫的」那一種。

## 這幾條擋不住什麼

* **擋不住「清單上看不到它」。**修完之後那一份還在硬碟上，但陣容頁上仍然
  少一份、而且沒有一句話說為什麼。要補那句話得動頁面原始檔與 `app/i18n.py`，
  兩者都不在這一組手上（見 `docs/WHY.md` 同名那一則）。
* **擋不住「原始那一筆本身是壞的」。**這裡只保證它不會被這一版動到，
  不保證它有意義。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.core.lineup import AgentSlot, Lineup
from app.services.lineup_presets import (
    LineupPresetStore,
    PresetEntryUnreadable,
)


def _seed(config_dir: Path) -> Path:
    """一份 %APPDATA% 裡的模式檔：一筆讀得出來，一筆這一版讀不出來。

    第二筆讀不出來的理由是 `provider: "gemini"` ——那正是 `VALID_PROVIDERS`
    多加一家而版本號沒跳的樣子，也是 `tests/test_lineup.py` 自己用的例子。
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "lineup-presets.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "presets": [
                    {
                        "name": "五節點",
                        "description": "老闆要親手走一遍的那一條線",
                        "slots": [
                            {"role": "發想", "provider": "codex", "duty": "plan"},
                            {"role": "粗剪", "provider": "gemini", "duty": "work"},
                            {"role": "細剪", "provider": "claude", "duty": "work"},
                        ],
                    },
                    {
                        "name": "keeper",
                        "description": "讀得出來的那一份",
                        "slots": [
                            {"role": "執行", "provider": "claude", "duty": "work"}
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _names(path: Path) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [str(item.get("name", "")) for item in raw["presets"]]


def test_saving_a_mode_does_not_delete_the_one_this_build_cannot_read(
    tmp_path: Path,
) -> None:
    """他存一份新的模式，另一份**沒有**跟著從硬碟上消失。

    弄壞的樣子：`save()` 拿 `saved()`（只有讀得出來的）整份覆寫檔案，
    於是那一筆在一次回報成功的存檔裡被刪掉，一個字都不會出現。
    """
    path = _seed(tmp_path / "appdata")
    store = LineupPresetStore(config_dir=tmp_path / "appdata")
    assert _names(path) == ["五節點", "keeper"], "前提壞了：測試資料沒寫進去"
    assert [p.name for p in store.saved()] == ["keeper"], (
        "前提壞了：那一筆應該是讀不出來的，不然這條在驗空氣"
    )

    store.save("全新的一份", Lineup.of([AgentSlot("執行", "claude", duty="work")]))

    after = _names(path)
    assert "五節點" in after, f"存一份新的把另一份從硬碟上刪掉了：{after}"
    assert "keeper" in after and "全新的一份" in after, after


def test_the_mode_it_cannot_read_is_written_back_word_for_word(
    tmp_path: Path,
) -> None:
    """那一份被留下來的模式，**內容一個字都沒有被改過**。

    留一份「照這一版的形狀重寫」的複本等於沒留：讀不出來的原因很可能正是
    它有一個這一版沒有欄位可以裝的東西，重寫一次那個東西就沒了——而使用者
    看到的一樣是「已儲存」。
    """
    path = _seed(tmp_path / "appdata")
    before = next(
        item
        for item in json.loads(path.read_text(encoding="utf-8"))["presets"]
        if item["name"] == "五節點"
    )
    store = LineupPresetStore(config_dir=tmp_path / "appdata")

    store.save("全新的一份", Lineup.of([AgentSlot("執行", "claude", duty="work")]))

    after = next(
        item
        for item in json.loads(path.read_text(encoding="utf-8"))["presets"]
        if item["name"] == "五節點"
    )
    assert after == before, f"被重寫過了：\n  before={before}\n  after ={after}"


def test_deleting_does_not_claim_the_mode_was_never_there(tmp_path: Path) -> None:
    """要刪那一份看不見的模式時，得到的不是「找不到那個模式」。

    「找不到」在有讀不出來的一筆時是一句假話——它很可能就在檔案裡，只是這
    一版讀不出它的形狀。`delete()` 自己的註解 2026-08-24 之前就寫著這件事，
    而它只擋得住「整份檔案是新版寫的」那一種。

    今天 repo 裡還沒有刪除模式的按鈕，所以這是**留給下一個接刪除鍵的人**
    的：他接上去的那一天，這條會逼他處理，而不是讓他把一句謊話接到畫面上。
    """
    _seed(tmp_path / "appdata")
    store = LineupPresetStore(config_dir=tmp_path / "appdata")

    with pytest.raises(PresetEntryUnreadable):
        store.delete("五節點")


def test_a_clean_file_can_still_have_a_mode_deleted(tmp_path: Path) -> None:
    """全部讀得出來的時候，刪除照樣刪得掉。

    上面那條的反面。少了這一條，「永遠丟例外」也會是綠的——而那會讓刪除鍵
    在最正常的情況下壞掉。
    """
    config = tmp_path / "appdata"
    config.mkdir(parents=True)
    store = LineupPresetStore(config_dir=config)
    store.save("我的一份", Lineup.of([AgentSlot("執行", "claude", duty="work")]))

    assert store.delete("我的一份") is True
    assert store.delete("我的一份") is False, "刪過了還說刪掉了"


def test_the_page_says_why_a_mode_could_not_be_saved(tmp_path, monkeypatch) -> None:
    """模式檔是新版 AICC 寫的時候，他按「存成模式」會**收到一句話**。

    弄壞的樣子：`PresetFileTooNew` 是 `RuntimeError` 不是 `PresetError`，
    而 `_save_preset` 只接後者——那個例外會直接穿出 `handle_action`，從頁面
    橋掉出去。使用者按下去，沒有訊息、沒有變化、也沒有存進去。

    **這條擋不住**「訊息寫得好不好」，只擋得住「有沒有講」。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.ui.lineup_web_panel import LineupWebPanel

    config = tmp_path / "appdata"
    config.mkdir(parents=True)
    (config / "lineup-presets.json").write_text(
        json.dumps({"version": 99, "presets": []}), encoding="utf-8"
    )
    monkeypatch.setenv("APPDATA", str(config.parent / "appdata_root"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    panel = LineupWebPanel(project)
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    # 模式檔的位置是全機器共用的，測試要指到臨時目錄去。
    panel._presets = LineupPresetStore(config_dir=config)

    said: list[str] = []
    panel.action_failed.connect(said.append)

    panel.handle_action("save_preset", "存不進去的一份")

    assert said, "按下去什麼都沒說（例外從頁面橋掉出去了）"
    assert "99" in said[0], f"沒講是第幾版寫的：{said[0]}"
