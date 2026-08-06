from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from memrelay_eval.domain.entities import (
    ArtifactRef,
    Assignment,
    Attempt,
    AttemptTerminal,
    Claim,
    CostEntry,
    Endpoint,
    Evidence,
    Experiment,
    ExposureDecision,
    FreshIsolationAttestation,
    History,
    InternalRetryPolicy,
    InternalRetryRecord,
    Protocol,
    RetryAuthorization,
    Run,
    Scenario,
    Task,
)
from memrelay_eval.domain.errors import InvalidAttemptTerminalError, InvalidIdentifierError
from memrelay_eval.domain.ids import (
    AssignmentId,
    AttemptId,
    ClaimId,
    CostEntryId,
    EndpointId,
    EvidenceId,
    ExperimentId,
    HistoryId,
    ProtocolId,
    RunId,
    ScenarioId,
    TaskId,
)
from memrelay_eval.domain.states import (
    AttemptTerminalKind,
    ExposureClassification,
    InternalRetrySubsystem,
)


def test_ids_are_typed_opaque_and_stably_serialized() -> None:
    identifier_types = (
        ExperimentId,
        ProtocolId,
        ScenarioId,
        TaskId,
        HistoryId,
        AssignmentId,
        RunId,
        AttemptId,
        EvidenceId,
        EndpointId,
        ClaimId,
        CostEntryId,
    )
    for identifier_type in identifier_types:
        identifier = identifier_type.new()
        assert str(identifier).startswith(f"{identifier_type.prefix}_")
        assert len(str(identifier).split("_", maxsplit=1)[1]) == 32
        assert identifier_type(str(identifier)) == identifier


@pytest.mark.parametrize(
    "value",
    [
        "exp_treatment00000000000000000000000",
        "exp_arm000000000000000000000000000000",
        "exp_ABCDEF0123456789ABCDEF0123456789",
        "not-exp_00000000000000000000000000000000",
    ],
)
def test_ids_reject_treatment_labels_and_nonopaque_values(value: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        ExperimentId(value)


def test_every_named_entity_is_frozen_and_treatment_neutral() -> None:
    experiment = Experiment(ExperimentId.new(), ProtocolId.new())
    assignment = Assignment(AssignmentId.new(), experiment.id)
    run = Run(RunId.new(), assignment.id)
    attempt = Attempt(AttemptId.new(), run.id)
    artifact = ArtifactRef.from_bytes(b"frozen evidence")
    terminal = AttemptTerminal(
        attempt.id,
        run.id,
        AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE,
        datetime.now(UTC),
        "provisioning failed",
        (artifact,),
    )
    records = (
        experiment,
        Protocol(ProtocolId.new()),
        Scenario(ScenarioId.new()),
        Task(TaskId.new()),
        History(HistoryId.new()),
        assignment,
        run,
        attempt,
        Evidence(EvidenceId.new(), ()),
        Endpoint(EndpointId.new()),
        Claim(ClaimId.new()),
        CostEntry(CostEntryId.new()),
        artifact,
        terminal,
        ExposureDecision(ExposureClassification.UNEXPOSED, (artifact,)),
        FreshIsolationAttestation(True, (artifact,)),
        InternalRetryPolicy(InternalRetrySubsystem.INSPECT, 1),
        InternalRetryRecord(attempt.id, InternalRetrySubsystem.INSPECT, 1),
        RetryAuthorization(
            run.id,
            assignment.id,
            assignment.id,
            attempt.id,
            Attempt(AttemptId.new(), run.id),
            terminal,
            (artifact,),
            (artifact,),
        ),
    )
    for record in records:
        assert "treatment" not in repr(record).lower()
    with pytest.raises(FrozenInstanceError):
        run.id = RunId.new()  # type: ignore[misc]


def test_attempt_terminal_uses_only_the_frozen_vocabulary() -> None:
    terminal = AttemptTerminal(
        AttemptId.new(),
        RunId.new(),
        AttemptTerminalKind.SUCCEEDED,
        datetime.now(UTC),
        "completed",
    )
    assert terminal.classification is AttemptTerminalKind.SUCCEEDED
    with pytest.raises(InvalidAttemptTerminalError):
        AttemptTerminal(
            AttemptId.new(),
            RunId.new(),
            "invented",  # type: ignore[arg-type]
            datetime.now(UTC),
            "invalid",
        )
