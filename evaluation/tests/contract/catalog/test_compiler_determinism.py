from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from memrelay_eval.catalog import compiler as compiler_module
from memrelay_eval.catalog import validation as validation_module
from memrelay_eval.catalog.canonical import attach_digest, canonical_bytes
from memrelay_eval.catalog.compiler import (
    CatalogCompileError,
    CatalogRecoveryError,
    compile_catalog_command,
    verify_compiled_catalog,
)
from memrelay_eval.catalog.validation import CatalogValidationError

EVALUATION_ROOT = Path(__file__).parents[3]
SOURCE_CATALOG = EVALUATION_ROOT / "catalog" / "catalog.yaml"
GENERATED_FILENAMES = (
    "tasks.json",
    "assignment-inputs.json",
    "fixture-manifest.json",
    "traceability.json",
)


class _SimulatedTermination(BaseException):
    pass


def catalog_root(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir(parents=True)
    shutil.copy2(SOURCE_CATALOG, root / "catalog.yaml")
    return root


def compile_paths(root: Path) -> dict[str, Path]:
    return {
        "catalog": root / "catalog.yaml",
        "output": root / "generated",
        "lock": root / "catalog-lock.json",
        "manifest": root / "compile-manifest.json",
    }


def compile_in_process(root: Path) -> None:
    paths = compile_paths(root)
    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )
    assert result.terminal_status == "succeeded"
    assert result.exit_code == 0


def compiled_bytes(root: Path) -> dict[str, bytes]:
    paths = compile_paths(root)
    return {
        **{
            f"generated/{filename}": (paths["output"] / filename).read_bytes()
            for filename in GENERATED_FILENAMES
        },
        "catalog-lock.json": paths["lock"].read_bytes(),
    }


def test_compile_is_byte_identical_across_repeated_clean_processes(tmp_path: Path) -> None:
    first = catalog_root(tmp_path / "first")
    second = catalog_root(tmp_path / "second")
    compile_in_process(first)
    first_bytes = compiled_bytes(first)

    command = [
        sys.executable,
        "-m",
        "memrelay_eval.cli.main",
        "compile-catalog",
        "--catalog",
        "catalog/catalog.yaml",
        "--output-dir",
        "catalog/generated",
        "--lock",
        "catalog/catalog-lock.json",
        "--manifest",
        "catalog/compile-manifest.json",
    ]
    completed = subprocess.run(
        command,
        cwd=second.parent,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert compiled_bytes(second) == first_bytes
    second_snapshot = compiled_bytes(second)
    compile_in_process(second)
    assert compiled_bytes(second) == second_snapshot


def test_compile_normalizes_git_crlf_checkout_bytes_for_source_identity(tmp_path: Path) -> None:
    lf_root = catalog_root(tmp_path / "lf")
    crlf_root = catalog_root(tmp_path / "crlf")
    lf_catalog = compile_paths(lf_root)["catalog"]
    crlf_catalog = compile_paths(crlf_root)["catalog"]
    source = lf_catalog.read_bytes().replace(b"\r\n", b"\n")
    lf_catalog.write_bytes(source)
    crlf_catalog.write_bytes(source.replace(b"\n", b"\r\n"))

    compile_in_process(lf_root)
    compile_in_process(crlf_root)

    assert compiled_bytes(crlf_root) == compiled_bytes(lf_root)


def test_compile_preserves_authored_scenario_and_reference_order(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    catalog = yaml.safe_load(paths["catalog"].read_text(encoding="utf-8"))
    first_scenario = catalog["scenarios"][0]
    second_scenario = deepcopy(first_scenario)
    second_scenario["id"] = f"scenario_{'9' * 32}"
    second_scenario["title"] = "A separately authored synthetic scenario"
    second_scenario["injected_conditions"][0]["id"] = f"condition_{'9' * 32}"
    second_scenario["procedure"]["id"] = f"procedure_{'9' * 32}"
    second_scenario["pass_criteria"]["id"] = f"verdict_{'9' * 32}"
    second_protocol = f"protocol_{'9' * 32}"
    catalog["references"]["protocols"].insert(0, second_protocol)
    second_scenario["protocol_ids"] = [second_protocol, first_scenario["protocol_ids"][0]]
    catalog["scenarios"] = [second_scenario, first_scenario]
    paths["catalog"].write_text(yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8")

    compile_in_process(root)
    tasks = json.loads((paths["output"] / "tasks.json").read_text(encoding="utf-8"))
    assignment_inputs = json.loads(
        (paths["output"] / "assignment-inputs.json").read_text(encoding="utf-8")
    )

    assert [task["scenario_id"] for task in tasks["tasks"]] == [
        second_scenario["id"],
        first_scenario["id"],
    ]
    assert tasks["tasks"][0]["protocol_ids"] == second_scenario["protocol_ids"]
    assert (
        assignment_inputs["assignment_inputs"][0]["protocol_ids"] == second_scenario["protocol_ids"]
    )


def test_content_change_lock_is_history_independent_and_freshly_verifiable(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    compile_in_process(root)
    prior_lock = tmp_path / "prior-lock.json"
    prior_lock.write_bytes(paths["lock"].read_bytes())
    paths["catalog"].write_text(
        paths["catalog"]
        .read_text(encoding="utf-8")
        .replace('catalog_version: "1.0.0"', 'catalog_version: "1.0.1"')
        .replace("Validate the executable catalog", "Validate changed catalog content"),
        encoding="utf-8",
    )

    changed = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        prior_lock=prior_lock,
    )
    changed_bytes = compiled_bytes(root)
    changed_lock = json.loads(paths["lock"].read_text(encoding="utf-8"))
    changed_manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert changed.terminal_status == "succeeded"
    assert "change_kind" not in changed_lock
    assert changed_manifest["catalog_change_kind"] == "content"
    verify_compiled_catalog(paths["output"], paths["lock"], catalog_path=paths["catalog"])

    unchanged = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )

    assert unchanged.terminal_status == "succeeded"
    assert compiled_bytes(root) == changed_bytes
    assert (
        json.loads(paths["manifest"].read_text(encoding="utf-8"))["catalog_change_kind"] == "none"
    )


def test_custom_output_directory_is_locked_verified_and_exact(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    output = root / "outputs"
    result = compile_catalog_command(
        paths["catalog"],
        output_dir=output,
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )
    lock = json.loads(paths["lock"].read_text(encoding="utf-8"))

    assert result.terminal_status == "succeeded"
    assert {document["path"] for document in lock["generated_outputs"].values()} == {
        f"outputs/{filename}" for filename in GENERATED_FILENAMES
    }
    verify_compiled_catalog(output, paths["lock"], catalog_path=paths["catalog"])

    (output / "rogue.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CatalogCompileError, match="undeclared artifact"):
        verify_compiled_catalog(output, paths["lock"], catalog_path=paths["catalog"])
    (output / "rogue.json").unlink()

    lock["generated_outputs"]["tasks.json"]["path"] = "generated/tasks.json"
    paths["lock"].write_bytes(canonical_bytes(attach_digest(lock)))
    with pytest.raises(CatalogCompileError, match="path is invalid"):
        verify_compiled_catalog(output, paths["lock"], catalog_path=paths["catalog"])


@pytest.mark.parametrize("rollback_failure", ("live_to_failed", "backup_to_live"))
def test_rollback_failure_preserves_recoverable_prior_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rollback_failure: str
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)
    original_replace = compiler_module._durable_replace

    def fail_rollback(source: Path, destination: Path) -> None:
        if rollback_failure == "live_to_failed" and destination.name.startswith(
            ".catalog-compile-failed-"
        ):
            raise OSError("injected live-to-failed rollback failure")
        if (
            rollback_failure == "backup_to_live"
            and source.name.startswith(".catalog-compile-backup-")
            and destination == root
        ):
            raise OSError("injected backup-to-live rollback failure")
        original_replace(source, destination)

    monkeypatch.setattr(compiler_module, "_durable_replace", fail_rollback)
    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        after_live=lambda _: (_ for _ in ()).throw(RuntimeError("post-live failure")),
    )

    backups = list(root.parent.glob(".catalog-compile-backup-*"))
    assert result.terminal_status == "failed"
    assert backups
    assert compiled_bytes(backups[0]) == before
    assert list(root.parent.glob(".catalog-compile-transaction-*.json"))
    if not root.exists():
        fallback_manifest = root.parent / ".catalog-command-evidence" / "compile-manifest.json"
        assert (
            json.loads(fallback_manifest.read_text(encoding="utf-8"))["terminal_status"] == "failed"
        )

    monkeypatch.setattr(compiler_module, "_durable_replace", original_replace)
    if root.exists():
        verify_compiled_catalog(paths["output"], paths["lock"], catalog_path=paths["catalog"])
    else:
        verify_compiled_catalog(
            root / "generated",
            root / "catalog-lock.json",
            catalog_path=root / "catalog.yaml",
        )
    assert root.exists()
    assert not list(root.parent.glob(".catalog-compile-backup-*"))
    assert not list(root.parent.glob(".catalog-compile-transaction-*.json"))


@pytest.mark.parametrize("hook_name", ("after_publish_verify", "before_manifest", "after_manifest"))
@pytest.mark.parametrize("exception_type", (KeyboardInterrupt, _SimulatedTermination))
def test_post_publish_interruption_keeps_truthful_hash_receipt(
    tmp_path: Path, hook_name: str, exception_type: type[BaseException]
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)
    paths["catalog"].write_text(
        paths["catalog"]
        .read_text(encoding="utf-8")
        .replace('catalog_version: "1.0.0"', 'catalog_version: "1.0.1"')
        .replace("Validate the executable catalog", "Interrupted publication catalog"),
        encoding="utf-8",
    )

    def interrupt(_: object) -> None:
        raise exception_type()

    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        **{hook_name: interrupt},
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert result.terminal_status == "interrupted"
    assert manifest["terminal_status"] == "interrupted"
    assert manifest["canonical_output_sha256"]
    assert manifest["catalog_input_sha256"] is not None
    if hook_name == "after_publish_verify":
        assert compiled_bytes(root) == before
    else:
        verify_compiled_catalog(paths["output"], paths["lock"], catalog_path=paths["catalog"])


@pytest.mark.parametrize("exception_type", (KeyboardInterrupt, _SimulatedTermination))
def test_interruption_inside_post_publish_verification_restores_prior_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exception_type: type[BaseException]
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)
    paths["catalog"].write_text(
        paths["catalog"]
        .read_text(encoding="utf-8")
        .replace('catalog_version: "1.0.0"', 'catalog_version: "1.0.1"')
        .replace("Validate the executable catalog", "Verification interruption catalog"),
        encoding="utf-8",
    )
    original_verify = compiler_module._verify_compiled_catalog
    call_count = 0

    def interrupt_live_verify(*args: object, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise exception_type()
        original_verify(*args, **kwargs)

    monkeypatch.setattr(compiler_module, "_verify_compiled_catalog", interrupt_live_verify)
    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert result.terminal_status == "interrupted"
    assert call_count == 2
    assert compiled_bytes(root) == before
    assert manifest["canonical_output_sha256"]
    assert manifest["catalog_input_sha256"] is not None


def test_catalog_artifacts_sync_before_the_attesting_command_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    events: list[tuple[str, str]] = []
    original_sync = compiler_module._fsync_directory
    original_manifest = compiler_module._write_command_manifest

    def record_sync(path: Path) -> None:
        events.append(("sync", path.name))
        original_sync(path)

    def record_manifest(*args: object, **kwargs: object) -> None:
        events.append(("manifest", "compile-manifest.json"))
        original_manifest(*args, **kwargs)

    monkeypatch.setattr(compiler_module, "_fsync_directory", record_sync)
    monkeypatch.setattr(compiler_module, "_write_command_manifest", record_manifest)
    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )
    manifest_index = events.index(("manifest", "compile-manifest.json"))

    assert result.terminal_status == "succeeded"
    assert ("sync", "generated") in events[:manifest_index]
    assert ("sync", "catalog") in events[:manifest_index]


def test_catalog_root_working_directory_is_rejected_without_mutation(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)
    command = [
        sys.executable,
        "-m",
        "memrelay_eval.cli.main",
        "compile-catalog",
        "--catalog",
        str(paths["catalog"]),
        "--output-dir",
        str(paths["output"]),
        "--lock",
        str(paths["lock"]),
        "--manifest",
        str(paths["manifest"]),
    ]

    completed = subprocess.run(command, cwd=root, check=False, capture_output=True, text=True)

    assert completed.returncode == 1
    assert "working directory outside the catalog root" in completed.stdout
    assert compiled_bytes(root) == before


def test_compiler_enforces_allowed_retries_when_schema_is_loosened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    schema = json.loads((EVALUATION_ROOT / "schemas" / "scenario.schema.json").read_text())
    schema["$defs"]["scenario"]["properties"]["allowed_retries"] = {"type": "integer"}
    monkeypatch.setattr(validation_module, "_load_schema", lambda: schema)
    paths["catalog"].write_text(
        paths["catalog"]
        .read_text(encoding="utf-8")
        .replace("allowed_retries: 0", "allowed_retries: 2"),
        encoding="utf-8",
    )

    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )

    assert result.terminal_status == "failed"
    assert result.error is not None
    assert "allowed_retries must be an integer" in str(result.error)


def test_ambient_stale_lock_does_not_change_compilation_identity(tmp_path: Path) -> None:
    fresh_root = catalog_root(tmp_path / "fresh")
    stale_root = catalog_root(tmp_path / "stale")
    stale_paths = compile_paths(stale_root)
    stale_paths["lock"].write_text('{"stale":"unrelated"}', encoding="utf-8")

    compile_in_process(fresh_root)
    compile_in_process(stale_root)

    assert compiled_bytes(stale_root) == compiled_bytes(fresh_root)


def test_explicit_prior_lock_controls_version_policy_and_binds_manifest_hash(
    tmp_path: Path,
) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    compile_in_process(root)
    prior_lock = tmp_path / "explicit-prior-lock.json"
    prior_lock.write_bytes(paths["lock"].read_bytes())
    original_prior_sha256 = sha256(prior_lock.read_bytes()).hexdigest()
    changed = (
        paths["catalog"]
        .read_text(encoding="utf-8")
        .replace("Validate the executable catalog", "Explicit version policy catalog")
    )
    paths["catalog"].write_text(changed, encoding="utf-8")

    rejected = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        prior_lock=prior_lock,
    )
    assert rejected.terminal_status == "failed"
    assert json.loads(paths["manifest"].read_text(encoding="utf-8"))["prior_lock"] == {
        "path": "explicit-prior-lock.json",
        "sha256": original_prior_sha256,
    }

    paths["catalog"].write_text(
        changed.replace('catalog_version: "1.0.0"', 'catalog_version: "1.0.1"'),
        encoding="utf-8",
    )
    accepted = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        prior_lock=prior_lock,
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert accepted.terminal_status == "succeeded"
    assert manifest["prior_lock"]["sha256"] == original_prior_sha256

    prior_lock.write_bytes(prior_lock.read_bytes() + b" ")
    tampered = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        prior_lock=prior_lock,
    )
    tampered_manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert tampered.terminal_status == "failed"
    assert tampered_manifest["prior_lock"]["sha256"] == sha256(prior_lock.read_bytes()).hexdigest()
    assert tampered_manifest["prior_lock"]["sha256"] != original_prior_sha256


def test_compile_retains_good_output_on_validation_failure_and_writes_failure_manifest(
    tmp_path: Path,
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)
    paths["catalog"].write_text(
        paths["catalog"]
        .read_text(encoding="utf-8")
        .replace('catalog_version: "1.0.0"', 'catalog_version: "bad"'),
        encoding="utf-8",
    )

    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert result.terminal_status == "failed"
    assert isinstance(result.error, CatalogValidationError)
    assert compiled_bytes(root) == before
    assert manifest["manifest_type"] == "catalog_compile_command"
    assert manifest["terminal_status"] == "failed"
    assert manifest["canonical_output_sha256"] == {}
    assert manifest["catalog_input_sha256"] is None
    assert manifest["unpaid_conformance"] is True
    assert "semantic_source" not in manifest


def test_interruption_before_publication_preserves_complete_prior_set(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)
    staged_parents: list[Path] = []

    def interrupt(staging: Path) -> None:
        staged_parents.append(staging.parent)
        raise KeyboardInterrupt

    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        before_publish=interrupt,
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert result.terminal_status == "interrupted"
    assert result.exit_code == 130
    assert compiled_bytes(root) == before
    assert manifest["terminal_status"] == "interrupted"
    assert manifest["canonical_output_sha256"] == {}
    assert staged_parents == [root.parent]
    assert not list(root.parent.glob(".catalog-compile-stage-*"))


def test_interruption_during_windows_safe_directory_swap_restores_prior_set(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)

    def interrupt_after_backup(_: Path) -> None:
        raise KeyboardInterrupt

    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        after_backup=interrupt_after_backup,
    )

    assert result.terminal_status == "interrupted"
    assert compiled_bytes(root) == before
    assert not list(root.parent.glob(".catalog-compile-backup-*"))
    verify_compiled_catalog(paths["output"], paths["lock"], catalog_path=paths["catalog"])


@pytest.mark.parametrize(
    ("hook_name", "exception_type", "expected_status"),
    [
        ("before_publish", RuntimeError, "failed"),
        ("after_backup", RuntimeError, "failed"),
        ("after_live", RuntimeError, "failed"),
        ("before_publish", _SimulatedTermination, "interrupted"),
        ("after_backup", _SimulatedTermination, "interrupted"),
        ("after_live", _SimulatedTermination, "interrupted"),
    ],
)
def test_every_publish_hook_restores_prior_catalog_and_writes_typed_manifest(
    tmp_path: Path,
    hook_name: str,
    exception_type: type[BaseException],
    expected_status: str,
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    prior_lock = compile_paths(root)["lock"].read_bytes()
    paths = compile_paths(root)
    observed: list[tuple[bool, bool, bool]] = []

    def abort(_: Path) -> None:
        observed.append(
            (
                (root / "catalog.yaml").exists(),
                bool(list(root.parent.glob(".catalog-compile-backup-*"))),
                bool(list(root.parent.glob(".catalog-compile-stage-*"))),
            )
        )
        raise exception_type("injected publish failure")

    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        **{hook_name: abort},
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert result.terminal_status == expected_status
    assert manifest["terminal_status"] == expected_status
    assert compiled_bytes(root) == before
    assert paths["lock"].read_bytes() == prior_lock
    assert observed == [
        {
            "before_publish": (True, False, True),
            "after_backup": (False, True, True),
            "after_live": (True, True, False),
        }[hook_name]
    ]
    assert not list(root.parent.glob(".catalog-compile-stage-*"))
    assert not list(root.parent.glob(".catalog-compile-backup-*"))
    assert not list(root.parent.glob(".catalog-compile-transaction-*.json"))


def test_interrupt_after_first_directory_move_restores_prior_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)
    original_replace = os.replace
    move_count = 0

    def interrupt_after_move(source: str | Path, destination: str | Path) -> None:
        nonlocal move_count
        original_replace(source, destination)
        move_count += 1
        if move_count == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr("memrelay_eval.catalog.compiler.os.replace", interrupt_after_move)
    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )

    assert result.terminal_status == "interrupted"
    assert compiled_bytes(root) == before
    assert not list(root.parent.glob(".catalog-compile-backup-*"))


@pytest.mark.parametrize("hook_name", ("after_backup", "after_live"))
def test_process_termination_between_directory_moves_recovers_prior_catalog(
    tmp_path: Path, hook_name: str
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)
    prior_lock = paths["lock"].read_bytes()
    code = f"""
import os
from pathlib import Path
from memrelay_eval.catalog.compiler import compile_catalog

root = Path({str(root)!r})

def terminate(_: Path) -> None:
    os._exit(73)

compile_catalog(
    root / "catalog.yaml",
    output_dir=root / "generated",
    lock_path=root / "catalog-lock.json",
    {hook_name}=terminate,
)
"""

    completed = subprocess.run([sys.executable, "-c", code], check=False)

    assert completed.returncode == 73
    if hook_name == "after_backup":
        assert not root.exists()
    else:
        assert root.exists()
    verify_compiled_catalog(paths["output"], paths["lock"], catalog_path=paths["catalog"])
    assert compiled_bytes(root) == before
    assert paths["lock"].read_bytes() == prior_lock
    assert not list(root.parent.glob(".catalog-compile-stage-*"))
    assert not list(root.parent.glob(".catalog-compile-backup-*"))
    assert not list(root.parent.glob(".catalog-compile-transaction-*.json"))


def test_recovery_validates_prior_backup_with_its_sealed_runtime_reference(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    runtime_lock = root / "runtime-lock.json"
    runtime_lock.write_text('{"runtime":"A"}', encoding="utf-8")
    first = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        runtime_lock=runtime_lock,
    )
    assert first.terminal_status == "succeeded"
    prior_lock = paths["lock"].read_bytes()
    runtime_lock.write_text('{"runtime":"B"}', encoding="utf-8")
    code = f"""
import os
from pathlib import Path
from memrelay_eval.catalog.compiler import compile_catalog

root = Path({str(root)!r})

def terminate(_: Path) -> None:
    os._exit(73)

compile_catalog(
    root / "catalog.yaml",
    output_dir=root / "generated",
    lock_path=root / "catalog-lock.json",
    runtime_lock=root / "runtime-lock.json",
    after_backup=terminate,
)
"""

    crashed = subprocess.run([sys.executable, "-c", code], check=False)
    recovered = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        runtime_lock=runtime_lock,
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    lock = json.loads(paths["lock"].read_text(encoding="utf-8"))

    assert crashed.returncode == 73
    assert recovered.terminal_status == "succeeded"
    assert manifest["recovery_status"] == "restored_prior"
    assert paths["lock"].read_bytes() != prior_lock
    assert lock["runtime_lock"]["sha256"] != json.loads(prior_lock)["runtime_lock"]["sha256"]
    assert not list(root.parent.glob(".catalog-compile-stage-*"))
    assert not list(root.parent.glob(".catalog-compile-backup-*"))
    assert not list(root.parent.glob(".catalog-compile-transaction-*.json"))


def test_recovery_fails_closed_for_unowned_or_ambiguous_publication_state(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    orphan = root.parent / ".catalog-compile-backup-unowned"
    orphan.mkdir()

    with pytest.raises(CatalogRecoveryError, match="ownership journal"):
        verify_compiled_catalog(paths["output"], paths["lock"], catalog_path=paths["catalog"])


def test_recovery_never_guesses_between_multiple_transaction_journals(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    for transaction_id in ("a" * 32, "b" * 32):
        (root.parent / f".catalog-compile-transaction-{transaction_id}.json").write_text(
            "{}", encoding="utf-8"
        )

    with pytest.raises(CatalogRecoveryError, match="multiple publication journals"):
        verify_compiled_catalog(paths["output"], paths["lock"], catalog_path=paths["catalog"])


def test_recovery_refuses_a_backup_with_source_bytes_that_do_not_match_its_lock(
    tmp_path: Path,
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    paths = compile_paths(root)
    transaction_id = "a" * 32
    backup = root.parent / f".catalog-compile-backup-{transaction_id}"
    staging = root.parent / f".catalog-compile-stage-{transaction_id}"
    compiler_module._write_transaction(
        root,
        transaction_id=transaction_id,
        staging=staging,
        backup=backup,
        source_sha256=compiler_module._catalog_source_sha256(paths["catalog"]),
        backup_lock_sha256=sha256(paths["lock"].read_bytes()).hexdigest(),
        runtime_lock={"path": None, "sha256": None},
        output_dir="generated",
    )
    os.replace(root, backup)
    (backup / "catalog.yaml").write_text("corrupted: source\n", encoding="utf-8")

    with pytest.raises(CatalogCompileError):
        verify_compiled_catalog(
            root / "generated",
            root / "catalog-lock.json",
            catalog_path=root / "catalog.yaml",
        )

    assert not root.exists()
    assert backup.exists()
    assert (backup / "catalog-lock.json").exists()
    assert list(root.parent.glob(".catalog-compile-transaction-*.json"))


def test_cross_process_compile_lock_has_one_writer_and_releases_after_winner_crash(
    tmp_path: Path,
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)
    ready = tmp_path / "winner-ready"
    code = f"""
import time
from pathlib import Path
from memrelay_eval.catalog.compiler import compile_catalog

root = Path({str(root)!r})
ready = Path({str(ready)!r})

def hold(_: Path) -> None:
    ready.write_text("locked", encoding="utf-8")
    time.sleep(60)

compile_catalog(
    root / "catalog.yaml",
    output_dir=root / "generated",
    lock_path=root / "catalog-lock.json",
    before_publish=hold,
)
"""
    winner = subprocess.Popen([sys.executable, "-c", code])
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists()
        loser = subprocess.run(
            [
                sys.executable,
                "-m",
                "memrelay_eval.cli.main",
                "compile-catalog",
                "--catalog",
                str(paths["catalog"]),
                "--output-dir",
                str(paths["output"]),
                "--lock",
                str(paths["lock"]),
                "--manifest",
                str(paths["manifest"]),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert loser.returncode == 1
        assert "already owned by another process" in loser.stdout
        assert compiled_bytes(root) == before
    finally:
        winner.terminate()
        winner.wait(timeout=15)

    recovery_result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
    )
    recovery_manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert recovery_result.terminal_status == "succeeded"
    assert recovery_manifest["recovery_status"] == "discarded_unpublished"
    assert compiled_bytes(root) == before


def test_staging_is_a_same_volume_sibling_on_windows_and_posix(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    observed: list[tuple[Path, int, str]] = []

    def inspect_staging(staging: Path) -> None:
        observed.append(
            (staging.parent, staging.stat().st_dev, os.path.splitdrive(str(staging.resolve()))[0])
        )
        raise KeyboardInterrupt

    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        before_publish=inspect_staging,
    )

    assert result.terminal_status == "interrupted"
    assert observed == [
        (root.parent, root.stat().st_dev, os.path.splitdrive(str(root.resolve()))[0])
    ]


@pytest.mark.parametrize("collision", ("catalog.yaml", "catalog-lock.json", "generated/tasks.json"))
def test_manifest_path_cannot_replace_catalog_lock_or_generated_output(
    tmp_path: Path, collision: str
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)

    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=root / collision,
    )

    assert result.terminal_status == "failed"
    assert result.error is not None
    assert compiled_bytes(root) == before


def test_manifest_path_cannot_replace_supplied_runtime_lock(tmp_path: Path) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    before = compiled_bytes(root)
    paths = compile_paths(root)
    runtime_lock = root / "runtime-lock.json"
    runtime_lock.write_text('{"runtime":"pinned"}', encoding="utf-8")
    original_runtime_lock = runtime_lock.read_bytes()

    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=runtime_lock,
        runtime_lock=runtime_lock,
    )

    assert result.terminal_status == "failed"
    assert runtime_lock.read_bytes() == original_runtime_lock
    assert compiled_bytes(root) == before


@pytest.mark.parametrize("terminal", ("succeeded", "failed", "interrupted"))
def test_command_manifests_are_complete_and_redacted(tmp_path: Path, terminal: str) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    before_publish: Callable[[Path], None] | None = None
    if terminal == "failed":
        paths["catalog"].write_text("schema_version: 'bad'\n", encoding="utf-8")
    elif terminal == "interrupted":

        def before_publish(_: Path) -> None:
            raise KeyboardInterrupt

    result = compile_catalog_command(
        paths["catalog"],
        output_dir=paths["output"],
        lock_path=paths["lock"],
        manifest_path=paths["manifest"],
        before_publish=before_publish,
    )
    raw = paths["manifest"].read_bytes()
    manifest = json.loads(raw.decode("utf-8"))

    assert result.terminal_status == terminal
    assert {
        "catalog_source_sha256",
        "catalog_input_sha256",
        "canonical_output_sha256",
        "runtime_lock",
        "prior_lock",
        "protocol_id",
        "protocol_ids",
        "terminal_status",
        "schema_versions",
        "generator_version",
        "recovery_status",
        "unpaid_conformance",
        "digest",
    } <= set(manifest)
    assert manifest["terminal_status"] == terminal
    assert manifest["unpaid_conformance"] is True
    assert str(tmp_path).replace("\\", "/") not in raw.decode("utf-8")
    assert "COPILOT_TOKEN" not in raw.decode("utf-8")
    if terminal == "succeeded":
        assert manifest["catalog_input_sha256"] is not None
        assert manifest["canonical_output_sha256"]["catalog-lock.json"]
    else:
        assert manifest["canonical_output_sha256"] == {}


def test_compile_uses_no_network_or_provider_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = catalog_root(tmp_path)
    paths = compile_paths(root)
    monkeypatch.setenv("COPILOT_TOKEN", "must-not-persist")

    def network_forbidden(*_: object, **__: object) -> None:
        raise AssertionError("catalog compilation must remain offline")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    compile_in_process(root)

    all_output = b"".join(compiled_bytes(root).values())
    assert b"must-not-persist" not in all_output
    verify_compiled_catalog(paths["output"], paths["lock"], catalog_path=paths["catalog"])


def test_traceability_is_catalog_relative_and_defers_fixture_and_eligibility_gates(
    tmp_path: Path,
) -> None:
    root = catalog_root(tmp_path)
    compile_in_process(root)
    paths = compile_paths(root)
    fixture_manifest = json.loads(
        (paths["output"] / "fixture-manifest.json").read_text(encoding="utf-8")
    )
    traceability = json.loads((paths["output"] / "traceability.json").read_text(encoding="utf-8"))

    record = traceability["traceability"][0]
    location_documents = [
        record["scenario_source"],
        *record["field_locations"].values(),
        *[
            source
            for reference in record["references"]
            for source in (reference["scenario_source"], reference["catalog_source"])
        ],
    ]
    assert all(location["path"] == "catalog.yaml" for location in location_documents)
    assert all("\\" not in location["path"] for location in location_documents)
    assert fixture_manifest["fixture_content_validation"] == "not_performed"
    assert record["fixture_content_validation"] == "not_performed"
    assert record["eligibility_evaluation"] == "not_performed"
