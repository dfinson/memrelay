from __future__ import annotations

import asyncio

from memrelay_eval.adapters.copilot import session as session_adapter
from memrelay_eval.adapters.copilot.catalog import (
    UNAVAILABLE,
    archive_native_catalog,
    eligible_models,
    select_models,
)
from memrelay_eval.domain.entities import (
    ModelQualification,
    QualificationCaps,
    QualificationTaskResult,
    QualificationUsage,
)


def model(
    identifier: str,
    family: str | None,
    *,
    tools: bool = True,
) -> dict[str, object]:
    return {
        "id": identifier,
        **({} if family is None else {"family": family}),
        "capabilities": {
            "tools": tools,
            "permissions": True,
            "context": 128000,
            "events": True,
            "cancellation": True,
            "sessions": True,
        },
        "reasoning_effort": "high",
        "context_tier": "large",
    }


def qualification(
    native_id: str, passes: int, protected: float, credits: float, active: float
) -> ModelQualification:
    return ModelQualification(
        native_id,
        tuple(
            QualificationTaskResult(
                executable_passed=index < passes,
                protected_check_fraction=protected,
                usage=QualificationUsage(
                    sessions=1,
                    credits=credits,
                    tokens=10,
                    active_seconds=active,
                    wall_seconds=active + 1,
                ),
            )
            for index in range(8)
        ),
    )


def test_catalog_archives_exact_raw_bytes_and_marks_missing_fields_unavailable() -> None:
    raw = b'{"models":[{"id":"native-a"}]}'
    archive = archive_native_catalog(raw, {"models": [model("native-a", None)]})

    assert archive.raw_bytes == raw
    assert archive.catalog.models[0].family == UNAVAILABLE
    assert archive.catalog.raw_sha256 != archive.catalog.projection_sha256


def test_capability_filter_never_infers_missing_native_capabilities() -> None:
    archive = archive_native_catalog(
        b'{"models":[]}',
        {"models": [model("eligible", "a"), model("ineligible", "b", tools=False)]},
    )

    assert [model.native_id for model in eligible_models(archive.catalog)] == ["eligible"]


def test_selection_uses_frozen_ranking_family_and_credit_rules() -> None:
    archive = archive_native_catalog(
        b'{"models":[]}',
        {
            "models": [
                model("native-a", "a"),
                model("native-b", "b"),
                model("native-c", "c"),
                model("native-d", "d"),
            ]
        },
    )
    selection = select_models(
        archive.catalog,
        (
            qualification("native-a", 8, 1.0, 4, 5),
            qualification("native-b", 8, 0.9, 1, 9),
            qualification("native-c", 8, 0.95, 0.5, 2),
            qualification("native-d", 6, 1.0, 1, 1),
        ),
    )

    assert selection.m0.native_id == "native-a"
    assert selection.m1 is not None and selection.m1.native_id == "native-c"
    assert selection.m2 is not None and selection.m2.native_id == "native-c"
    assert [judge.native_id for judge in selection.judges] == ["native-c", "native-b", "native-d"]
    assert "judges" not in selection.omissions


def test_selection_records_homogeneous_panel_when_no_family_diversity_is_feasible() -> None:
    archive = archive_native_catalog(
        b'{"models":[]}',
        {"models": [model(f"native-{index}", "shared") for index in range(4)]},
    )

    selection = select_models(
        archive.catalog,
        tuple(qualification(f"native-{index}", 8, 1.0, index + 1, 1) for index in range(4)),
    )

    assert selection.m1 is None
    assert selection.m2 is not None and selection.m2.native_id == "native-0"
    assert [judge.native_id for judge in selection.judges] == [
        "native-1",
        "native-2",
        "native-3",
    ]
    assert selection.omissions["judges"] == "homogeneous_panel_only"


def test_live_qualification_delegates_to_the_catalog_eligibility_authority(monkeypatch) -> None:
    archive = archive_native_catalog(
        b'{"models":[]}',
        {"models": [model("eligible", "a"), model("missing-tools", "b", tools=False)]},
    )
    captured = []

    async def fake_qualify(models, caps, execute):
        del caps, execute
        captured.extend(model.native_id for model in models)
        return (), QualificationUsage()

    monkeypatch.setattr(session_adapter, "qualify_models", fake_qualify)
    asyncio.run(
        session_adapter.qualify_native_catalog(
            archive.catalog,
            QualificationCaps(8, 8, 80, 8, 16),
        )
    )

    assert captured == [model.native_id for model in eligible_models(archive.catalog)]
