"""負向驗證的配方不准腐爛。

這個專案唯一真正的品質規則是「綠燈在被弄紅過之前不算證據」，而執行那條規則的
是 `tools/negative_control.py` 加上 `tools/mutations/*.json` 這幾份配方：每一條
說「把這一行改成那樣，然後跑這條測試」，判決應該是紅 ✓。

配方會腐爛。程式碼被重構之後（換了縮排、改了一句訊息、搬了一行），配方裡的
`replace` 就比不到任何東西了。工具那時給的判決是「壞 ？ 測試根本沒跑起來」——
它沒有說謊，但那一行混在一整頁紅 ✓ 中間長得像雜訊，而它真正的意思是：**這條
負向驗證從那天起什麼都沒有在守**。

2026-08-21 實測到的狀態：五份配方裡有四條已經腐爛（mutations_cli_runner 的 M4、
mutations_project_verification 的 M9 / M10 / M11），沒有人發現，而且是審稿人逐條
手動比對才抓到的。那次的機制本身還好——每一條都有別的紅燈頂著——但那是運氣，
不是設計。

所以這裡把「配方要跟得上程式碼」從一句叮嚀變成一條會變紅的規則。老闆的判準是：
一條規則失效的時候，有沒有東西會變紅？會的叫機制，不會的叫叮嚀。

檢查邏輯**不寫在這裡**，寫在 `tools/negative_control.py::check_recipes`，這條測試
只是它的第二個入口（第一個是 `--check`）。這個專案這一輪才剛因為「同一個問題有
兩份實作」吃過虧——兩個解析器對「這個工具找不找得到」給不同答案，而不一致會被洗
成紅燈，然後派人去修一個不存在的 bug。同樣的形狀不要在這裡重演一次。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_negative_control():
    """把 tools/negative_control.py 當模組載進來。

    tools/ 不是套件，也不在 sys.path 上——用檔案路徑載，而不是把 tools/ 塞進
    sys.path，免得這條測試改變了整套測試看得到的匯入名稱空間。
    """
    path = ROOT / "tools" / "negative_control.py"
    assert path.is_file(), f"負向驗證工具不見了：{path}"
    spec = importlib.util.spec_from_file_location("_aicc_negative_control", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_recipe_can_still_find_what_it_breaks() -> None:
    """每一條配方都還比得到它要破壞的字串，測試也還在。

    這條紅的時候要做的事是**照現行程式碼更新那條配方**，不是刪掉它。訊息裡
    直接寫著是哪一份、哪一條、哪個檔案。
    """
    negative_control = _load_negative_control()
    problems = negative_control.check_recipes()
    assert problems == [], (
        "負向驗證的配方腐爛了 —— 這幾條現在什麼都沒有在守：\n  "
        + "\n  ".join(problems)
        + "\n照現行程式碼更新它們（`.venv\\Scripts\\python.exe "
        "tools\\negative_control.py --check` 可以單獨跑這個檢查），不要刪掉它們。"
    )


def test_the_recipes_are_all_in_one_place_and_there_are_some() -> None:
    """配方全部住在 tools/mutations/，而且不是空的。

    分開寫是因為它守的是不同的東西：上面那條問「內容還準不準」，這條問
    「東西還在不在該在的地方」。散在 repo 根目錄的配方沒有人擁有，而沒有人
    擁有的檔案正是上一次腐爛的來源——五份未追蹤的 JSON 躺在根目錄，三棒都
    看到了、都提議搬走，但沒有人動手。

    「不是空的」那一半同樣承重：把 tools/mutations/ 清空會讓上面那條測試變成
    永遠通過，因為它檢查不到任何東西。一個什麼都沒檢查的檢查是綠的。
    """
    negative_control = _load_negative_control()

    recipes = negative_control.recipe_files()
    assert recipes, (
        "tools/mutations/ 底下一份配方都沒有——那會讓 "
        "test_every_recipe_can_still_find_what_it_breaks 變成一條永遠通過、"
        "什麼都沒檢查的測試。"
    )

    # 用工具自己的 stray_recipes()，不要在這裡再寫一次同樣的 glob——
    # 這個 repo 這一輪才剛因為「同一個問題有兩份實作」吃過虧。
    strays = [path.name for path in negative_control.stray_recipes()]
    assert strays == [], (
        f"這幾份配方沒有住在 tools/mutations/ 底下：{strays}。"
        "沒有人擁有的檔案沒有人維護，這正是上一次四條配方腐爛的原因。"
    )


@pytest.mark.parametrize(
    "rot",
    [
        pytest.param(
            {"replace": "這串字絕對不在任何一個檔案裡 :: sentinel"},
            id="needle-no-longer-matches",
        ),
        pytest.param(
            {"file": "app/does_not_exist.py"},
            id="target-file-is-gone",
        ),
        pytest.param(
            {"test": "tests/test_cli_runner.py::test_that_was_renamed_away"},
            id="test-was-renamed",
        ),
        pytest.param(
            {"with": None},  # None 代表「跟 replace 一樣」，下面填
            id="mutation-changes-nothing",
        ),
    ],
)
def test_the_check_itself_has_teeth(tmp_path: Path, rot: dict) -> None:
    """這條檢查自己被弄紅過。

    一條「檢查配方有沒有腐爛」的檢查，如果它自己壞掉的時候是綠的，那它就是
    這個專案在抓的那種假綠燈。所以這裡拿四種真的腐爛形狀去打它，每一種都必須
    被講出來——而且不能只是「有回報問題」，要回報到**那一條**。
    """
    import json

    negative_control = _load_negative_control()

    healthy = {
        "label": "SENTINEL 只給這條測試用",
        "file": "tools/negative_control.py",
        "replace": "def check_recipes(",
        "with": "def check_recipes_disabled(",
        "test": "tests/test_the_negative_control_recipes_still_bite.py"
        "::test_the_check_itself_has_teeth",
    }
    # 先證明健康的那一份是乾淨的，否則下面的紅可能來自別的地方。
    good = tmp_path / "mutations_sentinel.json"
    good.write_text(json.dumps([healthy], ensure_ascii=False), encoding="utf-8")
    assert negative_control.check_recipes([good]) == []

    broken = dict(healthy)
    for key, value in rot.items():
        broken[key] = healthy["replace"] if value is None else value
    bad = tmp_path / "mutations_sentinel_rotted.json"
    bad.write_text(json.dumps([broken], ensure_ascii=False), encoding="utf-8")

    problems = negative_control.check_recipes([bad])
    assert problems, f"腐爛的配方沒有被講出來：{broken}"
    assert any("SENTINEL" in problem for problem in problems), (
        "有回報問題，但沒有指出是哪一條配方——一頁看不出對象的抱怨，"
        f"下一個人會直接略過：{problems}"
    )


def _load_answer(module_path: Path, unique_name: str) -> str:
    """把 `module_path` 當模組載進來，回傳它的 ANSWER。

    走 SourceFileLoader，也就是 CPython 平常匯入 `.py` 時走的那條路——它會
    寫、也會讀 `__pycache__` 底下的 `.pyc`，而快取有沒有過期是用（原始碼的
    mtime，取整秒 + 原始碼的位元組數）判斷的。這條測試要的就是那個判斷。
    """
    spec = importlib.util.spec_from_file_location(unique_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.ANSWER)


def test_the_second_mutation_is_not_run_as_the_first_one(
    tmp_path: Path, monkeypatch
) -> None:
    """負向驗證工具不准把上一條破壞的位元碼當成這一條來跑。

    2026-08-21 實測到的真實情況：`never_started=True,` 這一行在 `CliRunner.run`
    裡出現三次，於是同一批裡有兩條破壞各刪掉其中一行——**產生的檔案位元組數
    一模一樣**，而且套用的時間差不到一秒。CPython 判斷 `.pyc` 過不過期只看
    （整秒的 mtime, 位元組數），兩個都對得上，所以第二條破壞跑的其實是第一條
    的位元碼。

    這件事的份量不在「量錯了一次」，在於它**錯的方向**：受測程式根本沒被改到，
    測試當然通過，工具於是印出「綠 ✗ 沒有牙齒」。而這個專案裡「沒有牙齒」的
    意思是「這條測試沒在守什麼」，下一個人照著它做的事是**刪掉一條其實有效的
    測試**。一個假的紅燈只會浪費時間，一個假的「沒有牙齒」會拆掉防線。

    修法是 `tools/negative_control.py::_drop_stale_bytecode`，套用前與還原後
    各清一次快取。這條測試就是那段程式碼的守衛：把它變成 no-op，最後那個斷言
    就會拿到 'first'。

    ## 這條測試自己決定要不要寫 `.pyc`，**不看環境的臉色**

    同一個假綠燈 2026-08-25 補了第二道：`tools/negative_control.py` 現在
    替它的兩個子行程帶 `PYTHONDONTWRITEBYTECODE=1`，整輪一個新的 `.pyc`
    都不產生。而**這條測試是被那支工具跑的**——繼承那個設定的話，下面那句
    「這台機器有沒有寫出 `.pyc`」的前提檢查會**必定**失敗。

    後果不是少測一次，是 `tools/mutations/mutations_recipe_rot_guard.json`
    裡指著這一條的 G1／G5／G6 **有沒有被抓到都回報「紅 ✓」**——而一條恆紅的
    負向驗證跟一條恆綠的一樣，什麼都沒有量到，只是讀起來像量過了。
    量法：`PYTHONDONTWRITEBYTECODE=1` 跑這一條，沒有這一行的時候它紅在
    第 201 行的前提檢查上，破壞一個字都還沒套。

    所以 `.pyc` 寫不寫由這一條自己說了算：它量的就是 `.pyc` 的行為。
    """
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    negative_control = _load_negative_control()

    module_path = tmp_path / "victim_module.py"
    # 同一整秒。真實情況裡這是「兩條破壞套得很近」自然發生的，這裡直接指定，
    # 免得這條測試變成一條看機器快慢決定紅綠的擲骰子。
    same_whole_second = (1_700_000_000, 1_700_000_000)

    module_path.write_text("ANSWER = 'first'\n", encoding="utf-8")
    os.utime(module_path, same_whole_second)
    assert _load_answer(module_path, "_aicc_victim_before") == "first"

    cache = tmp_path / "__pycache__"
    assert list(cache.glob("victim_module.*.pyc")), (
        "這台機器沒有寫出 .pyc（sys.dont_write_bytecode 或 sys.pycache_prefix "
        "被動過？），那這條測試就證明不了它要證明的事——不要把它改成跳過，"
        "先弄清楚為什麼。"
    )

    # 第二條破壞：同一個檔案、**同樣的位元組數**、同樣那一整秒。
    module_path.write_text("ANSWER = 'secnd'\n", encoding="utf-8")
    os.utime(module_path, same_whole_second)

    assert _load_answer(module_path, "_aicc_victim_stale") == "first", (
        "預期中的危險沒有重現：這個 Python 已經不是用（整秒 mtime, 位元組數）"
        "判斷 .pyc 過不過期了（例如改用 hash-based .pyc）。那 "
        "_drop_stale_bytecode 可能已經不需要，但**先確認**再拿掉它，"
        "不要因為這條測試紅了就刪。"
    )

    negative_control._drop_stale_bytecode(module_path)

    assert _load_answer(module_path, "_aicc_victim_after") == "secnd", (
        "清了快取之後載進來的還是上一條破壞的位元碼——"
        "`tools/negative_control.py::_drop_stale_bytecode` 沒有真的把 .pyc 丟掉。"
        "後果不是量不準，是**工具會謊報「綠 ✗ 沒有牙齒」**：受測程式根本沒被"
        "改到，測試當然通過，而下一個人會照著那句話去刪一條其實有效的測試。"
    )
