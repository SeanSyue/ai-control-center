"""Who last wrote each shared-memory file.

The panel shows "由 CLAUDE 更新" next to a file, so the answer has to be
recorded rather than guessed. These tests pin the two properties that make it
trustworthy: a declared author is stored, and an undeclared one records nothing
at all instead of defaulting to a plausible name.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.services.project_knowledge import ProjectKnowledgeStore


def _store(tmp_path: Path) -> ProjectKnowledgeStore:
    project = tmp_path / "project"
    project.mkdir()
    store = ProjectKnowledgeStore(project)
    store.ensure_memory_files()
    return store


def test_a_declared_author_is_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.write_memory_file("DECISIONS.md", "- 決定了一件事", author="codex")

    record = store.read_provenance()
    assert record["DECISIONS.md"]["by"] == "codex"
    assert record["DECISIONS.md"]["at"]


def test_an_undeclared_author_records_nothing(tmp_path: Path) -> None:
    # The default must not become "someone plausible". No entry means the panel
    # shows no author at all, which is the honest outcome.
    store = _store(tmp_path)

    store.write_memory_file("DECISIONS.md", "- 沒有人宣告自己是誰")

    assert "DECISIONS.md" not in store.read_provenance()


def test_an_unrecognised_author_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.write_memory_file("DECISIONS.md", "- 內容", author="gemini")

    assert "DECISIONS.md" not in store.read_provenance()


def test_writing_again_replaces_the_previous_author(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.write_memory_file("DECISIONS.md", "- 先由 codex 寫", author="codex")
    store.write_memory_file("DECISIONS.md", "- 再由使用者改", author="you")

    assert store.read_provenance()["DECISIONS.md"]["by"] == "you"


def test_other_files_keep_their_own_author(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.write_memory_file("DECISIONS.md", "- a", author="codex")
    store.write_memory_file("NEXT_STEPS.md", "- b", author="you")

    record = store.read_provenance()
    assert record["DECISIONS.md"]["by"] == "codex"
    assert record["NEXT_STEPS.md"]["by"] == "you"


def test_split_memory_text_attributes_every_section(tmp_path: Path) -> None:
    store = _store(tmp_path)

    written = store.write_memory_from_text(
        "### Decisions\n- 一\n\n### Next Steps\n- 二\n", author="codex"
    )

    record = store.read_provenance()
    assert written
    for name in written:
        assert record[name]["by"] == "codex"


def test_a_corrupt_sidecar_is_ignored_rather_than_fatal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sidecar = store.knowledge_path / ProjectKnowledgeStore.PROVENANCE_FILE
    sidecar.write_text("{ this is not json", encoding="utf-8")

    assert store.read_provenance() == {}

    store.write_memory_file("DECISIONS.md", "- 內容", author="you")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["DECISIONS.md"]["by"] == "you"


def test_the_memory_prompt_asks_for_exactly_the_files_the_writer_accepts() -> None:
    """Two lists that must agree, and both fail silently when they do not.

    The memory-update prompt tells Codex 「必須使用以下固定標題」 and
    `write_memory_from_text` skips any section whose heading is not in
    `MEMORY_FILES`. So:

    * a heading in the prompt but not in `MEMORY_FILES` → Codex writes that
      section every run and it is dropped on arrival, paid for and discarded;
    * a file in `MEMORY_FILES` but not in the prompt → that memory file is
      never updated again, and nothing anywhere says so.

    Neither shows up as an error. `PITCH.md` is the live example of why this
    is easy to get wrong: it sits in `.aicc/knowledge/` next to the eight, but
    it is **not** a memory file - it is marketing copy with its own checklist,
    and letting the maintainer rewrite it is exactly the mistake this check
    exists to stop somebody making while "fixing" the list.
    """
    import re
    from pathlib import Path

    from app.services.project_knowledge import MEMORY_FILES

    prompt_source = Path("app/core/prompt_builder.py").read_text(encoding="utf-8")
    block = prompt_source.split("def build_memory_update_prompt")[1]
    asked_for = re.findall(r"^## ([A-Z_]+\.md)$", block, re.M)

    assert asked_for, "找不到記憶更新 prompt 裡的標題清單"
    assert list(dict.fromkeys(asked_for)) == asked_for, "prompt 裡有重複的標題"
    assert set(asked_for) == set(MEMORY_FILES), (
        "記憶更新 prompt 要的檔案跟寫入端接受的檔案對不上。"
        f"prompt 多要的：{set(asked_for) - set(MEMORY_FILES)}；"
        f"prompt 漏掉的：{set(MEMORY_FILES) - set(asked_for)}"
    )


def test_pitch_is_not_a_memory_file() -> None:
    # Pinned separately from the list check, because "the two lists agree" and
    # "PITCH is excluded from both" are different claims, and someone adding
    # it to both would keep the first one green.
    from app.services.project_knowledge import MEMORY_FILES

    assert "PITCH.md" not in MEMORY_FILES
