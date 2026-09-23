from __future__ import annotations


from pathlib import Path

from app.core.orchestrator import BOOTSTRAP_MARKER


def test_orchestrator_injects_retrieved_context() -> None:
    """The orchestrator still *wires* retrieved context into every prompt.

    Split across two files by ROADMAP P10 Stage A: the orchestrator decides
    what to retrieve and hands it over, `prompt_builder` decides where in the
    text it goes. Asserting only against `orchestrator.py` used to cover both,
    and after the move it would silently cover only half - the wiring could
    stay while the prompt stopped carrying the context, and this file would
    still be green.
    """
    source = Path("app/core/orchestrator.py").read_text(encoding="utf-8")
    prompts = Path("app/core/prompt_builder.py").read_text(encoding="utf-8")

    assert "ProjectKnowledgeStore" in source
    assert "knowledge-context.md" in source
    # 2026-08-25：這一行本來比對的是 `orchestrator.py` 裡有沒有出現
    # `SESSION_BOOTSTRAP.md` 這幾個字，而那幾個字**是 `get_git_diff()` 排除
    # 清單裡的一條路徑**——不是任何接線。也就是說它一直綠著，但它守的不是
    # 它宣稱在守的那件事：真正寫、真正讀那份檔案的是 `project_knowledge.py`，
    # 就算 orchestrator 完全不再提它，那條路徑也會讓這一行過。
    # 排除清單改成共用常數之後它才第一次變紅——改成問真的在做那件事的檔案。
    knowledge = Path("app/services/project_knowledge.py").read_text(
        encoding="utf-8"
    )
    assert "SESSION_BOOTSTRAP.md" in knowledge
    # 2026-08-24：那個標記搬去 `app/core/prompt_markers.py` 了（它是**比對用**
    # 的字，不是介面文字）。**守的東西沒有變**：orchestrator 要用它。
    assert "BOOTSTRAP_MARKER" in source

    markers = Path("app/core/prompt_markers.py").read_text(encoding="utf-8")

    assert BOOTSTRAP_MARKER in markers
    assert '"project_context": knowledge_context' in source
    assert "context_profile=self.settings.context_profile" in source

    # The orchestrator passes it in ...
    assert "knowledge_context=knowledge_context" in source
    # ... and the templates put it under a heading the model can see.
    assert "共同專案記憶與相關文件" in prompts
    assert "專案共同記憶與相關文件" in prompts
