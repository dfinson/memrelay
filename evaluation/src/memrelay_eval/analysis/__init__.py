"""Frozen confirmatory-analysis contracts."""

from .diagnostics import DiagnosticReport, build_diagnostics
from .estimands import FrozenEstimand, FrozenEstimatorRegistry, MissingnessPolicy
from .estimators import (
    AssignmentAnalysisLock,
    AssignmentDisclosure,
    CmhSensitivity,
    EstimatorDecisionRecord,
    GeeSensitivity,
    GlmmSensitivity,
    IttTable,
    IttTableBuilder,
    cluster_sensitivity,
    cmh_sensitivity,
    estimate_itt,
    estimate_itt_bounds,
    estimator_decision,
    gee_sensitivity,
    glmm_sensitivity,
)
from .gates import ClaimGateDecision, FrozenThresholdPolicy, evaluate_claim, release_fitness
from .intervals import SimultaneousInterval, holm_simultaneous_intervals
from .multiplicity import EndpointPValue, FrozenClaimFamily, HolmResult, holm_fwer
from .power import (
    FinalInformationProof,
    FrozenPowerProtocol,
    FrozenSimulationCell,
    PowerEvaluation,
    SimulationCellResult,
    evaluate_power,
    fixed_information_look,
)
from .preregistration import SealedClaimProtocol, SealedClaimRegistration

__all__ = [
    "AssignmentDisclosure",
    "AssignmentAnalysisLock",
    "CmhSensitivity",
    "ClaimGateDecision",
    "DiagnosticReport",
    "EstimatorDecisionRecord",
    "FinalInformationProof",
    "FrozenEstimand",
    "FrozenEstimatorRegistry",
    "FrozenClaimFamily",
    "FrozenPowerProtocol",
    "FrozenSimulationCell",
    "FrozenThresholdPolicy",
    "GeeSensitivity",
    "GlmmSensitivity",
    "IttTable",
    "IttTableBuilder",
    "EndpointPValue",
    "HolmResult",
    "MissingnessPolicy",
    "PowerEvaluation",
    "SimulationCellResult",
    "SealedClaimProtocol",
    "SealedClaimRegistration",
    "SimultaneousInterval",
    "build_diagnostics",
    "cluster_sensitivity",
    "cmh_sensitivity",
    "estimator_decision",
    "gee_sensitivity",
    "glmm_sensitivity",
    "estimate_itt",
    "estimate_itt_bounds",
    "evaluate_claim",
    "evaluate_power",
    "fixed_information_look",
    "holm_fwer",
    "holm_simultaneous_intervals",
    "release_fitness",
]
