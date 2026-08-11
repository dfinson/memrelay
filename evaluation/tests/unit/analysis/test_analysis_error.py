from __future__ import annotations

from memrelay_eval.domain.errors import AnalysisError


def test_analysis_error_preserves_single_code_story_5_3_call_shape() -> None:
    error = AnalysisError("assignment_lineage_incomplete")

    assert error.code == "assignment_lineage_incomplete"
    assert error.fields == ()
    assert str(error) == "assignment lineage incomplete"
    assert error.args == ("assignment lineage incomplete",)


def test_analysis_error_retains_story_5_2_conflicting_dimension_fields() -> None:
    error = AnalysisError(
        "explicit_stratified_operation_required",
        ("environment_fingerprint_sha256", "model_id"),
    )

    assert error.code == "explicit_stratified_operation_required"
    assert error.fields == ("environment_fingerprint_sha256", "model_id")
    assert str(error) == "explicit stratified operation required"
    assert error.args == ("explicit stratified operation required",)
