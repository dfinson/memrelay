from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.catalog.canonical import attach_digest, canonical_bytes
from memrelay_eval.catalog.compiler import (
    CatalogCompileError,
    compile_catalog,
    verify_compiled_catalog,
)

EVALUATION_ROOT = Path(__file__).parents[3]
CHECKED_CATALOG_ROOT = EVALUATION_ROOT / "catalog"
GENERATED_FILENAMES = (
    "tasks.json",
    "assignment-inputs.json",
    "fixture-manifest.json",
    "traceability.json",
)


def copy_checked_catalog(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    shutil.copytree(CHECKED_CATALOG_ROOT, root)
    return root


def test_checked_in_artifacts_verify_and_regenerate_byte_identically(tmp_path: Path) -> None:
    checked_output = CHECKED_CATALOG_ROOT / "generated"
    checked_lock = CHECKED_CATALOG_ROOT / "catalog-lock.json"
    verify_compiled_catalog(
        checked_output,
        checked_lock,
        catalog_path=CHECKED_CATALOG_ROOT / "catalog.yaml",
    )
    root = copy_checked_catalog(tmp_path)
    compile_catalog(
        root / "catalog.yaml",
        output_dir=root / "generated",
        lock_path=root / "catalog-lock.json",
    )

    for filename in GENERATED_FILENAMES:
        assert (root / "generated" / filename).read_bytes() == (
            checked_output / filename
        ).read_bytes()
    assert (root / "catalog-lock.json").read_bytes() == checked_lock.read_bytes()


@pytest.mark.parametrize(
    "relative_path",
    [
        *(f"generated/{filename}" for filename in GENERATED_FILENAMES),
        "catalog-lock.json",
    ],
)
def test_verification_rejects_a_byte_tamper_in_each_output_or_lock(
    tmp_path: Path, relative_path: str
) -> None:
    root = copy_checked_catalog(tmp_path)
    target = root / relative_path
    target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(CatalogCompileError):
        verify_compiled_catalog(
            root / "generated",
            root / "catalog-lock.json",
            catalog_path=root / "catalog.yaml",
        )


def test_verification_rejects_a_consistently_rewritten_artifact_and_lock(tmp_path: Path) -> None:
    root = copy_checked_catalog(tmp_path)
    task_path = root / "generated" / "tasks.json"
    task_document = json.loads(task_path.read_text(encoding="utf-8"))
    task_document["tasks"][0]["task_input"]["title"] = "Tampered but canonical"
    task_document["tasks"][0] = attach_digest(task_document["tasks"][0])
    task_document = attach_digest(task_document)
    task_bytes = canonical_bytes(task_document)
    task_path.write_bytes(task_bytes)

    lock_path = root / "catalog-lock.json"
    lock_document = json.loads(lock_path.read_text(encoding="utf-8"))
    lock_document["generated_outputs"]["tasks.json"]["sha256"] = sha256(task_bytes).hexdigest()
    lock_path.write_bytes(canonical_bytes(attach_digest(lock_document)))

    with pytest.raises(CatalogCompileError, match="does not match deterministic compilation"):
        verify_compiled_catalog(
            root / "generated",
            lock_path,
            catalog_path=root / "catalog.yaml",
        )


def test_verification_requires_the_trusted_runtime_lock_reference(tmp_path: Path) -> None:
    root = copy_checked_catalog(tmp_path)
    runtime_lock = root / "runtime-lock.json"
    runtime_lock.write_text('{"runtime":"pinned"}', encoding="utf-8")
    compile_catalog(
        root / "catalog.yaml",
        output_dir=root / "generated",
        lock_path=root / "catalog-lock.json",
        runtime_lock=runtime_lock,
    )

    verify_compiled_catalog(
        root / "generated",
        root / "catalog-lock.json",
        catalog_path=root / "catalog.yaml",
        runtime_lock=runtime_lock,
    )
    with pytest.raises(CatalogCompileError):
        verify_compiled_catalog(
            root / "generated",
            root / "catalog-lock.json",
            catalog_path=root / "catalog.yaml",
        )


def test_catalog_has_one_canonicalizer_and_no_sdk_or_network_imports() -> None:
    catalog_source = EVALUATION_ROOT / "src" / "memrelay_eval" / "catalog"
    source_by_file = {
        path.name: path.read_text(encoding="utf-8") for path in catalog_source.glob("*.py")
    }

    assert source_by_file["canonical.py"].count("import rfc8785") == 1
    assert all(
        "rfc8785" not in source
        for filename, source in source_by_file.items()
        if filename != "canonical.py"
    )
    assert all("json.dumps(" not in source for source in source_by_file.values())
    assert all("sort_keys=True" not in source for source in source_by_file.values())
    assert all(
        forbidden not in source.casefold()
        for source in source_by_file.values()
        for forbidden in ("inspect", "copilot", "openai", "socket", "requests", "httpx")
    )
