from __future__ import annotations

import asyncio
import base64
import inspect
from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.adapters.grader.executable import CredentialFreeExecutableGrader
from memrelay_eval.adapters.workspace.base import WorkspaceSnapshot
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import GraderContract, GraderResult
from memrelay_eval.domain.errors import GraderContractError, GraderReplayMismatchError
from memrelay_eval.domain.states import GraderTerminalKind
from memrelay_eval.evidence.required import require_unpaid_conformance_ports
from memrelay_eval.scoring.service import (
    classify_candidate_flakiness,
    compare_grading_replays,
    grade_with_bounded_regrades,
    require_intake_stability,
    require_matching_replays,
)


def _files_document(files: dict[str, bytes]) -> bytes:
    return canonical_bytes(
        {
            "schema_version": "1.0.0",
            "files": [
                {
                    "path": path,
                    "mode": 0o644,
                    "sha256": sha256(contents).hexdigest(),
                    "content_base64": base64.b64encode(contents).decode("ascii"),
                }
                for path, contents in sorted(files.items())
            ],
        }
    )


def _snapshot(
    store: InMemoryArtifactStore,
    *,
    baseline: dict[str, bytes] | None = None,
    terminal: dict[str, bytes] | None = None,
) -> WorkspaceSnapshot:
    baseline = baseline or {"app.py": b"answer = 1\n"}
    terminal = terminal or {"app.py": b"answer = 2\n"}
    baseline_ref = store.put_bytes(
        _files_document(baseline),
        media_type="application/json",
        classification="workspace_snapshot",
    )
    terminal_ref = store.put_bytes(
        _files_document(terminal),
        media_type="application/json",
        classification="workspace_snapshot",
    )
    patch_ref = store.put_bytes(
        b"diff --git a/app.py b/app.py\n",
        media_type="text/x-diff",
        classification="workspace_patch",
    )
    document = canonical_bytes(
        {
            "schema_version": "1.0.0",
            "normalization": {
                "paths": "relative_posix",
                "timestamps": "omitted",
                "file_order": "codepoint",
                "reparse_points": "rejected",
            },
            "baseline_revision": "a" * 40,
            "terminal_revision": "a" * 40,
            "baseline_files_sha256": baseline_ref.sha256,
            "terminal_files_sha256": terminal_ref.sha256,
            "patch_sha256": patch_ref.sha256,
        }
    )
    canonical_ref = store.put_bytes(
        document, media_type="application/json", classification="workspace_snapshot"
    )
    return WorkspaceSnapshot(
        revision="a" * 40,
        source_content_sha256="b" * 64,
        workspace_content_sha256="c" * 64,
        baseline_revision="a" * 40,
        baseline_files_artifact=baseline_ref,
        terminal_files_artifact=terminal_ref,
        patch_artifact=patch_ref,
        canonical_artifact=canonical_ref,
        canonical_sha256=canonical_ref.sha256,
    )


def _contract(
    store: InMemoryArtifactStore, snapshot: WorkspaceSnapshot, script: str, **changes: object
) -> GraderContract:
    assert snapshot.baseline_files_artifact is not None
    native = store.put_bytes(
        script.encode(), media_type="text/x-python", classification="grader_native_tests"
    )
    hidden = store.put_bytes(
        b"assert True\n", media_type="text/x-python", classification="grader_hidden_tests"
    )
    dependencies = store.put_bytes(
        b"fixture-dependency==1\n", media_type="text/plain", classification="grader_dependencies"
    )
    values: dict[str, object] = {
        "grader_version": "fixture-grader/1",
        "grader_sha256": "d" * 64,
        "native_tests_artifact": native,
        "hidden_tests_artifact": hidden,
        "dependencies_artifact": dependencies,
        "scope_policy_sha256": "e" * 64,
        "tamper_policy_sha256": "f" * 64,
        "command": ("{python}", "{native_tests}", "{hidden_tests}", "{snapshot}"),
        "allowed_paths": ("app.py",),
        "forbidden_paths": ("secrets/**",),
        "expected_baseline_revision": "a" * 40,
        "expected_baseline_files_sha256": snapshot.baseline_files_artifact.sha256,
    }
    values.update(changes)
    return GraderContract(**values)  # type: ignore[arg-type]


def _passing_script() -> str:
    return (
        "import json, pathlib, sys\n"
        "hidden, snapshot = sys.argv[1:]\n"
        "assert pathlib.Path(hidden).read_bytes() == b'assert True\\n'\n"
        "assert (pathlib.Path(snapshot) / 'app.py').read_text() == 'answer = 2\\n'\n"
        "print(json.dumps({'tests': {'native': True, 'hidden': True}, "
        "'continuous_score': 1.0, 'objective_components': {'fraction': 1.0}}))\n"
    )


def _grade(
    store: InMemoryArtifactStore,
    snapshot: WorkspaceSnapshot,
    script: str | None = None,
    timeout_seconds: float = 1.0,
    **changes: object,
) -> GraderResult:
    grader = CredentialFreeExecutableGrader(store, timeout_seconds=timeout_seconds)
    return asyncio.run(
        grader.grade(snapshot, _contract(store, snapshot, script or _passing_script(), **changes))
    )


def test_identical_frozen_snapshot_and_contract_replay_exactly() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)

    first = _grade(store, snapshot)
    second = _grade(store, snapshot)

    assert first.terminal is GraderTerminalKind.PASSED
    assert first.test_outcomes == second.test_outcomes
    assert first.continuous_score == second.continuous_score == 1.0
    assert compare_grading_replays(first, second).matches is True


def test_grading_reads_detached_bytes_not_a_later_mutable_workspace(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    mutable = tmp_path / "workspace"
    mutable.mkdir()
    (mutable / "app.py").write_text("answer = 999\n", encoding="utf-8")

    result = _grade(store, snapshot)

    assert result.terminal is GraderTerminalKind.PASSED


def test_partial_or_corrupt_snapshot_fails_closed() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    partial = WorkspaceSnapshot(
        snapshot.revision, snapshot.source_content_sha256, snapshot.workspace_content_sha256
    )

    grader = CredentialFreeExecutableGrader(store)
    contract = _contract(store, snapshot, _passing_script())
    assert asyncio.run(grader.grade(partial, contract)).terminal is GraderTerminalKind.BLOCKED
    assert snapshot.canonical_artifact is not None
    store._blobs[snapshot.canonical_artifact.sha256] = b"{}"
    assert _grade(store, snapshot).terminal is GraderTerminalKind.BLOCKED


def test_path_escape_and_scope_tampering_are_blocked() -> None:
    store = InMemoryArtifactStore()
    escaped = _snapshot(store, terminal={"../escape.py": b"bad"})

    assert _grade(store, escaped).terminal is GraderTerminalKind.BLOCKED
    out_of_scope = _snapshot(store, terminal={"README.md": b"changed"})
    assert _grade(store, out_of_scope).terminal is GraderTerminalKind.BLOCKED


def test_frozen_baseline_and_contract_hash_mismatch_are_blocked() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)

    result = _grade(store, snapshot, expected_baseline_revision="b" * 40)

    assert result.terminal is GraderTerminalKind.BLOCKED
    assert result.binary_passed is None


def test_grader_receives_no_provider_credentials_or_assignment() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    script = (
        "import json, os\n"
        "blocked = {'OPENAI_API_KEY', 'GITHUB_TOKEN', 'GH_TOKEN', 'COPILOT_AUTH_TOKEN'}\n"
        "assert not blocked.intersection(os.environ)\n"
        "assert not any('assignment' in name.lower() for name in os.environ)\n"
        "print(json.dumps({'tests': {'credential_free': True}}))\n"
    )

    assert _grade(store, snapshot, script).terminal is GraderTerminalKind.PASSED
    assert "assignment" not in inspect.signature(CredentialFreeExecutableGrader.grade).parameters


def test_network_policy_is_enforced_inside_the_grader_process() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    script = "import socket\nsocket.create_connection(('example.invalid', 443))\n"

    result = _grade(store, snapshot, script)

    assert result.terminal is GraderTerminalKind.UNAVAILABLE
    assert result.raw_output_artifact is not None
    assert b"network denied" in store.open_verified(result.raw_output_artifact)


def test_secret_output_blocks_without_persisting_secret_bytes() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    secret = "sk-" + "z" * 24
    result = _grade(store, snapshot, "print('sk-' + 'z' * 24)\n")

    assert result.terminal is GraderTerminalKind.BLOCKED
    assert result.raw_output_artifact is None
    assert all(secret.encode() not in store.open_verified(ref) for ref in result.evidence_refs)


def test_timeout_and_crash_preserve_partial_raw_evidence() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    timeout = _grade(store, snapshot, "import time\nprint('started', flush=True)\ntime.sleep(2)\n")
    crash = _grade(
        store, snapshot, "print('before crash', flush=True)\nraise RuntimeError('fixture')\n"
    )

    assert timeout.terminal is GraderTerminalKind.UNAVAILABLE
    assert crash.terminal is GraderTerminalKind.UNAVAILABLE
    assert timeout.raw_output_artifact is not None
    assert crash.raw_output_artifact is not None
    assert b"started" in store.open_verified(timeout.raw_output_artifact)
    assert b"before crash" in store.open_verified(crash.raw_output_artifact)


def test_replay_disagreement_and_flaky_favorable_run_cannot_be_substituted() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    passed = _grade(store, snapshot)
    failed = _grade(
        store, snapshot, "import json\nprint(json.dumps({'tests': {'native': False}}))\n"
    )

    assert compare_grading_replays(passed, failed).matches is False
    with pytest.raises(GraderReplayMismatchError) as raised:
        require_matching_replays(passed, failed)
    assert raised.value.code == "grader_replay_mismatch"
    classification = classify_candidate_flakiness((False, True, False), (False, True, False))
    assert classification.is_preregistered_flaky_signature is True
    assert classification.aggregate_passed is False


def test_intake_requires_five_fresh_baseline_and_gold_patch_passes() -> None:
    require_intake_stability((True,) * 5, (True,) * 5)
    with pytest.raises(GraderContractError, match="grader baseline instability"):
        require_intake_stability((True, True, False, True, True), (True,) * 5)
    with pytest.raises(GraderContractError, match="grader gold patch instability"):
        require_intake_stability((True,) * 5, (True, True, False, True, True))


def test_regrade_reuses_the_same_snapshot_and_contract_with_a_bounded_count() -> None:
    store = InMemoryArtifactStore()
    snapshot = _snapshot(store)
    contract = _contract(store, snapshot, "raise RuntimeError('unavailable')\n", maximum_regrades=1)
    unavailable = asyncio.run(CredentialFreeExecutableGrader(store).grade(snapshot, contract))

    class UnavailableGrader:
        def __init__(self) -> None:
            self.calls: list[tuple[object, GraderContract]] = []

        async def grade(
            self, received_snapshot: object, received_contract: GraderContract
        ) -> GraderResult:
            self.calls.append((received_snapshot, received_contract))
            return unavailable

    grader = UnavailableGrader()
    results = asyncio.run(grade_with_bounded_regrades(grader, snapshot, contract))

    assert results == (unavailable, unavailable)
    assert grader.calls == [(snapshot, contract), (snapshot, contract)]


def test_concurrent_attempts_use_distinct_detached_snapshot_bytes() -> None:
    store = InMemoryArtifactStore()
    first = _snapshot(store, terminal={"app.py": b"answer = 2\n"})
    second = _snapshot(store, terminal={"app.py": b"answer = 3\n"})
    script = (
        "import json, pathlib, sys\n"
        "print(json.dumps({'tests': {'isolated': pathlib.Path(sys.argv[2], 'app.py').exists()}}))\n"
    )
    grader = CredentialFreeExecutableGrader(store)
    first_contract = _contract(store, first, script)
    second_contract = _contract(store, second, script)

    async def grade_both() -> tuple[GraderResult, GraderResult]:
        first_result, second_result = await asyncio.gather(
            grader.grade(first, first_contract), grader.grade(second, second_contract)
        )
        return first_result, second_result

    results = asyncio.run(grade_both())

    assert all(result.terminal is GraderTerminalKind.PASSED for result in results)
    assert results[0].snapshot_sha256 != results[1].snapshot_sha256


def test_fake_artifacts_remain_explicitly_unpaid() -> None:
    store = InMemoryArtifactStore()

    require_unpaid_conformance_ports(store, CredentialFreeExecutableGrader(store))
    assert store.eligible_for_paid_or_study is False
