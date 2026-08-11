from __future__ import annotations

from pathlib import Path

from memrelay_eval.scoring.blinding import BlindingPolicy, detect_direct_leaks


def test_scoring_does_not_import_assignment_resolution() -> None:
    scoring = Path(__file__).parents[3] / "src" / "memrelay_eval" / "scoring"
    source = "\n".join(path.read_text(encoding="utf-8") for path in scoring.glob("*.py"))

    assert "domain.assignment" not in source
    assert "AssignmentPort" not in source
    assert "ProvisioningAssignmentPort" not in source


def test_every_named_metadata_leak_class_is_detected_without_echoing_values() -> None:
    policy = BlindingPolicy(treatment_aliases=("control",))
    categories = detect_direct_leaks(
        {
            "arm_name": "control",
            "assignment_record": "opaque",
            "provider_name": "provider",
            "tool_name": "tool",
            "duration_ms": 1,
            "run_order": 1,
            "workspace_path": "C:\\memrelay\\run",
        },
        policy,
    )

    assert categories == ("assignment", "ordering", "path", "provider", "timing", "tool")
