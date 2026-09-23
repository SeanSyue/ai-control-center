from __future__ import annotations

from pathlib import Path

from app.services.project_registry import ProjectRegistry
from app.ui.diagnostics_dialog import install_crash_handler, run_startup_checks
from app.ui.main_window import MainWindow, create_application
from app.ui.shutdown import run_until_closed


def resolve_initial_project_path(registry: ProjectRegistry) -> Path:
    """Pick a project folder to open at startup.

    MainWindow's constructor already prefers project_registry's last_project
    once it matches a currently-registered project, so this only needs to
    produce a real, existing folder for the very first launch ever - before
    anything has been registered (a fresh install has no sandbox/demo_project
    to fall back to; that folder is a dev-only fixture, never packaged).
    """
    projects, last_project = registry.load()

    if last_project and Path(last_project).is_dir():
        return Path(last_project)

    for entry in projects:
        if entry.resolved_path.is_dir():
            return entry.resolved_path

    default = Path.home() / "Documents" / "AI Control Center"
    default.mkdir(parents=True, exist_ok=True)
    return default


def main() -> int:
    app = create_application()
    # Before the window, so a machine missing Claude Code says so up front
    # rather than through a run that produces no output twenty minutes later.
    # Never blocks: see run_startup_checks.
    install_crash_handler(app)
    run_startup_checks()

    project_path = resolve_initial_project_path(ProjectRegistry())

    # `window.show()` + `app.exec()` **不寫在這裡**。
    #
    # 寫在這裡的時候，`app.exec()` 回來之後就直接 `return` 了，而那一刻
    # 陣容頁的背景掃描還在讀硬碟。接著 `QApplication` 被解構，Qt 撞到一條
    # 還在跑的 QThread，整個行程以 0xC0000409 死掉——視窗已經消失、
    # `app.exec() returned 0` 已經印出來，所以**使用者看到的是「關掉的時候
    # 當了」**，而那是他對整個產品的最後一個印象。
    #
    # 收尾那幾行搬進 `app/ui/shutdown.py`，理由是釘住這件事的測試要跑
    # **同一支**：分成兩份寫的話，測試會綠著而產品照樣當。
    return run_until_closed(app, MainWindow(project_path))


if __name__ == "__main__":
    raise SystemExit(main())
