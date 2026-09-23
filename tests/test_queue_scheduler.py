from __future__ import annotations

from app.services.ai_settings import AIExecutionSettings
from app.services.project_registry import ProjectEntry
from app.services import queue_scheduler


def _settings(**overrides) -> AIExecutionSettings:
    """一份**便宜的**設定。這幾支測的是排程的挑選邏輯，不是出廠值。

    2026-08-27 之前這裡是 `AIExecutionSettings()`，而出廠值那天從計畫序列
    換成開發序列（老闆拍板），估算的呼叫次數從 1 變成 6——**出廠設定自己
    就過了高用量門檻**。留著原本的寫法的話，這幾支會全部退化成「每一個
    候選都是高用量」，而它們要測的排序、惰性、原樣帶回設定通通量不到。

    所以這裡明著把它調便宜：不修正、不維護共同記憶＝2 次呼叫。
    出廠值本身是哪一種，由底下
    `test_the_factory_settings_now_count_as_high_usage` 一條專門講。
    """
    base = AIExecutionSettings(
        max_fix_attempts=0,
        auto_memory_maintenance=False,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# --- is_high_usage -------------------------------------------------------------


def test_is_high_usage_false_for_cheap_settings() -> None:
    assert queue_scheduler.is_high_usage(_settings()) is False


def test_the_factory_settings_now_count_as_high_usage() -> None:
    """**出廠設定會跳確認視窗，佇列也不會自己開始它。**

    這不是這條測試在主張的事，是量到的：出廠序列 2026-08-27 換成開發序列
    之後，估算的呼叫次數是 6，而門檻是 5。使用者會看到的是——手動按開始
    多一個「本次設定可能消耗較多額度⋯是否繼續？」，而儀表板佇列**永遠
    不會自動開始任何一個沒有設定過的專案**，只會留一句「用量設定較高」。

    這一條擺在這裡是為了**讓那件事有人看得到**：門檻要不要跟著改是花錢的
    決定，不是這一輪自己能決定的事。哪天門檻動了，這一條會紅——而那時候
    要紅的正是它。
    """
    factory = AIExecutionSettings()

    assert queue_scheduler.is_high_usage(factory) is True, (
        "出廠設定不再算高用量了——如果那是有人刻意調的門檻，改這一條；"
        "如果不是，`expected_model_calls` 被誰改小了"
    )


def test_is_high_usage_true_for_many_expected_model_calls() -> None:
    # expected_model_calls 是算出來的（開發序列：2 + 2×修正上限 + 維護
    # 共同記憶 2），所以這裡驅動的是**開發序列真的會讀的那幾個欄位**。
    # 2026-08-27 之前這裡調的是 `planning_rounds`，而那個欄位只有計畫序列
    # 讀得到——出廠換成開發序列之後，它一次都不會被看，這條會變成
    # 「反正出廠就是高用量」的假綠燈。
    assert queue_scheduler.is_high_usage(
        _settings(max_fix_attempts=2, auto_memory_maintenance=True)
    )


def test_is_high_usage_true_for_high_claude_effort() -> None:
    assert queue_scheduler.is_high_usage(_settings(claude_effort="xhigh"))


def test_is_high_usage_true_for_high_codex_effort() -> None:
    assert queue_scheduler.is_high_usage(_settings(codex_effort="high"))


def test_is_high_usage_true_for_unlimited_max_turns() -> None:
    assert queue_scheduler.is_high_usage(_settings(claude_max_turns=0))


def test_is_high_usage_true_for_unlimited_timeout() -> None:
    assert queue_scheduler.is_high_usage(_settings(claude_timeout_minutes=0))


# --- select_eligible_candidates -------------------------------------------------


def _entry(name: str) -> ProjectEntry:
    return ProjectEntry(name=name, path=f"/tmp/{name}")


def test_select_eligible_candidates_filters_out_high_usage_preserving_order() -> (
    None
):
    cheap_a = _entry("cheap-a")
    expensive = _entry("expensive")
    cheap_b = _entry("cheap-b")
    settings_by_name = {
        "cheap-a": _settings(),
        # 「貴」現在要用開發序列讀得到的欄位表達（見 `_settings` 的說明）。
        "expensive": _settings(max_fix_attempts=2, auto_memory_maintenance=True),
        "cheap-b": _settings(),
    }

    eligible = list(
        queue_scheduler.select_eligible_candidates(
            [cheap_a, expensive, cheap_b],
            load_settings=lambda candidate: settings_by_name[candidate.name],
        )
    )

    assert [candidate.name for candidate, _ in eligible] == [
        "cheap-a",
        "cheap-b",
    ]


def test_select_eligible_candidates_empty_when_all_high_usage() -> None:
    only = _entry("expensive")

    eligible = list(
        queue_scheduler.select_eligible_candidates(
            [only],
            load_settings=lambda candidate: _settings(
                max_fix_attempts=2, auto_memory_maintenance=True
            ),
        )
    )

    assert eligible == []


def test_select_eligible_candidates_empty_for_empty_input() -> None:
    assert (
        list(
            queue_scheduler.select_eligible_candidates(
                [], load_settings=lambda candidate: _settings()
            )
        )
        == []
    )


def test_select_eligible_candidates_returns_settings_alongside_candidate() -> (
    None
):
    entry = _entry("solo")
    expected_settings = _settings(claude_model="opus")

    eligible = list(
        queue_scheduler.select_eligible_candidates(
            [entry], load_settings=lambda candidate: expected_settings
        )
    )

    assert eligible == [(entry, expected_settings)]


def test_select_eligible_candidates_is_lazy_and_stops_loading_settings_once_caller_stops() -> (
    None
):
    # Regression test: an earlier version eagerly loaded settings for
    # every candidate up front. AISettingsStore.load_project() has a real
    # filesystem side effect (creating the project's .aicc directory), so
    # a caller that stops as soon as it finds a usable candidate - as
    # MainWindow._maybe_start_next_queued_project does - must never
    # trigger that side effect for candidates it never looks at.
    first = _entry("first")
    second = _entry("second")
    third = _entry("third")
    loaded_for: list[str] = []

    def load_settings(candidate: ProjectEntry) -> AIExecutionSettings:
        loaded_for.append(candidate.name)
        return _settings()

    generator = queue_scheduler.select_eligible_candidates(
        [first, second, third], load_settings=load_settings
    )

    candidate, _ = next(generator)

    assert candidate is first
    assert loaded_for == ["first"]

    generator.close()
    assert loaded_for == ["first"]
