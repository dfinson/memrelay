from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from memrelay_eval.catalog.compiler import (
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
        "protocol_id",
        "protocol_ids",
        "terminal_status",
        "schema_versions",
        "generator_version",
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
