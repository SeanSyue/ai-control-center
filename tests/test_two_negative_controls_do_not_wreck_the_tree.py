"""負向驗證一次只准一支在跑。

這支工具的做法是「整檔複製 → 改壞 → 跑測試 → 還原」。兩支同時跑的時候，
**第二支存下來的「原檔」其實是第一支的破壞**，還原之後那個破壞就永久
留在樹上了。

2026-08-27 這件事一天內真的發生兩次：`app/core/orchestrator.py:2277` 被留在
`seat_with_duty(DUTY_REVIEW, fallback=ROLE_REVIEWER)`——正好是另一份配方的
破壞字串。代價是兩條測試紅著、三條配方腐爛。

**最貴的不是那個破壞，是它留下的樣子。**它讓一條好好的檢查印出
「綠 ✗ 沒有牙齒」，而工具接著印的那句話是：

> 去看看那條測試在守什麼，如果答案是「一個沒有人讀的值」，那條紅燈是假警報。

照著讀，下一步就是**刪掉一條有用的測試**。

同型的前一次是 2026-08-10 那個 `git checkout --`。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "negative_control.py"
LOCK = ROOT / ".aicc" / "runtime" / "negative_control.lock"


def test_a_second_run_is_turned_away_instead_of_racing() -> None:
    """**這一條是這份檔案存在的理由。**

    真的把鎖放上去，真的跑第二支，看它有沒有停下來。
    改壞的樣子就是它 2026-08-27 之前的樣子：兩支一起跑，誰都不知道。
    """
    assert not LOCK.exists(), f"跑之前就有鎖在：{LOCK}（上一次沒收乾淨？）"
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text("pid 0（這是測試放的）", encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--check"],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
    finally:
        LOCK.unlink(missing_ok=True)

    said = proc.stdout + proc.stderr
    assert proc.returncode == 3, (
        f"第二支照跑了（離開碼 {proc.returncode}）——"
        "它會把第一支的破壞當成原檔存起來，還原之後那個破壞永久留在樹上"
    )
    assert "已經有另一支" in said, f"它沒說為什麼停下來：{said[:200]}"
    # **停下來還不夠，要講得出怎麼繼續。**機器當掉的話那個鎖會留著，
    # 而一個沒有出路的錯誤訊息，下一步就是有人去改程式繞過它。
    assert str(LOCK) in said or LOCK.name in said, (
        "它沒有告訴人鎖在哪——機器當掉之後那個檔案會留著，"
        "而看不到路的人會直接把這道鎖拿掉"
    )


def test_the_lock_is_actually_wrapped_around_the_run() -> None:
    """**「被定義」不等於「被叫到」。**

    2026-08-27 同一天已經因為這件事寫過三條沒有牙齒的破壞：只比函式名字
    在不在，而它躺在檔案裡沒有人呼叫的時候，那條檢查照樣是綠的。
    """
    src = TOOL.read_text(encoding="utf-8")
    assert "with _the_only_one_touching_the_tree():" in src, (
        "那道鎖沒有真的包在執行外面——定義在檔案裡不算，要有人 with 它"
    )
    assert "except AnotherRunHasTheTree" in src, (
        "撞到鎖之後沒有人接住——使用者會看到一坨 traceback，"
        "而 traceback 讀起來像「這支工具壞了」，不是「等它跑完再跑」"
    )


def test_a_normal_run_puts_the_lock_back() -> None:
    """跑完要把鎖收掉。

    收不掉的話，**第一次跑完之後這支工具就再也跑不動了**——
    而它會用一句「已經有另一支在跑」把自己擋在門外。
    """
    proc = subprocess.run(
        [sys.executable, str(TOOL)],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    # 3 是「有人在跑」；2 是 argparse 的「參數打錯」，空手跑本來就會回 2。
    assert proc.returncode != 3, "空手跑就撞到鎖了"
    assert not LOCK.exists(), (
        f"跑完之後鎖還在：{LOCK}——下一支會被自己上一次的鎖擋掉"
    )
