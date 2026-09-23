"""跨專案彙整（ROADMAP P8）。

這份彙整只有在它願意講壞消息的時候才有價值，所以大部分的斷言都是在檢查
「它有沒有安靜地把不該算的算進去」：驗不過的鏈、讀不到的專案、以及那個
真正要看的比例——已定案的決策裡有多少其實沒有人看過。
"""
from __future__ import annotations

from app.i18n import t

import json
from pathlib import Path

from app.services.decision_summary import (
    render_summary,
    summarise_all,
    summarise_project,
)
from app.services.long_task import DecisionLogEntry, DecisionLogStore
from app.services.project_registry import ProjectEntry, ProjectRegistry


def make_project(root: Path, name: str) -> Path:
    project = root / name
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    return project


def log(project: Path, *entries: DecisionLogEntry) -> DecisionLogStore:
    store = DecisionLogStore(project)
    for entry in entries:
        store.append(entry)
    return store


def asked(question: str = "要不要刪資料？", at: str = "2026-08-05T10:00:00"):
    return DecisionLogEntry(
        run_id="r1", action="ASK_USER", question=question, created_at=at
    )


def answered(at: str = "2026-08-05T10:05:00"):
    return DecisionLogEntry(
        run_id="r1",
        action="ANSWERED",
        question="要不要刪資料？",
        answer="不要。",
        source="claude",
        answered_by="user",
        created_at=at,
    )


def auto_approved(at: str = "2026-08-05T11:00:00"):
    return DecisionLogEntry(
        run_id="r1",
        action="CONTINUE",
        question="要不要加一個測試？",
        answer="加。",
        source="claude",
        created_at=at,
    )


# ---- one project ---------------------------------------------------------


def test_the_two_kinds_of_decision_are_counted_apart(tmp_path: Path) -> None:
    """人答的與 Codex 自己批的分開數，是這份彙整存在的全部理由。"""
    project = make_project(tmp_path, "a")
    log(project, asked(), answered(), auto_approved(), auto_approved())

    counts = summarise_project("A 案", project)

    assert counts.answered_by_person == 1
    assert counts.approved_by_codex == 2
    # Zero, not one. `asked()` and `answered()` are the two halves of the same
    # question, and this used to report 1 because it counted ASK_USER rows
    # instead of pairing them - so every question ever asked stayed in
    # 「還在等」 forever. Three decisions here (one answered, two auto), none
    # outstanding.
    assert counts.still_waiting == 0
    assert counts.open_questions == ()
    assert counts.total == 3


def test_a_date_window_actually_filters(tmp_path: Path) -> None:
    project = make_project(tmp_path, "a")
    log(
        project,
        answered(at="2026-07-31T23:59:59"),
        answered(at="2026-08-01T00:00:00"),
        answered(at="2026-08-31T23:59:59"),
        answered(at="2026-09-01T00:00:00"),
    )

    counts = summarise_project(
        "A 案", project, since="2026-08-01", until="2026-09-01"
    )

    assert counts.answered_by_person == 2


def test_an_unknown_action_lands_in_other_not_in_automatic(
    tmp_path: Path,
) -> None:
    """新的 action 不可以安靜地加進「Codex 自己批」那一堆。

    那一堆的大小就是這份彙整要看的東西，所以它必須是明列的，
    不能是「不是 ANSWERED 的都算」。
    """
    project = make_project(tmp_path, "a")
    log(
        project,
        DecisionLogEntry(
            run_id="r1",
            action="EXPORT_AUTHORISED",
            question="",
            created_at="2026-08-05T12:00:00",
        ),
    )

    counts = summarise_project("A 案", project)

    assert counts.approved_by_codex == 0
    assert counts.other == 1


# ---- what it refuses to count -------------------------------------------


def test_a_broken_chain_is_listed_but_not_added_to_the_total(
    tmp_path: Path,
) -> None:
    """安靜地算進去，等於用一份可能被改過的紀錄回答「我批准了幾件」。"""
    project = make_project(tmp_path, "a")
    store = log(project, answered(), auto_approved())

    path = store.path
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["answer"] = "改成別的答案"
    lines[0] = json.dumps(tampered, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    counts = summarise_project("A 案", project)

    assert counts.trustworthy is False
    assert counts.answered_by_person == 1, "數字還是要數出來，只是不採計"


def test_a_missing_project_is_reported_not_skipped(
    tmp_path: Path, monkeypatch
) -> None:
    """「這個專案不見了」跟「這個專案沒有決策」不可以長得一樣。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    registry = ProjectRegistry()
    registry.save([ProjectEntry("還在", str(make_project(tmp_path, "a")))], "")
    raw = json.loads(registry.config_path.read_text(encoding="utf-8"))
    raw["projects"].append({"name": "不見了", "path": str(tmp_path / "gone")})
    registry.config_path.write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )

    summary = summarise_all(registry=ProjectRegistry())

    missing = [p for p in summary.excluded if p.name == "不見了"]
    assert missing and missing[0].unreadable == t("project.path_missing.title")


def test_excluded_projects_stay_out_of_every_total(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    good = make_project(tmp_path, "good")
    log(good, answered(), auto_approved())

    registry = ProjectRegistry()
    registry.save([ProjectEntry("好的", str(good))], "")
    raw = json.loads(registry.config_path.read_text(encoding="utf-8"))
    raw["projects"].append({"name": "不見了", "path": str(tmp_path / "gone")})
    registry.config_path.write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )

    summary = summarise_all(registry=ProjectRegistry())

    assert summary.answered_by_person == 1
    assert summary.approved_by_codex == 1
    assert len(summary.excluded) == 1


# ---- the ratio -----------------------------------------------------------


def test_nothing_decided_gives_no_ratio_rather_than_zero_percent(
    tmp_path: Path, monkeypatch
) -> None:
    """0% 讀起來像一個嚇人的發現，而不是「沒有資料」。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = make_project(tmp_path, "a")
    log(project, asked())
    ProjectRegistry().save([ProjectEntry("A 案", str(project))], "")

    summary = summarise_all(registry=ProjectRegistry())

    assert summary.decided == 0
    assert summary.human_share is None
    assert "沒有「多少比例有人看過」可算" in render_summary(summary)


def test_a_mostly_automatic_month_says_so_in_words(
    tmp_path: Path, monkeypatch
) -> None:
    """數字對了還不夠——這個比例的意思要被講出來。

    「AI 做事、人把關」與「AI 做事」是兩個不同的產品，而分辨它們的就是這個
    比例。把 0.2 印出來卻不說它代表什麼，等於把判斷丟回給不會每天看這份表
    的人。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = make_project(tmp_path, "a")
    log(project, answered(), *[auto_approved() for _ in range(9)])
    ProjectRegistry().save([ProjectEntry("A 案", str(project))], "")

    summary = summarise_all(registry=ProjectRegistry())
    text = render_summary(summary)

    assert summary.human_share == 0.1
    assert "10%" in text
    assert "比較接近「AI 做事」" in text


def test_a_mostly_human_month_does_not_get_the_warning(
    tmp_path: Path, monkeypatch
) -> None:
    # 一直亮著的警告不會有人讀。
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = make_project(tmp_path, "a")
    log(project, *[answered() for _ in range(9)], auto_approved())
    ProjectRegistry().save([ProjectEntry("A 案", str(project))], "")

    text = render_summary(summarise_all(registry=ProjectRegistry()))

    assert "比較接近「AI 做事」" not in text


def test_the_output_says_it_carries_project_names(
    tmp_path: Path, monkeypatch
) -> None:
    # 專案名稱常常就是客戶名稱，而這份東西看起來很像可以直接轉寄的報表。
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    ProjectRegistry().save([], "")

    text = render_summary(summarise_all(registry=ProjectRegistry()))

    assert "含專案名稱" in text
    assert "不是可以直接寄出去的報表" in text


# ---- 未解問題集中在一個地方（ROADMAP P2） ---------------------------------


def test_an_answered_question_stops_counting_as_waiting(tmp_path: Path) -> None:
    """It used to count forever.

    An answered question writes two rows - ASK_USER then ANSWERED - and the
    count read the first one. So 「還在等」 was really 「問過幾次」, and a
    project that had settled everything still reported a backlog. Measured:
    one answered plus one open reported 2.
    """
    project = make_project(tmp_path, "a")
    log(project, asked(), answered(), asked("要不要通知客戶？"))

    counts = summarise_project("A 案", project)

    assert counts.still_waiting == 1
    assert counts.open_questions == ("要不要通知客戶？",)


def test_the_questions_themselves_are_carried_not_just_the_count(
    tmp_path: Path,
) -> None:
    # A count says there is something to deal with. It does not say what,
    # which is the part that decides whether you deal with it now.
    project = make_project(tmp_path, "a")
    log(project, asked("要不要把舊的匯出檔一起刪掉？"))

    counts = summarise_project("A 案", project)

    assert counts.open_questions == ("要不要把舊的匯出檔一起刪掉？",)


def test_the_open_list_gathers_every_project(tmp_path: Path, monkeypatch) -> None:
    """「散在 timeline」 means: one line, in one run's log, in one project's
    folder. This is the one place they are together."""
    from app.services.decision_summary import render_open_questions

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    a = make_project(tmp_path, "a")
    b = make_project(tmp_path, "b")
    log(a, asked("A 案要不要刪舊檔？"))
    log(b, asked("B 案要不要通知客戶？"))
    ProjectRegistry().save(
        [ProjectEntry("A 案", str(a)), ProjectEntry("B 案", str(b))], ""
    )

    text = render_open_questions(summarise_all(registry=ProjectRegistry()))

    assert "A 案要不要刪舊檔？" in text
    assert "B 案要不要通知客戶？" in text
    assert "含問題原文" in text, "印出問題原文卻沒有提醒，等於默默升級了敏感度"


def test_nothing_open_says_so_rather_than_printing_a_blank(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services.decision_summary import render_open_questions

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = make_project(tmp_path, "a")
    log(project, asked(), answered())
    ProjectRegistry().save([ProjectEntry("A 案", str(project))], "")

    text = render_open_questions(summarise_all(registry=ProjectRegistry()))

    assert text == "沒有還在等你回答的問題。"


def test_the_counts_view_points_at_the_flag_without_printing_the_text(
    tmp_path: Path, monkeypatch
) -> None:
    """The default view must not leak the question text.

    Counts carry project names; question text can carry a client's name and a
    case detail. Making the second one opt-in is the whole reason they are
    two functions.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = make_project(tmp_path, "a")
    log(project, asked("陳先生的帳戶要不要一起關掉？"))
    ProjectRegistry().save([ProjectEntry("A 案", str(project))], "")

    text = render_summary(summarise_all(registry=ProjectRegistry()))

    assert "--questions" in text
    assert "陳先生" not in text


def test_the_date_window_applies_to_open_questions_too(tmp_path: Path) -> None:
    """Found by negative control: every window case used answered entries.

    A monthly summary that silently included questions raised in April would
    make 「這個月還有幾件在等我」 an answer to a different question.
    """
    project = make_project(tmp_path, "a")
    log(
        project,
        asked("四月問的", at="2026-04-10T10:00:00"),
        asked("八月問的", at="2026-08-10T10:00:00"),
    )

    counts = summarise_project(
        "A 案", project, since="2026-08-01", until="2026-08-31"
    )

    assert counts.open_questions == ("八月問的",)
    assert counts.still_waiting == 1
