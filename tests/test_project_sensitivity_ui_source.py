from __future__ import annotations

from pathlib import Path


def test_orchestrator_decision_gate_uses_project_sensitivity() -> None:
    orchestrator_source = Path("app/core/orchestrator.py").read_text(
        encoding="utf-8"
    )
    prompt_builder_source = Path("app/core/prompt_builder.py").read_text(
        encoding="utf-8"
    )

    assert "ProjectRegistry.get_sensitivity(self.project_path)" in (
        orchestrator_source
    )
    assert "prompt_builder.build_decision_gate_prompt(" in orchestrator_source
    assert "sensitive_kyc" in prompt_builder_source
    assert "一律必須 ASK_USER" in prompt_builder_source


def test_main_window_exposes_project_sensitivity_control() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    # Was the combo's objectName. The control is a select in the shell's
    # execution bar now, so what identifies it is the field pushed to the page
    # and the action key sent back.
    assert '"sensitivity"' in source
    assert '"set:sensitivity"' in source
    assert "_on_sensitivity_changed" in source
    assert "ProjectRegistry.set_sensitivity(self.project_path" in source
    assert "ProjectRegistry.get_sensitivity(" in source
