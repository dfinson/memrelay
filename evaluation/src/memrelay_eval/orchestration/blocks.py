"""Environment-bound assignment block records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from memrelay_eval.canonical import attach_digest
from memrelay_eval.domain.entities import EnvironmentStratum
from memrelay_eval.domain.environment import EnvironmentFingerprint
from memrelay_eval.domain.errors import EnvironmentStratumChangedError, InvalidConfigurationError
from memrelay_eval.domain.policies import require_no_secret_values, require_treatment_neutral

BLOCKS_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class EnvironmentBlocks:
    """Ordered blocks that explicitly belong to one fingerprint stratum."""

    environment_stratum: EnvironmentStratum
    blocks: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        if not self.blocks:
            raise InvalidConfigurationError()
        normalized: list[Mapping[str, object]] = []
        for block in self.blocks:
            if not isinstance(block, Mapping) or not block:
                raise InvalidConfigurationError()
            require_no_secret_values(block)
            require_treatment_neutral(block)
            normalized.append(MappingProxyType(dict(block)))
        object.__setattr__(self, "blocks", tuple(normalized))

    def to_document(self) -> dict[str, object]:
        return attach_digest(
            {
                "artifact_type": "environment_blocks",
                "schema_version": BLOCKS_SCHEMA_VERSION,
                "environment_stratum_id": str(self.environment_stratum.id),
                "environment_fingerprint_digest": self.environment_stratum.fingerprint_digest,
                "blocks": [dict(block) for block in self.blocks],
            }
        )


def build_environment_blocks(
    fingerprint: EnvironmentFingerprint, blocks: Sequence[Mapping[str, object]]
) -> EnvironmentBlocks:
    """Bind ordered assignment blocks to the exact pre-assignment environment."""
    if isinstance(blocks, (str, bytes, bytearray)):
        raise InvalidConfigurationError()
    return EnvironmentBlocks(fingerprint.stratum, tuple(blocks))


def require_same_environment_stratum(
    blocks: EnvironmentBlocks, fingerprint: EnvironmentFingerprint
) -> None:
    """Fail closed rather than pooling a changed host with its original block."""
    if blocks.environment_stratum != fingerprint.stratum:
        raise EnvironmentStratumChangedError(EnvironmentStratumChangedError.code)
