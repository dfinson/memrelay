"""Deterministic, treatment-blind evidence views and local leakage conformance."""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Literal

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactManifest, ArtifactRef
from memrelay_eval.domain.errors import BlindingConformanceError
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.evidence.required import require_unpaid_conformance_ports

BLINDING_POLICY_VERSION = "1.0.0"
BLINDED_VIEW_SCHEMA_VERSION = "1.0.0"
LEAKAGE_PROTOCOL_VERSION = "1.0.0"
LEAKAGE_AUC_UPPER_BOUND = 0.60
_CLASSIFIER_ALGORITHM = "token-presence-naive-bayes-v1"
_CONFIDENCE_METHOD = "hanley-mcneil-normal-95-v1"
_ALLOWED_EVIDENCE_FIELDS = frozenset(
    {"requirements", "code", "patch", "tests", "artifact_locations", "evidence"}
)
_DENIED_FIELD_TOKENS = frozenset({"arm", "treatment", "assignment"})
_PROVIDER_FIELD_TOKENS = frozenset({"provider", "credential", "model"})
_TOOL_FIELD_TOKENS = frozenset({"tool", "command"})
_TIMING_FIELD_TOKENS = frozenset(
    {"time", "timestamp", "duration", "latency", "timing", "started", "finished"}
)
_ORDER_FIELD_TOKENS = frozenset({"order", "sequence", "position", "rank"})
_PATH_PATTERN = re.compile(
    r"(?i)(?:[a-z]:)?[\\/][^\s'\"`]*memrelay[^\s'\"`]*|"
    r"(?:[a-z]:)?[\\/][^\s'\"`]*(?:copilot-worktrees|memrelay)[^\s'\"`]*"
)
_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")
_FIELD_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class BlindingPolicy:
    """Versioned allow, deny, and transform rules pinned before outcome access."""

    version: str = BLINDING_POLICY_VERSION
    treatment_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.version != BLINDING_POLICY_VERSION:
            raise BlindingConformanceError("unsupported_blinding_policy_version")
        aliases = tuple(sorted({alias.casefold() for alias in self.treatment_aliases if alias}))
        if len(aliases) != len(self.treatment_aliases):
            raise BlindingConformanceError("invalid_blinding_policy_aliases")
        object.__setattr__(self, "treatment_aliases", aliases)

    def document(self) -> dict[str, object]:
        return {
            "version": self.version,
            "allow_fields": sorted(_ALLOWED_EVIDENCE_FIELDS),
            "deny_field_tokens": sorted(_DENIED_FIELD_TOKENS),
            "provider_field_tokens": sorted(_PROVIDER_FIELD_TOKENS),
            "tool_field_tokens": sorted(_TOOL_FIELD_TOKENS),
            "timing_field_tokens": sorted(_TIMING_FIELD_TOKENS),
            "order_field_tokens": sorted(_ORDER_FIELD_TOKENS),
            "path_transform": "sha256-artifact-location-v1",
            "treatment_aliases": list(self.treatment_aliases),
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.document())

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class BlindedEvidenceView:
    """Canonical derived evidence with immutable source and transform provenance."""

    source_artifact: ArtifactRef
    policy_artifact: ArtifactRef
    view_artifact: ArtifactRef
    source_sha256: str
    policy_sha256: str
    transform_sha256: str
    bytes: bytes

    @property
    def sha256(self) -> str:
        return self.view_artifact.sha256


@dataclass(frozen=True, slots=True)
class LeakageCandidate:
    """Escrowed binary arm label paired with a deterministic blinded candidate."""

    candidate_id: str
    view_bytes: bytes
    arm: Literal[0, 1]


@dataclass(frozen=True, slots=True)
class FrozenLeakageProtocol:
    """Pre-outcome lock for corpus, feature projection, split, and confidence method."""

    seed: int
    sentinel_corpus_sha256: str
    training_ids: tuple[str, ...]
    evaluation_ids: tuple[str, ...]
    version: str = LEAKAGE_PROTOCOL_VERSION
    classifier_algorithm: str = _CLASSIFIER_ALGORITHM
    confidence_method: str = _CONFIDENCE_METHOD

    def __post_init__(self) -> None:
        if (
            self.version != LEAKAGE_PROTOCOL_VERSION
            or self.classifier_algorithm != _CLASSIFIER_ALGORITHM
            or self.confidence_method != _CONFIDENCE_METHOD
        ):
            raise BlindingConformanceError("unsupported_leakage_protocol")
        if not self.training_ids or not self.evaluation_ids:
            raise BlindingConformanceError("leakage_protocol_requires_nonempty_split")
        if (
            len(set(self.training_ids)) != len(self.training_ids)
            or len(set(self.evaluation_ids)) != len(self.evaluation_ids)
            or set(self.training_ids).intersection(self.evaluation_ids)
        ):
            raise BlindingConformanceError("invalid_leakage_protocol_split")
        if not re.fullmatch(r"[a-f0-9]{64}", self.sentinel_corpus_sha256):
            raise BlindingConformanceError("invalid_sentinel_corpus_hash")

    def document(self) -> dict[str, object]:
        return {
            "version": self.version,
            "seed": self.seed,
            "sentinel_corpus_sha256": self.sentinel_corpus_sha256,
            "feature_projection": "lowercase-ascii-token-presence-v1",
            "training_ids": list(self.training_ids),
            "evaluation_ids": list(self.evaluation_ids),
            "classifier_algorithm": self.classifier_algorithm,
            "confidence_method": self.confidence_method,
            "upper_auc_threshold": LEAKAGE_AUC_UPPER_BOUND,
        }

    @property
    def sha256(self) -> str:
        return sha256(canonical_bytes(self.document())).hexdigest()


@dataclass(frozen=True, slots=True)
class LeakageConformance:
    """Polarity-symmetric leakage gate result retained with its protocol hash."""

    protocol_sha256: str
    auc: float
    upper_auc_95: float
    direct_leak_categories: tuple[str, ...]

    @property
    def passes(self) -> bool:
        return not self.direct_leak_categories and self.upper_auc_95 <= LEAKAGE_AUC_UPPER_BOUND


def generate_blinded_view(
    store: ArtifactStorePort, source_artifact: ArtifactRef, policy: BlindingPolicy
) -> BlindedEvidenceView:
    """Build and persist a canonical view without modifying its source artifact."""
    require_unpaid_conformance_ports(store)
    source_bytes = store.open_verified(source_artifact)
    source = _parse_source_document(source_bytes)
    policy_artifact = store.put_bytes(
        policy.canonical_bytes, media_type="application/json", classification="unpaid_conformance"
    )
    transformed = _transform_source(source, policy)
    document = {
        "schema_version": BLINDED_VIEW_SCHEMA_VERSION,
        "source": {
            "artifact_id": str(source_artifact.artifact_id),
            "sha256": source_artifact.sha256,
        },
        "policy_sha256": policy.sha256,
        "transform_sha256": _transform_sha256(policy),
        "evidence": transformed["evidence"],
        "artifact_locations": transformed["artifact_locations"],
    }
    view_bytes = canonical_bytes(document)
    direct_leaks = detect_direct_leaks(document, policy)
    if direct_leaks:
        raise BlindingConformanceError("direct_blinding_leak", direct_leaks)
    view_artifact = store.put_bytes(
        view_bytes, media_type="application/json", classification="unpaid_conformance"
    )
    return BlindedEvidenceView(
        source_artifact=source_artifact,
        policy_artifact=policy_artifact,
        view_artifact=view_artifact,
        source_sha256=source_artifact.sha256,
        policy_sha256=policy.sha256,
        transform_sha256=document["transform_sha256"],
        bytes=view_bytes,
    )


def write_blinded_view_manifest(
    store: ArtifactStorePort, view: BlindedEvidenceView, manifest: ArtifactManifest
) -> ArtifactRef:
    """Write the inherited 1.0.0 manifest only when it matches derived view bytes."""
    require_unpaid_conformance_ports(store)
    if (
        manifest.kind != "blinded_evidence_view"
        or manifest.sha256 != view.sha256
        or manifest.artifact_id != view.view_artifact.artifact_id
        or manifest.size_bytes != view.view_artifact.size_bytes
        or set(manifest.source_artifact_ids)
        != {view.source_artifact.artifact_id, view.policy_artifact.artifact_id}
    ):
        raise BlindingConformanceError("blinded_view_manifest_provenance_mismatch")
    return store.write_manifest(manifest)


def detect_direct_leaks(document: object, policy: BlindingPolicy) -> tuple[str, ...]:
    """Report leak classes without persisting or echoing leaking values."""
    categories: set[str] = set()
    _collect_direct_leaks(document, policy, categories)
    return tuple(sorted(categories))


def generate_sentinel_corpus(seed: int, count: int) -> tuple[dict[str, object], ...]:
    """Generate a reproducible, value-free sentinel corpus for transform conformance."""
    if count <= 0:
        raise BlindingConformanceError("sentinel_count_must_be_positive")
    generator = random.Random(seed)
    return tuple(
        {
            "candidate_id": f"sentinel-{index:04d}",
            "requirements": f"Requirement {generator.randrange(1_000_000)}",
            "assignment_record": {"treatment_code": f"escrow-{generator.randrange(1_000_000)}"},
            "tool_timing": {"tool_name": "hidden", "duration_ms": generator.randrange(1_000_000)},
            "artifact_locations": {
                "patch": f"C:\\memrelay\\attempts\\{generator.randrange(1_000_000)}\\patch.diff"
            },
        }
        for index in range(count)
    )


def sentinel_corpus_sha256(corpus: Sequence[Mapping[str, object]]) -> str:
    """Hash the exact frozen sentinel corpus using evaluator canonical bytes."""
    return sha256(canonical_bytes(list(corpus))).hexdigest()


def evaluate_leakage_classifier(
    candidates: Sequence[LeakageCandidate], protocol: FrozenLeakageProtocol
) -> LeakageConformance:
    """Fit and evaluate only the frozen local token classifier and split."""
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    expected_ids = set(protocol.training_ids).union(protocol.evaluation_ids)
    if len(by_id) != len(candidates) or set(by_id) != expected_ids:
        raise BlindingConformanceError("leakage_candidates_do_not_match_frozen_split")
    training = [by_id[candidate_id] for candidate_id in protocol.training_ids]
    evaluation = [by_id[candidate_id] for candidate_id in protocol.evaluation_ids]
    positive, negative = _fit_token_counts(training)
    scored = tuple(
        (_classifier_score(candidate.view_bytes, positive, negative), candidate.arm)
        for candidate in evaluation
    )
    auc, positive_count, negative_count = _polarity_symmetric_auc(scored)
    upper = _auc_upper_bound_95(auc, positive_count, negative_count)
    return LeakageConformance(protocol.sha256, auc, upper, ())


def require_blinding_conformance(
    direct_leak_categories: Sequence[str], classifier: LeakageConformance
) -> None:
    """Fail scoring conformance on either direct leakage or the frozen AUC gate."""
    categories = tuple(sorted(set(direct_leak_categories).union(classifier.direct_leak_categories)))
    if categories:
        raise BlindingConformanceError("direct_blinding_leak", categories)
    if classifier.upper_auc_95 > LEAKAGE_AUC_UPPER_BOUND:
        raise BlindingConformanceError("blinding_classifier_auc_upper_bound")


def _parse_source_document(data: bytes) -> Mapping[str, object]:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BlindingConformanceError("blinding_source_must_be_json") from error
    if not isinstance(value, dict):
        raise BlindingConformanceError("blinding_source_must_be_object")
    return MappingProxyType(value)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BlindingConformanceError("blinding_source_duplicate_key")
        result[key] = value
    return result


def _transform_source(source: Mapping[str, object], policy: BlindingPolicy) -> dict[str, object]:
    evidence: dict[str, object] = {}
    for key in sorted(_ALLOWED_EVIDENCE_FIELDS.difference({"artifact_locations"})):
        if key in source:
            evidence[key] = _transform_value(source[key], policy)
    locations = source.get("artifact_locations", {})
    if not isinstance(locations, Mapping):
        raise BlindingConformanceError("artifact_locations_must_be_object")
    return {
        "evidence": evidence,
        "artifact_locations": {
            key: _blinded_location(value)
            for key, value in sorted(locations.items())
            if isinstance(key, str) and isinstance(value, str)
        },
    }


def _transform_value(value: object, policy: BlindingPolicy) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, nested in sorted(value.items()):
            if not isinstance(key, str) or _field_category(key) is not None:
                continue
            result[key] = _transform_value(nested, policy)
        return result
    if isinstance(value, (list, tuple)):
        return sorted((_transform_value(item, policy) for item in value), key=canonical_bytes)
    if isinstance(value, str):
        return _transform_text(value, policy)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise BlindingConformanceError("unsupported_blinding_source_value")


def _transform_text(value: str, policy: BlindingPolicy) -> str:
    transformed = _PATH_PATTERN.sub(lambda match: _blinded_location(match.group(0)), value)
    for alias in policy.treatment_aliases:
        transformed = re.sub(re.escape(alias), "[BLINDED]", transformed, flags=re.IGNORECASE)
    return transformed


def _blinded_location(value: str) -> str:
    return f"artifact://blinded/{sha256(value.encode('utf-8')).hexdigest()}"


def _field_category(key: str) -> str | None:
    tokens = set(_FIELD_TOKEN_PATTERN.findall(key.casefold()))
    if tokens.intersection(_DENIED_FIELD_TOKENS):
        return "assignment"
    if tokens.intersection(_PROVIDER_FIELD_TOKENS):
        return "provider"
    if tokens.intersection(_TOOL_FIELD_TOKENS):
        return "tool"
    if tokens.intersection(_TIMING_FIELD_TOKENS):
        return "timing"
    if tokens.intersection(_ORDER_FIELD_TOKENS):
        return "ordering"
    if "path" in tokens:
        return "path"
    return None


def _collect_direct_leaks(value: object, policy: BlindingPolicy, categories: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str):
                category = _field_category(key)
                if category is not None and key not in {"artifact_locations"}:
                    categories.add(category)
            _collect_direct_leaks(nested, policy, categories)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _collect_direct_leaks(nested, policy, categories)
    elif isinstance(value, str):
        if _PATH_PATTERN.search(value):
            categories.add("path")
        if any(alias in value.casefold() for alias in policy.treatment_aliases):
            categories.add("assignment")


def _transform_sha256(policy: BlindingPolicy) -> str:
    return sha256(
        canonical_bytes(
            {
                "policy_sha256": policy.sha256,
                "transform_version": "recursive-default-deny-v1",
                "location_transform": "sha256-artifact-location-v1",
            }
        )
    ).hexdigest()


def _fit_token_counts(candidates: Sequence[LeakageCandidate]) -> tuple[Counter[str], Counter[str]]:
    positive: Counter[str] = Counter()
    negative: Counter[str] = Counter()
    for candidate in candidates:
        tokens = set(_tokens(candidate.view_bytes))
        (positive if candidate.arm else negative).update(tokens)
    if not positive or not negative:
        raise BlindingConformanceError("leakage_training_requires_both_arms")
    return positive, negative


def _classifier_score(data: bytes, positive: Counter[str], negative: Counter[str]) -> float:
    score = 0.0
    for token in set(_tokens(data)):
        score += math.log((positive[token] + 1) / (negative[token] + 1))
    return score


def _tokens(data: bytes) -> tuple[str, ...]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BlindingConformanceError("leakage_candidate_must_be_utf8") from error
    return tuple(_TOKEN_PATTERN.findall(text.casefold()))


def _auc(scored: Sequence[tuple[float, Literal[0, 1]]]) -> float:
    positives = [score for score, arm in scored if arm == 1]
    negatives = [score for score, arm in scored if arm == 0]
    if not positives or not negatives:
        raise BlindingConformanceError("leakage_evaluation_requires_both_arms")
    wins = sum(
        1.0 if positive > negative else 0.5 if positive == negative else 0.0
        for positive in positives
        for negative in negatives
    )
    return wins / (len(positives) * len(negatives))


def _polarity_symmetric_auc(
    scored: Sequence[tuple[float, Literal[0, 1]]],
) -> tuple[float, int, int]:
    """Choose the stronger label direction before applying the fixed AUC gate."""
    directional_auc = _auc(scored)
    positive_count = sum(arm == 1 for _, arm in scored)
    negative_count = len(scored) - positive_count
    if directional_auc >= 0.5:
        return directional_auc, positive_count, negative_count
    return 1 - directional_auc, negative_count, positive_count


def _auc_upper_bound_95(auc: float, positive_count: int, negative_count: int) -> float:
    """Compute the upper bound for the orientation selected by the leakage statistic."""
    q1 = auc / (2 - auc)
    q2 = (2 * auc * auc) / (1 + auc)
    variance = (
        auc * (1 - auc)
        + (positive_count - 1) * (q1 - auc * auc)
        + (negative_count - 1) * (q2 - auc * auc)
    ) / (positive_count * negative_count)
    return min(1.0, auc + 1.96 * math.sqrt(max(0.0, variance)))
