"""Filesystem-backed, append-only SHA-256 content-addressed evidence store."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from memrelay_eval.domain.entities import ArtifactManifest, ArtifactRef
from memrelay_eval.domain.errors import (
    ArtifactAuthorityConflictError,
    ArtifactIntegrityError,
    ArtifactRetentionError,
    InvalidArtifactManifestError,
)
from memrelay_eval.evidence.manifest import canonical_json_bytes, manifest_bytes, parse_manifest


@dataclass(frozen=True, slots=True)
class ReachabilityIndex:
    """Deterministic rebuild output; indexes are derived convenience state."""

    experiments: dict[str, tuple[str, ...]]
    runs: dict[str, tuple[str, ...]]
    attempts: dict[str, tuple[str, ...]]
    evidence: dict[str, tuple[str, ...]]
    orphaned_blobs: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "attempts": {key: list(value) for key, value in self.attempts.items()},
            "evidence": {key: list(value) for key, value in self.evidence.items()},
            "experiments": {key: list(value) for key, value in self.experiments.items()},
            "orphaned_blobs": list(self.orphaned_blobs),
            "runs": {key: list(value) for key, value in self.runs.items()},
        }


@dataclass(frozen=True, slots=True)
class DurableAdapterQualification:
    """Credential-free proof that this is durable, not fake unpaid evidence."""

    qualified: bool
    provenance: str
    verified_manifest_count: int
    reachability_sha256: str


class FilesystemArtifactStore:
    """Atomic, idempotent filesystem CAS with fail-closed authority verification."""

    provenance = "durable_filesystem_cas"
    eligible_for_paid_or_study = True

    def __init__(
        self,
        root: Path | str,
        *,
        publication_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.root = Path(root)
        self._publication_hook = publication_hook
        self._blobs = self.root / "blobs" / "sha256"
        self._manifests = self.root / "manifests" / "sha256"
        self._indexes = self.root / "indexes"
        self._corruption = self.root / "corruption"
        self._derivations = self.root / "derivations"
        for directory in (
            self._blobs,
            self._manifests,
            self._indexes,
            self._corruption,
            self._derivations,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, data: bytes, *, media_type: str, classification: str) -> ArtifactRef:
        """Atomically publish exact bytes, or verify an existing immutable object."""
        del media_type, classification
        reference = ArtifactRef.from_bytes(bytes(data))
        path = self._blob_path(reference.sha256)
        self._publish_immutable(path, data, reference.sha256, "blob")
        self._verify_path(path, reference)
        return reference

    def open_verified(self, artifact: ArtifactRef) -> bytes:
        """Read artifact bytes only after digest and size verification."""
        path = self._blob_path(artifact.sha256)
        if not path.is_file():
            self._preserve_corruption("missing_blob", artifact.sha256)
            raise ArtifactIntegrityError("artifact is missing")
        data = path.read_bytes()
        actual = ArtifactRef.from_bytes(data)
        if actual != artifact:
            self._preserve_corruption(
                "blob_integrity_failure",
                artifact.sha256,
                actual_sha256=actual.sha256,
            )
            raise ArtifactIntegrityError("artifact bytes do not match their content address")
        return data

    def write_manifest(self, manifest: ArtifactManifest) -> ArtifactRef:
        """Publish one canonical authority manifest after verifying artifact lineage."""
        artifact = ArtifactRef(manifest.artifact_id, manifest.sha256, manifest.size_bytes)
        self.open_verified(artifact)
        source_digests = self._resolve_source_digests(manifest)
        payload = manifest_bytes(manifest)
        manifest_ref = ArtifactRef.from_bytes(payload)
        path = self._manifest_path(manifest.sha256)
        if path.exists():
            existing = self._read_authoritative_manifest(path)
            if manifest_bytes(existing) != payload:
                self._preserve_corruption(
                    "manifest_authority_conflict",
                    manifest.sha256,
                    actual_sha256=manifest_ref.sha256,
                )
                raise ArtifactAuthorityConflictError(
                    "an incompatible manifest already governs this artifact"
                )
            self.put_bytes(
                payload,
                media_type="application/json",
                classification=manifest.classification,
            )
        else:
            self._publish_immutable(path, payload, manifest_ref.sha256, "manifest")
            existing = self._read_authoritative_manifest(path)
            if manifest_bytes(existing) != payload:
                self._preserve_corruption(
                    "manifest_authority_conflict",
                    manifest.sha256,
                    actual_sha256=manifest_ref.sha256,
                )
                raise ArtifactAuthorityConflictError(
                    "concurrent publication produced incompatible manifest authority"
                )
            self.put_bytes(
                payload,
                media_type="application/json",
                classification=manifest.classification,
            )
        self._publish_derivation_identity(manifest, manifest_ref.sha256, source_digests)
        return manifest_ref

    def read_manifest(self, artifact: ArtifactRef) -> ArtifactManifest:
        """Return an artifact's sole verified authority manifest."""
        path = self._manifest_path(artifact.sha256)
        manifest = self._read_authoritative_manifest(path)
        if manifest.sha256 != artifact.sha256 or manifest.size_bytes != artifact.size_bytes:
            self._preserve_corruption("manifest_blob_mismatch", artifact.sha256)
            raise ArtifactIntegrityError("manifest authority does not match artifact reference")
        self.open_verified(ArtifactRef.from_bytes(manifest_bytes(manifest)))
        self.open_verified(artifact)
        return manifest

    def copy_verified(self, artifact: ArtifactRef, destination: Path | str) -> Path:
        """Copy verified bytes atomically and verify destination bytes after publication."""
        data = self.open_verified(artifact)
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        self._publish_immutable(destination_path, data, artifact.sha256, "copy")
        self._verify_path(destination_path, artifact)
        return destination_path

    def rebuild_reachability(self) -> ReachabilityIndex:
        """Rebuild deterministic index state from verified manifest authorities only."""
        manifests = self._verified_manifests()
        by_id = {str(manifest.artifact_id): manifest for manifest in manifests}
        if len(by_id) != len(manifests):
            self._preserve_corruption("duplicate_manifest_authority", None)
            raise ArtifactAuthorityConflictError("duplicate authoritative artifact manifest")

        dependencies: dict[str, tuple[str, ...]] = {}
        for manifest in manifests:
            source_digests: list[str] = []
            for source_id in manifest.source_artifact_ids:
                source = by_id.get(str(source_id))
                if source is None:
                    self._preserve_corruption("missing_source_manifest", manifest.sha256)
                    raise ArtifactIntegrityError("derived artifact source is not authoritative")
                self.open_verified(ArtifactRef(source.artifact_id, source.sha256, source.size_bytes))
                source_digests.append(source.sha256)
            dependencies[manifest.sha256] = tuple(sorted(source_digests))
        self._reject_cycles(dependencies)

        experiments: dict[str, list[str]] = {}
        runs: dict[str, list[str]] = {}
        attempts: dict[str, list[str]] = {}
        evidence = {manifest.sha256: dependencies[manifest.sha256] for manifest in manifests}
        for manifest in manifests:
            if manifest.experiment_id is not None:
                experiments.setdefault(str(manifest.experiment_id), []).append(manifest.sha256)
            if manifest.run_id is not None:
                runs.setdefault(str(manifest.run_id), []).append(manifest.sha256)
            if manifest.attempt_id is not None:
                attempts.setdefault(str(manifest.attempt_id), []).append(manifest.sha256)
        index = ReachabilityIndex(
            experiments=_freeze_edges(experiments),
            runs=_freeze_edges(runs),
            attempts=_freeze_edges(attempts),
            evidence={key: tuple(value) for key, value in sorted(evidence.items())},
            orphaned_blobs=self._orphaned_blobs(manifests),
        )
        self._publish_derived(
            self._indexes / "reachability.json",
            canonical_json_bytes(index.to_dict()),
            "reachability_index",
        )
        return index

    def qualify(self) -> DurableAdapterQualification:
        """Emit a durable-adapter qualification after rebuild and integrity verification."""
        index = self.rebuild_reachability()
        payload = canonical_json_bytes(index.to_dict())
        return DurableAdapterQualification(
            qualified=not index.orphaned_blobs,
            provenance=self.provenance,
            verified_manifest_count=len(self._verified_manifests()),
            reachability_sha256=ArtifactRef.from_bytes(payload).sha256,
        )

    def delete(self, artifact: ArtifactRef) -> None:
        """Refuse deletion until a future formal claim-retirement authority exists."""
        del artifact
        raise ArtifactRetentionError(
            "artifacts are retained until every linked claim is formally retired"
        )

    def _verified_manifests(
        self, *, reject_orphans: bool = True
    ) -> tuple[ArtifactManifest, ...]:
        manifests: list[ArtifactManifest] = []
        for path in sorted(self._manifests.glob("*/*.json")):
            manifest = self._read_authoritative_manifest(path)
            if path != self._manifest_path(manifest.sha256):
                self._preserve_corruption("malformed_manifest_path", manifest.sha256)
                raise ArtifactIntegrityError("manifest path is not a convenience index for its digest")
            self.open_verified(ArtifactRef(manifest.artifact_id, manifest.sha256, manifest.size_bytes))
            self.open_verified(ArtifactRef.from_bytes(manifest_bytes(manifest)))
            manifests.append(manifest)
        if reject_orphans:
            self._orphaned_blobs(manifests)
        return tuple(manifests)

    def _orphaned_blobs(self, manifests: list[ArtifactManifest]) -> tuple[str, ...]:
        expected_blob_digests = {manifest.sha256 for manifest in manifests} | {
            ArtifactRef.from_bytes(manifest_bytes(manifest)).sha256 for manifest in manifests
        }
        orphans: list[str] = []
        for path in sorted(self._blobs.glob("*/*")):
            if not path.is_file() or (
                path.name.startswith(".") and path.name.endswith(".staging")
            ):
                continue
            digest = f"{path.parent.name}{path.name}"
            if digest not in expected_blob_digests:
                self._preserve_corruption("orphan_blob", digest)
                orphans.append(digest)
        return tuple(orphans)

    def _resolve_source_digests(self, manifest: ArtifactManifest) -> tuple[str, ...]:
        if not manifest.source_artifact_ids:
            return ()
        by_id = {
            str(candidate.artifact_id): candidate
            for candidate in self._verified_manifests(reject_orphans=False)
        }
        source_digests: list[str] = []
        for source_id in manifest.source_artifact_ids:
            source = by_id.get(str(source_id))
            if source is None:
                self._preserve_corruption("missing_source_manifest", manifest.sha256)
                raise ArtifactIntegrityError("derived artifact source does not resolve")
            self.open_verified(ArtifactRef(source.artifact_id, source.sha256, source.size_bytes))
            source_digests.append(source.sha256)
        return tuple(sorted(source_digests))

    def _publish_derivation_identity(
        self,
        manifest: ArtifactManifest,
        manifest_sha256: str,
        source_digests: tuple[str, ...],
    ) -> None:
        payload = canonical_json_bytes(
            {
                "artifact_sha256": manifest.sha256,
                "manifest_sha256": manifest_sha256,
                "schema_version": manifest.schema_version,
                "source_sha256": list(source_digests),
            }
        )
        self._publish_immutable(
            self._derivations / f"{manifest.sha256}.json",
            payload,
            None,
            "derivation_identity",
        )

    def _read_authoritative_manifest(self, path: Path) -> ArtifactManifest:
        if not path.is_file():
            self._preserve_corruption("missing_manifest", None)
            raise ArtifactIntegrityError("artifact has no authoritative manifest")
        try:
            manifest = parse_manifest(path.read_bytes())
        except (InvalidArtifactManifestError, ValueError) as exc:
            self._preserve_corruption("malformed_manifest", None)
            raise ArtifactIntegrityError("manifest is malformed or non-canonical") from exc
        return manifest

    def _publish_immutable(
        self,
        destination: Path,
        data: bytes,
        expected_sha256: str | None,
        stage: str,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._verify_immutable_destination(destination, data, expected_sha256, stage)
            return
        staging = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.staging")
        try:
            self._write_staging(staging, data)
            self._notify(f"{stage}_staged")
            os.link(staging, destination)
        except FileExistsError:
            self._verify_immutable_destination(destination, data, expected_sha256, stage)
        else:
            self._fsync_directory(destination.parent)
            self._notify(f"{stage}_published")
        finally:
            if staging.exists():
                staging.unlink()

    def _publish_derived(self, destination: Path, data: bytes, stage: str) -> None:
        """Atomically replace derived convenience state, never immutable authority."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.staging")
        try:
            self._write_staging(staging, data)
            self._notify(f"{stage}_staged")
            os.replace(staging, destination)
            self._fsync_directory(destination.parent)
            self._notify(f"{stage}_published")
        finally:
            if staging.exists():
                staging.unlink()

    @staticmethod
    def _write_staging(staging: Path, data: bytes) -> None:
        with staging.open("xb") as handle:
            remaining = memoryview(data)
            while remaining:
                written = handle.write(remaining)
                if written is None or written <= 0:
                    raise OSError("short write while publishing immutable evidence")
                remaining = remaining[written:]
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Sync directory metadata on platforms that expose a directory descriptor."""
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            if os.name == "nt":
                return
            raise
        try:
            os.fsync(descriptor)
        except OSError:
            if os.name != "nt":
                raise
        finally:
            os.close(descriptor)

    def _verify_immutable_destination(
        self,
        path: Path,
        expected_data: bytes,
        expected_sha256: str | None,
        stage: str,
    ) -> None:
        actual = path.read_bytes()
        if actual == expected_data:
            return
        actual_sha256 = ArtifactRef.from_bytes(actual).sha256
        self._preserve_corruption(
            f"{stage}_publication_conflict",
            expected_sha256,
            actual_sha256=actual_sha256,
        )
        raise ArtifactAuthorityConflictError("immutable publication would overwrite existing evidence")

    def _verify_path(self, path: Path, artifact: ArtifactRef) -> None:
        if not path.is_file():
            self._preserve_corruption("missing_published_copy", artifact.sha256)
            raise ArtifactIntegrityError("published artifact is missing")
        actual = ArtifactRef.from_bytes(path.read_bytes())
        if actual != artifact:
            self._preserve_corruption(
                "published_copy_integrity_failure",
                artifact.sha256,
                actual_sha256=actual.sha256,
            )
            raise ArtifactIntegrityError("published bytes failed verification")

    def _reject_cycles(self, dependencies: dict[str, tuple[str, ...]]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                self._preserve_corruption("cyclic_source_reference", node)
                raise ArtifactIntegrityError("artifact source references contain a cycle")
            if node in visited:
                return
            visiting.add(node)
            for child in dependencies[node]:
                visit(child)
            visiting.remove(node)
            visited.add(node)

        for node in sorted(dependencies):
            visit(node)

    def _blob_path(self, sha256: str) -> Path:
        return self._blobs / sha256[:2] / sha256[2:]

    def _manifest_path(self, sha256: str) -> Path:
        return self._manifests / sha256[:2] / f"{sha256[2:]}.json"

    def _preserve_corruption(
        self,
        reason: str,
        expected_sha256: str | None,
        *,
        actual_sha256: str | None = None,
    ) -> None:
        record = {
            "actual_sha256": actual_sha256,
            "expected_sha256": expected_sha256,
            "observed_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "reason": reason,
        }
        fingerprint = sha256(
            f"{reason}|{expected_sha256}|{actual_sha256}".encode("utf-8")
        ).hexdigest()
        path = self._corruption / f"{fingerprint}.json"
        if path.exists():
            return
        self._publish_immutable(path, canonical_json_bytes(record), None, "corruption_record")

    def _notify(self, stage: str) -> None:
        if self._publication_hook is not None:
            self._publication_hook(stage)


def _freeze_edges(edges: dict[str, list[str]]) -> dict[str, tuple[str, ...]]:
    return {key: tuple(sorted(values)) for key, values in sorted(edges.items())}
