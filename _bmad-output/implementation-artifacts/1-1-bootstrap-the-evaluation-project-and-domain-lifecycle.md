# Story 1.1: Bootstrap the Evaluation Project and Domain Lifecycle

Status: done

## Story

As an evaluator engineer,
I want a separate evaluation project with treatment-neutral domain identities and lifecycle rules,
So that I can plan studies without coupling evaluator dependencies or arm labels to the product.

## Acceptance Criteria

1. **Given** a Python 3.13 checkout of the repository
   **When** I install and invoke the `evaluation/` project  
   **Then** `evaluation/pyproject.toml`, `uv.lock`, `src/memrelay_eval`, schemas, catalog, collector configuration, tests, and `memrelay-eval` CLI are available  
   **And** evaluator dependencies are absent from the memrelay wheel metadata.
2. **Given** experiment, protocol, scenario, task, history, assignment, run, attempt, artifact, evidence, endpoint, claim, cost-entry, and inclusion records  
   **When** domain IDs and records are created  
   **Then** their IDs are opaque and contain no arm or treatment label  
   **And** `domain` imports only the standard library and domain-owned types.
3. **Given** a run or attempt transition request  
   **When** it violates `planned -> assigned -> provisioned -> running -> exported -> scored -> reconciled -> included|excluded`  
   **Then** the domain rejects it with a typed error  
   **And** attempt terminal classifications remain separate immutable records using the frozen terminal vocabulary.
4. **Given** domain evidence, lifecycle records, or telemetry  
   **When** an early story stores an artifact or emits a state change or event  
   **Then** domain-owned `ArtifactRef` values address immutable SHA-256 content, `ArtifactManifest` validates against schema version `1.0.0`, `LedgerPort` appends authoritative lifecycle records without mutation, and `TelemetryPort` emits redacted structured observations without becoming lifecycle truth  
   **And** deterministic fake/in-memory `ArtifactStorePort`, `LedgerPort`, and `TelemetryPort` adapters are available to early stories.
5. **Given** those deterministic adapters  
   **When** they are used before durable adapters pass conformance  
   **Then** their outputs support unpaid conformance only  
   **And** they cannot make paid or study runs eligible for inclusion.

## Dependencies and Prerequisites

- **Formal graph dependency:** none.
- This story is the prerequisite for every later Epic 1 story.
- Preserve the existing product package and its supported Python range; the evaluator is a separate project, not a new `memrelay` extra.
- No paid credential, provider call, study enrollment, efficacy claim, durable inclusion decision, or concrete SQLite/Collector adapter belongs here.
- **Repository baseline:** `evaluation/` does not exist in the current checkout. Root `pyproject.toml` builds only `src/memrelay`, uses Hatch, and limits the product to Python `>=3.11,<3.14`; all evaluator paths below are new. Existing `tests/eval/` and product integration tests are bounded product evidence, not the new evaluator test suite or efficacy evidence.

## Tasks / Subtasks

- [x] Create the isolated evaluator project and lock (AC: 1)
  - [x] Add the `evaluation/` package tree, Python 3.13 project metadata, independent `uv.lock`, README, schemas, catalog, collector, test, and artifact roots.
  - [x] Register `memrelay-eval` through the evaluator package composition root.
  - [x] Prove product wheel/sdist metadata and root runtime dependencies are unchanged.
- [x] Implement standard-library-only domain values (AC: 2, 4)
  - [x] Add immutable typed IDs and frozen records for every entity named in AC2.
  - [x] Add `ArtifactRef`, manifest `1.0.0` domain projection, error/reason types, and `LedgerPort`, `ArtifactStorePort`, `ExecutionAuthorityPort`, `AgentRuntimePort`, `TreatmentPort`, `WorkspacePort`, `AssignmentPort`, `GraderPort`, `TelemetryPort`, and `ReconciliationPort`.
  - [x] Make the manifest `1.0.0` projection cover `artifact_id`, `kind`, lowercase `sha256`, `size_bytes`, `media_type`, UTC `created_at`, producer component/version, classification, `contains_secrets`, source artifact IDs, retention policy ID, and encryption metadata.
  - [x] Define `attempt_id` as optional and valid only for attempt-scoped evidence. Pre-attempt experiment/run artifacts, including the Story 1.5 effective-configuration freeze, carry ownership through authoritative ledger `ArtifactLink` records rather than a fabricated attempt identity.
  - [x] Add an import-boundary test rejecting non-stdlib imports from `domain`.
- [x] Implement lifecycle and attempt-terminal policies (AC: 3)
  - [x] Validate only the frozen run transition graph.
  - [x] Keep attempt terminal records independent from run transitions and use: `succeeded`, `agent_failed`, `timed_out`, `provider_unavailable`, `quota_exhausted`, `grader_failed`, `evidence_incomplete`, `infrastructure_failed_pre_exposure`, `infrastructure_failed_post_exposure`, `cancelled_by_circuit_breaker`.
  - [x] Return typed failures; never silently coerce or skip a transition.
- [x] Implement explicitly non-durable deterministic adapters (AC: 4, 5)
  - [x] Provide in-memory artifact, ledger, and telemetry adapters with stable ordering and deterministic outputs.
  - [x] Mark their evidence provenance `unpaid_conformance` and make inclusion eligibility fail closed.
  - [x] Ensure telemetry redaction excludes prompts, code, repository/user names, credentials, provider payloads, and treatment labels by default.
- [x] Add focused unit and contract tests (AC: 1-5)

## Developer Context

This story establishes only the evaluator skeleton and domain seams. Inspect remains future execution authority; a future sole-writer SQLite adapter will own durable lifecycle truth. The fake ledger is not that adapter. Telemetry is observation, never a substitute state machine. The product currently supports Python `>=3.11,<3.14`, uses Hatch, and ships its own runtime dependency set; none may be altered by evaluator bootstrap.

### Architecture Compliance

- Follow the hexagonal modular-monolith dependency direction: `domain` imports only stdlib; application modules and adapters depend inward.
- Keep all evaluator code under `evaluation/`; product source must not import it.
- IDs use lowercase opaque type prefixes plus random/digest-backed values and never encode treatment.
- Use frozen dataclasses/value objects, UTC RFC 3339 `Z` timestamps plus monotonic duration values, lowercase SHA-256, UTF-8 canonical JSON, `snake_case` fields/modules, and `PascalCase` types.
- Run lifecycle and attempt terminal status are distinct append-only concepts.
- Deterministic fake paths prove unpaid conformance only. Durable/paid eligibility requires later SQLite, artifact, telemetry, reconciliation, backup, and restore conformance.

### Library and Version Requirements

- Evaluator interpreter: **Python 3.13** exactly; create and lock with `uv`.
- Testing baseline: stdlib + `pytest`; the architecture does not select a pytest patch, so resolve it in `evaluation/uv.lock` rather than inventing a normative version.
- Frozen future adapter pins to declare when the evaluator manifest is composed: `github-copilot-sdk==1.0.8`, `inspect-ai==0.3.252`, OTel SDK/exporters `==1.44.0`, DuckDB `==1.5.5`, PyArrow `==25.0.0`, OpenInference semantic conventions `==0.1.31`, and `openinference-instrumentation-openai==0.1.53`. Do not import or execute these from `domain`.
- Preserve product constraints from root `pyproject.toml`: `traceforge-toolkit>=0.1,<0.1.2`, `graphiti-core>=0.29,<0.30`, `ladybug>=0.18,<0.18.1`, and `mcp>=1.0,<2`.
- Do not add dependencies to root `pyproject.toml`.

### Expected File Paths

- **NEW:** `evaluation/pyproject.toml`, `evaluation/uv.lock`, `evaluation/README.md`
- **NEW:** `evaluation/src/memrelay_eval/__init__.py`
- **NEW:** `evaluation/src/memrelay_eval/domain/{ids,entities,states,errors,ports,policies}.py`
- **NEW:** `evaluation/src/memrelay_eval/adapters/fakes.py`
- **NEW:** `evaluation/src/memrelay_eval/cli/{main,commands}.py`
- **NEW:** `evaluation/schemas/artifact-manifest.schema.json`
- **NEW:** `evaluation/catalog/catalog.yaml`, `evaluation/collector/collector.yaml`
- **NEW:** `evaluation/artifacts/.gitignore`
- **NEW:** `evaluation/tests/unit/`, `evaluation/tests/contract/`
- **UPDATE:** none in `src/memrelay/` or root `pyproject.toml`

Paths marked **NEW** are absent at story start. Do not reinterpret existing root `tests/eval/` as `evaluation/tests/`, and do not move or import product tests into the evaluator package.

### Existing Behavior to Preserve

- Root package metadata, Hatch build, `memrelay` CLI, daemon/MCP ownership, product dependency bounds, and current tests.
- Existing `.gitignore` entries and the user's current uncommitted `.gitignore` work; prefer the nested evaluator artifact ignore rather than replacing root rules.
- No evaluator package or artifact may enter the product wheel/sdist.

### Testing Requirements

- Install and invoke `memrelay-eval --help` from the evaluator environment.
- Assert product built metadata contains no evaluator dependency/module.
- Property/table tests cover every valid and invalid lifecycle edge and terminal vocabulary member.
- ID tests reject treatment/arm substrings and verify stable serialization.
- Import test walks `domain` AST/imports and permits stdlib/domain only.
- Fake-adapter tests prove deterministic ordering, immutability, redaction, and unconditional paid/study-ineligibility.
- Manifest tests cover experiment-, run-, and attempt-scoped artifacts, require `attempt_id` for attempt-scoped evidence, and reject a fabricated `attempt_id` on pre-attempt artifacts.
- Manifest schema tests cover every §16.2 field, lowercase digest/size agreement, source lineage, secret flag/classification, and schema-version rejection.
- Run evaluator tests without network, Copilot, OpenAI, or provider credentials.

### Anti-Patterns

- Do not modify product runtime dependencies or place evaluator code under `src/memrelay`.
- Do not use SDK, ORM, Pydantic, telemetry, or JSON-schema types in the domain.
- Do not make the fake ledger durable or call fake evidence “included.”
- Do not merge run lifecycle with attempt terminal classification.
- Do not encode treatment in IDs, logs, exceptions, fixtures, or manifests.
- Do not implement future concrete adapters, assignment, catalog compilation, or paid execution.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` — “Epic 1” / “Story 1.1”]
- [Source: `ARCHITECTURE-SPINE.md` — AD-01, AD-02, AD-05, AD-11, AD-17, AD-22]
- [Source: `IMPLEMENTATION-DESIGN.md` — §§5, 6.1, 8, 20, 22.1, 24.1, 24.4]
- [Source: `_bmad-output/test-artifacts/test-design-qa.md` — “Dependencies & Test Blockers”; “NFR Test Coverage Plan”]
- [Source: `_bmad-output/test-artifacts/test-design/dfinson-solid-happiness-handoff.md` — “Quality gates”; “Open Implementation Blockers”]
- [Source: `pyproject.toml` — `[project]`, `[tool.hatch.build.targets.*]`, pytest/ruff configuration]
- [Source: `SPEC.md` — §2 and §8]

## Dev Agent Record

### Agent Model Used

GPT-5.6 Terra (gpt-5.6-terra)

### Debug Log References

- `uv lock --directory .\evaluation --python 3.13`
- `.\evaluation\.venv\Scripts\memrelay-eval.exe --help`
- `.\evaluation\.venv\Scripts\python.exe -m pytest .\evaluation\tests`
- `C:\Python312\python.exe -m ruff check .`
- `C:\Python312\python.exe -m ruff format --check .`
- `$env:PYTHONPATH = (Resolve-Path .\src).Path; C:\Python312\python.exe -m pytest`
- `.\evaluation\.venv\Scripts\python.exe -m hatchling build -t wheel`

### Completion Notes List

- Created an independently lockable Python 3.13 evaluator project with its own CLI,
  test suite, schema, catalog, collector configuration, and ignored artifact root.
- Added standard-library-only, immutable treatment-neutral IDs and domain records,
  a frozen run lifecycle graph, and separate immutable attempt terminal records.
- Added ArtifactManifest schema/domain validation for experiment, run, and attempt
  scope; pre-attempt artifacts are authoritatively owned through ArtifactLink records.
- Added deterministic in-memory artifact, ledger, and telemetry adapters that redact
  sensitive telemetry by default and categorically reject paid or study inclusion.
- Verified the evaluator suite (101 tests), project CLI, evaluator lock, Python
  compilation, repository lint/format gates, and built product wheel metadata boundary.

### File List

- `_bmad-output/implementation-artifacts/1-1-bootstrap-the-evaluation-project-and-domain-lifecycle.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `evaluation/README.md`
- `evaluation/pyproject.toml`
- `evaluation/uv.lock`
- `evaluation/artifacts/.gitignore`
- `evaluation/catalog/catalog.yaml`
- `evaluation/collector/collector.yaml`
- `evaluation/schemas/artifact-manifest.schema.json`
- `evaluation/src/memrelay_eval/__init__.py`
- `evaluation/src/memrelay_eval/adapters/__init__.py`
- `evaluation/src/memrelay_eval/adapters/fakes.py`
- `evaluation/src/memrelay_eval/cli/__init__.py`
- `evaluation/src/memrelay_eval/cli/commands.py`
- `evaluation/src/memrelay_eval/cli/main.py`
- `evaluation/src/memrelay_eval/domain/__init__.py`
- `evaluation/src/memrelay_eval/domain/entities.py`
- `evaluation/src/memrelay_eval/domain/errors.py`
- `evaluation/src/memrelay_eval/domain/ids.py`
- `evaluation/src/memrelay_eval/domain/policies.py`
- `evaluation/src/memrelay_eval/domain/ports.py`
- `evaluation/src/memrelay_eval/domain/states.py`
- `evaluation/tests/contract/test_fakes.py`
- `evaluation/tests/contract/test_project_boundary.py`
- `evaluation/tests/unit/test_artifact_manifest.py`
- `evaluation/tests/unit/test_ids_and_entities.py`
- `evaluation/tests/unit/test_import_boundaries.py`
- `evaluation/tests/unit/test_lifecycle.py`
