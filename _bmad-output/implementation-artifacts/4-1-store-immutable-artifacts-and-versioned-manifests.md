# Story 4.1: Store Immutable Artifacts and Versioned Manifests

Status: ready-for-dev

## Story

As an evidence operator,
I want a SHA-256 content-addressed store with verified manifests,
So that every raw or derived artifact is immutable and corruption is detectable.

## Acceptance Criteria

1. **Given** artifact bytes and metadata  
   **When** the filesystem CAS stores them  
   **Then** identity is lowercase SHA-256, bytes reside under `blobs/sha256/<prefix>/<remainder>`, and a versioned manifest records opaque IDs, attempt, kind, size, media type, producer/version, classification, secret flag, sources, and retention policy  
   **And** paths are convenience indexes, never identity.
2. **Given** any write, read, or copy  
   **When** hash verification fails or manifest authority conflicts  
   **Then** the operation fails closed with preserved corruption evidence  
   **And** the artifact cannot be linked for inclusion.
3. **Given** intact manifests and blobs  
   **When** indexes are deleted and rebuilt  
   **Then** experiment, run, attempt, and evidence reachability is reproduced  
   **And** artifacts remain retained until every linked claim is formally retired.

## Dependencies and Prerequisites

- **Authoritative direct graph dependency:** Story 1.1 only, exactly as declared in `epics.md`.
- Story 1.1 supplies domain-owned `ArtifactRef`, `ArtifactManifest` schema `1.0.0`, one shared canonicalizer, and `ArtifactStorePort`; this story qualifies the concrete adapter without replacing those contracts.
- Stories 2.8, 2.10, and 3.1-3.3 already define consumers that may use deterministic fake artifacts only for unpaid conformance. This story replaces no domain contract; it qualifies the durable adapter.
- The repository currently has no `evaluation/` tree. Implement after predecessor contracts exist; do not infer implementation learnings from ready-for-dev guides.
- Paid execution and study inclusion remain blocked until this durable filesystem CAS passes corruption, rebuild, secret-boundary, and qualification conformance.
- Exact traceability: FR42; NFR23, NFR29, NFR30, NFR37, NFR40; AR27.

## Tasks / Subtasks

- [ ] Implement the durable filesystem CAS behind `ArtifactStorePort` (AC: 1)
  - [ ] Hash exact bytes with stdlib SHA-256; use lowercase hex and `blobs/sha256/<first-two>/<remaining-62>`.
  - [ ] Write to a sibling staging file, flush/fsync as supported, atomically replace into final location, and make duplicate puts idempotent after byte/hash verification.
  - [ ] Treat every path, `.ref.json`, and reachability index as rebuildable convenience state.
- [ ] Finalize and validate artifact manifest schema `1.0.0` (AC: 1)
  - [ ] Preserve opaque artifact/attempt IDs, kind, digest, `size_bytes`, media type, UTC creation time, producer component/version, classification, `contains_secrets`, source artifact IDs, retention policy, and optional encryption metadata.
  - [ ] Require every source artifact ID in a derived manifest to resolve to a verified digest and include those resolved source digests in derivation identity; do not silently change the frozen `1.0.0` authority fields.
  - [ ] Canonicalize JSON only with the Story 1 RFC 8785 canonicalizer; raw blobs hash exact bytes.
- [ ] Implement verified put/open/copy and authority checks (AC: 1, 2)
  - [ ] Verify digest and size before publication, on every read, and after every copy.
  - [ ] Reject conflicting manifests for the same authoritative identity; preserve a redacted corruption/conflict record without replacing either source.
  - [ ] Prevent corrupt, secret-policy-violating, or authority-conflicted artifacts from receiving inclusion links.
- [ ] Implement deterministic reachability index rebuild (AC: 3)
  - [ ] Reconstruct experiment/run/attempt/evidence edges only from verified manifests and append-only artifact links; never trust `.ref.json`, directory names, or a stale index as authority.
  - [ ] Detect orphan, missing, malformed, duplicate-authority, and cyclic source references without deleting evidence.
- [ ] Enforce retention and qualification gates (AC: 2, 3)
  - [ ] Retain blobs while any evidence, endpoint, report, or claim is live; no “cleanup” may erase corruption evidence.
  - [ ] Emit a durable-adapter qualification result distinct from fake `unpaid_conformance` provenance.
- [ ] Add contract, fault, property, and no-network tests (AC: 1-3)
  - [ ] Cover concurrent duplicate puts, crash at each publication boundary, short write, tamper, hash collision simulation, manifest conflict, deleted indexes, orphan recovery, and copy corruption.

## Developer Context

The CAS is the durable evidence-of-record boundary. Native `.eval`, JSON, SDK events, patches, traces, grader/judge records, cost records, configuration, environment fingerprints, and cleanup evidence remain separate immutable bytes. SQLite receives only `ArtifactRef`/digest links. A successful write is not qualified until exact bytes and manifest both verify. Crash recovery must be idempotent: retries either resolve the already-published matching object or fail closed; they never create a second identity or overwrite evidence.

### Architecture Compliance

- Follow AD-01, AD-04, AD-05, AD-11, AD-15, AD-17, AD-22, and AD-25.
- Evaluator Python is exactly 3.11. Domain remains stdlib-only; the filesystem adapter may use stdlib filesystem primitives and domain-owned types.

### Frozen Version Requirements

- JSON is UTF-8 RFC 8785 canonical JSON with explicit schema version and no NaN/Infinity. Binary identity is SHA-256 over exact bytes.
- `ArtifactManifest` schema is frozen at `1.0.0`; changing authority fields or identity rules requires a new schema/protocol version.

### File Structure Requirements

- **UPDATE:** `evaluation/src/memrelay_eval/domain/{entities,ports,errors}.py` only if predecessor contracts need compatible completion; do not replace them.
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/adapters/artifacts/filesystem.py`
- **NEW/UPDATE:** `evaluation/src/memrelay_eval/evidence/manifest.py`
- **NEW/UPDATE:** `evaluation/schemas/artifact-manifest.schema.json`
- **NEW:** `evaluation/tests/contract/artifacts/test_filesystem_cas.py`
- **NEW:** `evaluation/tests/fault/artifacts/test_cas_crash_corruption.py`
- **NEW:** `evaluation/tests/integration/artifacts/test_rebuild_reachability.py`
- **READ ONLY:** product `src/memrelay/**`, root `pyproject.toml`, operational SQLite, future Parquet/DuckDB adapters.

### Existing Behavior to Preserve

- Preserve fake adapter determinism and explicit `unpaid_conformance` ineligibility.
- Preserve byte-for-byte native evidence, partial/failed-attempt evidence, secret-scan findings, treatment-neutral opaque IDs, and all predecessor source hashes.
- Do not modify root package dependencies, product graph ownership, retry/exposure policy, or current uncommitted `.gitignore` work.
- Keep the evaluator artifact store distinct from the shipped product's daemon-owned graph/spool. This story does not open, migrate, or write a live product graph.

### Testing Requirements

- Re-run the same `ArtifactStorePort` contract against fake and filesystem adapters, with durability assertions only for the filesystem implementation.
- Prove atomic/idempotent publication across interruption boundaries and verified write/read/copy.
- Prove complete rebuild from manifests/blobs, deterministic reachability output, and fail-closed corruption/authority-conflict behavior.
- Keep CI credential-free and no-network. CAS conformance is construction evidence, not efficacy evidence.

### Anti-Patterns

- Do not use paths, filenames, timestamps, or database row IDs as content identity.
- Do not overwrite blobs/manifests, silently repair corrupt bytes, delete conflict evidence, store large bodies in SQLite, or add a second canonicalizer.
- Do not implement the SQLite writer, Collector, reconciliation decision engine, backup service, Parquet publication, or DuckDB analysis in this story.
- Future Parquet must be atomically published from reconciled terminal evidence and DuckDB must remain read-only; this CAS story supplies verified inputs only.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 4 / Story 4.1; FR42]
- [Source: `ARCHITECTURE-SPINE.md` — AD-04, AD-05, AD-15, AD-25; consistency conventions]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 6.5-6.6, 7, 16, 22, 25]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — R-008, R-017; `TOOL-CAS-*`, `BLOCK-EVIDENCE`]
- [Source: technical research — conditional completeness, native evidence, reproducibility, and CAS commitments]
- [Source: predecessor Stories 1.1, 2.10, 3.6 — domain ports, fake provenance, and authority preservation]

## Dev Agent Record

### Agent Model Used

TBD by implementation agent

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created

### File List
