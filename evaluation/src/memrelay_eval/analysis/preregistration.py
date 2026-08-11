"""Canonical pre-enrollment authorization for confirmatory claim artifacts."""

from __future__ import annotations

from dataclasses import dataclass

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.errors import AnalysisError


def _valid_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value.isascii()
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True)
class SealedClaimRegistration:
    """The non-cyclic preregistration digests for one complete claim family."""

    family_id: str
    family_registration_sha256: str
    threshold_registration_sha256: str
    power_registration_sha256: str

    def __post_init__(self) -> None:
        if not self.family_id or not all(
            _valid_sha256(value)
            for value in (
                self.family_registration_sha256,
                self.threshold_registration_sha256,
                self.power_registration_sha256,
            )
        ):
            raise AnalysisError("sealed_claim_registration_invalid")

    def to_document(self) -> dict[str, object]:
        return {
            "family_id": self.family_id,
            "family_registration_sha256": self.family_registration_sha256,
            "threshold_registration_sha256": self.threshold_registration_sha256,
            "power_registration_sha256": self.power_registration_sha256,
        }


@dataclass(frozen=True, slots=True)
class SealedClaimProtocol:
    """An immutable, independently authorized registry of all claim-policy choices.

    Registration digests deliberately exclude this artifact's digest.  This lets the
    artifact authenticate the exact family, thresholds, and simulation grid without a
    self-referential hash.  ``pre_enrollment_authorization_sha256`` is supplied by the
    separately sealed assignment/pre-enrollment workflow; it is never derived here.
    """

    protocol_sha256: str
    assignment_plan_sha256: str
    estimator_registry_sha256: str
    pre_enrollment_authorization_sha256: str
    pre_enrollment_state: str
    registrations: tuple[SealedClaimRegistration, ...]

    def __post_init__(self) -> None:
        if (
            not all(
                _valid_sha256(value)
                for value in (
                    self.protocol_sha256,
                    self.assignment_plan_sha256,
                    self.estimator_registry_sha256,
                    self.pre_enrollment_authorization_sha256,
                )
            )
            or self.pre_enrollment_state != "authorized_before_enrollment"
        ):
            raise AnalysisError("sealed_claim_protocol_invalid")
        registrations = tuple(sorted(self.registrations, key=lambda item: item.family_id))
        if not registrations or len({item.family_id for item in registrations}) != len(
            registrations
        ):
            raise AnalysisError("sealed_claim_registration_invalid")
        object.__setattr__(self, "registrations", registrations)

    @property
    def sealed_claim_protocol_sha256(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "sealed_claim_protocol",
            "protocol_sha256": self.protocol_sha256,
            "assignment_plan_sha256": self.assignment_plan_sha256,
            "estimator_registry_sha256": self.estimator_registry_sha256,
            "pre_enrollment_authorization_sha256": self.pre_enrollment_authorization_sha256,
            "pre_enrollment_state": self.pre_enrollment_state,
            "registrations": [item.to_document() for item in self.registrations],
        }

    def require_family(
        self,
        *,
        family_id: str,
        protocol_sha256: str,
        assignment_plan_sha256: str,
        estimator_registry_sha256: str,
        family_registration_sha256: str,
        sealed_claim_protocol_sha256: str,
    ) -> None:
        registration = self._registration(family_id)
        if (
            protocol_sha256 != self.protocol_sha256
            or assignment_plan_sha256 != self.assignment_plan_sha256
            or estimator_registry_sha256 != self.estimator_registry_sha256
            or family_registration_sha256 != registration.family_registration_sha256
            or sealed_claim_protocol_sha256 != self.sealed_claim_protocol_sha256
        ):
            raise AnalysisError("sealed_claim_family_unregistered")

    def require_threshold(
        self,
        *,
        family_id: str,
        family_registration_sha256: str,
        threshold_registration_sha256: str,
        sealed_claim_protocol_sha256: str,
    ) -> None:
        registration = self._registration(family_id)
        if (
            family_registration_sha256 != registration.family_registration_sha256
            or threshold_registration_sha256 != registration.threshold_registration_sha256
            or sealed_claim_protocol_sha256 != self.sealed_claim_protocol_sha256
        ):
            raise AnalysisError("sealed_claim_threshold_unregistered")

    def require_power(
        self,
        *,
        family_id: str,
        family_registration_sha256: str,
        power_registration_sha256: str,
        sealed_claim_protocol_sha256: str,
    ) -> None:
        registration = self._registration(family_id)
        if (
            family_registration_sha256 != registration.family_registration_sha256
            or power_registration_sha256 != registration.power_registration_sha256
            or sealed_claim_protocol_sha256 != self.sealed_claim_protocol_sha256
        ):
            raise AnalysisError("sealed_claim_power_unregistered")

    def _registration(self, family_id: str) -> SealedClaimRegistration:
        for registration in self.registrations:
            if registration.family_id == family_id:
                return registration
        raise AnalysisError("sealed_claim_family_unregistered")
