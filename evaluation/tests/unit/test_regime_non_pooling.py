from __future__ import annotations

import pytest
from memrelay_eval.domain.errors import AnalysisBoundaryError
from memrelay_eval.domain.states import EvaluationStratum, HistoryMode
from memrelay_eval.orchestration.history import (
    SequenceAnalysisIdentity,
    require_same_sequence_analysis_identity,
)


def test_dynamic_sequence_analysis_rejects_controlled_or_cross_stratum_pooling() -> None:
    dynamic_product = SequenceAnalysisIdentity(
        HistoryMode.DYNAMIC, EvaluationStratum.PRODUCT, "a" * 64
    )
    dynamic_engine = SequenceAnalysisIdentity(
        HistoryMode.DYNAMIC, EvaluationStratum.DIRECT_ENGINE, "a" * 64
    )
    controlled_product = SequenceAnalysisIdentity(
        HistoryMode.CONTROLLED, EvaluationStratum.PRODUCT, "a" * 64
    )

    assert (
        require_same_sequence_analysis_identity((dynamic_product, dynamic_product))
        == dynamic_product
    )
    with pytest.raises(AnalysisBoundaryError):
        require_same_sequence_analysis_identity((dynamic_product, dynamic_engine))
    with pytest.raises(AnalysisBoundaryError):
        require_same_sequence_analysis_identity((dynamic_product, controlled_product))
