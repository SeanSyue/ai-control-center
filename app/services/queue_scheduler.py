"""Pure decision logic for the dashboard queue auto-scheduler.

Kept separate from `app/ui/main_window.py` so "which queued project
should start next" is unit testable without QApplication,
AISettingsStore, or any Qt widget. The Qt side effects (switching the
visible project, appending to the log, acquiring the workflow lock,
launching a worker) stay in
`MainWindow._maybe_start_next_queued_project`, which composes this
module's pure decision with those side effects.
"""

from __future__ import annotations

from typing import Callable, Iterator, Sequence

from app.services.ai_settings import AIExecutionSettings
from app.services.project_registry import ProjectEntry
from app.i18n import t


def is_high_usage(settings: AIExecutionSettings) -> bool:
    """Return True when a run's settings likely consume a lot of quota.

    The queue auto-scheduler never auto-starts a high-usage candidate
    unattended - it leaves it queued for manual confirmation instead of
    either silently starting it or blocking on a confirmation dialog
    nobody is there to click.
    """
    return (
        settings.expected_model_calls >= 5
        or settings.claude_effort in {"high", "xhigh", "max", "ultracode"}
        or settings.codex_effort in {"high", "xhigh"}
        or settings.claude_max_turns == 0
        or settings.claude_max_turns >= 50
        or settings.claude_timeout_minutes == 0
    )


def nothing_started_reason(
    high_usage: Sequence[str], locked: Sequence[str]
) -> list[str]:
    """佇列有東西、容量也夠，卻一個都沒開始——為什麼。

    這裡是純函式而不是 `main_window` 裡的幾個 if，因為它要講的是**理由**，
    而理由講錯的代價是使用者去錯的畫面找問題。原本只有一句話（「用量設定
    較高」），而它在鎖定的情況下也會被印出來——那時日誌上一行才剛說是鎖定。

    兩種原因可以同時成立（一個候選高用量、另一個鎖著），所以是列表不是
    三選一：只講其中一個，另一個就變成使用者永遠不會去看的地方。
    """
    lines: list[str] = []
    if locked:
        # 名字不重複講：每一個被鎖住的候選在略過的當下已經各自記過一行了。
        # 這一句只加新的東西——「那可能是殘留的鎖定，而且看得到」。
        lines.append(
            t("queue.stale_lock_hint")
        )
    if high_usage:
        names = "、".join(f"「{name}」" for name in high_usage)
        lines.append(
            f"佇列排程：{names}用量設定較高，需要手動確認後才會開始。"
        )
    if not lines:
        # 走得到：候選清單在拿到之後、`next()` 之前被別的視窗清空。
        # 講「不知道為什麼」也比套一個現成的錯理由好。
        lines.append(t("queue.nothing_to_start"))
    return lines


def select_eligible_candidates(
    candidates: Sequence[ProjectEntry],
    load_settings: Callable[[ProjectEntry], AIExecutionSettings],
    on_skip: Callable[[ProjectEntry], None] | None = None,
) -> Iterator[tuple[ProjectEntry, AIExecutionSettings]]:
    """Yield `(candidate, settings)` pairs for non-high-usage candidates.

    High-usage candidates are skipped entirely (order otherwise
    preserved) rather than picking just the first eligible one, because
    the caller may still need to skip a yielded candidate for other
    reasons - not found in the project selector, a workflow lock that's
    held by another process - and try the next eligible one in turn.
    Settings are yielded alongside each candidate so the caller does not
    need to reload them once it decides to actually start one.

    This is a generator, not a list: `load_settings` is only called for
    a candidate when the caller actually asks for the next item.
    `AISettingsStore.load_project()` has a real filesystem side effect
    (it creates the target project's `.aicc` directory if missing), so a
    caller that stops iterating as soon as it starts a project - as
    `MainWindow._maybe_start_next_queued_project` does - must not have
    settings loaded for every remaining queued candidate it never looks
    at.

    `load_settings` loads the effective AI settings for a given
    candidate (normally
    `AISettingsStore(candidate.resolved_path).load_project`), kept as a
    callback so this function has no dependency on AISettingsStore or
    the filesystem.

    `on_skip` is called with each candidate skipped for high usage.
    Without it the caller cannot tell 「跳過了高用量的」 from 「一個候選都
    沒看」, and it was telling the user the wrong one: the scheduler's
    closing message said 「用量設定較高」 whenever nothing started, including
    when every candidate was skipped because its lock was held. Two
    contradictory lines in the same log, and the wrong one was last.

    A callback rather than a second return value, because the skips have
    to stay lazy for the same reason the yields do - counting them up
    front means calling `load_settings` on candidates the caller will
    never reach, and that call creates directories.
    """
    for candidate in candidates:
        settings = load_settings(candidate)
        if is_high_usage(settings):
            if on_skip is not None:
                on_skip(candidate)
            continue
        yield candidate, settings
