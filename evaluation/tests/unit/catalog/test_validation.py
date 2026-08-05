from __future__ import annotations

from pathlib import Path

import pytest
from memrelay_eval.catalog.validation import (
    CatalogValidationError,
    validate_catalog,
)
from memrelay_eval.cli.main import build_parser


def opaque(prefix: str, value: str) -> str:
    return f"{prefix}_{value * 32}"


def valid_catalog() -> str:
    return f"""\
schema_version: "1.0.0"
catalog_version: "1.0.0"
catalog_id: "{opaque("cat", "a")}"
references:
  protocols: ["{opaque("protocol", "b")}"]
  fixtures:
    - id: "{opaque("fixture", "c")}"
      source_path: "fixtures/example.txt"
      sha256: "{"d" * 64}"
      media_type: "text/plain"
      license: "CC0-1.0"
      provenance: "synthetic"
      repository_revision: null
      extraction_path: "example.txt"
      data_classification: "synthetic"
      redistribution_policy: "allowed"
  risks: ["{opaque("risk", "e")}"]
  gates: ["{opaque("gate", "f")}"]
  endpoints: ["{opaque("endpoint", "1")}"]
  evidence: ["{opaque("evidence", "2")}"]
  claims: ["{opaque("claim", "3")}"]
  graders: ["{opaque("grader", "4")}"]
scenarios:
  - id: "{opaque("scenario", "5")}"
    title: "Verify synthetic catalog validation"
    protocol_ids: ["{opaque("protocol", "b")}"]
    priority: "P0"
    owner: "evaluation"
    preconditions: ["clean evaluator environment"]
    fixture_refs: ["{opaque("fixture", "c")}"]
    injected_conditions:
      - id: "{opaque("condition", "6")}"
        description: "A single synthetic condition"
    procedure:
      id: "{opaque("procedure", "7")}"
      description: "Validate the authored YAML"
    expected_evidence: ["{opaque("evidence", "2")}"]
    pass_criteria:
      id: "{opaque("verdict", "8")}"
      assertion: "Validation reports no diagnostics"
    allowed_retries: 0
    risk_ids: ["{opaque("risk", "e")}"]
    gate_ids: ["{opaque("gate", "f")}"]
    endpoint_ids: ["{opaque("endpoint", "1")}"]
    claim_ids: ["{opaque("claim", "3")}"]
    data_classification: "synthetic"
    network_policy: "deny"
    resource_limits:
      wall_seconds: 30
      memory_mb: 256
    grader_ref: "{opaque("grader", "4")}"
"""


def write_catalog(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def single_condition_block() -> str:
    return "\n".join(
        (
            "injected_conditions:",
            f'      - id: "{opaque("condition", "6")}"',
            '        description: "A single synthetic condition"',
        )
    )


def procedure_block() -> str:
    return "\n".join(
        (
            "procedure:",
            f'      id: "{opaque("procedure", "7")}"',
            '      description: "Validate the authored YAML"',
        )
    )


def pass_criteria_block() -> str:
    return "\n".join(
        (
            "pass_criteria:",
            f'      id: "{opaque("verdict", "8")}"',
            '      assertion: "Validation reports no diagnostics"',
        )
    )


def diagnostics_for(
    tmp_path: Path, content: str, *, prior_lock: Path | None = None
) -> list[object]:
    with pytest.raises(CatalogValidationError) as raised:
        validate_catalog(write_catalog(tmp_path, content), prior_lock=prior_lock)
    return list(raised.value.diagnostics)


def test_valid_catalog_is_schema_2020_12_and_semantically_closed(tmp_path: Path) -> None:
    result = validate_catalog(write_catalog(tmp_path, valid_catalog()))

    assert result.catalog["schema_version"] == "1.0.0"
    assert result.source_path == "catalog.yaml"


def test_pinned_schema_declares_json_schema_draft_2020_12() -> None:
    schema = (Path(__file__).parents[3] / "schemas" / "scenario.schema.json").read_text(
        encoding="utf-8"
    )

    assert '"$schema": "https://json-schema.org/draft/2020-12/schema"' in schema


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        (
            'protocol_ids: ["protocol_{}"]'.format("9" * 32),
            "CATALOG_UNRESOLVED_REFERENCE",
        ),
        (
            "\n".join(
                (
                    "injected_conditions:",
                    f'      - id: "{opaque("condition", "6")}"',
                    '        description: "one"',
                    f'      - id: "{opaque("condition", "9")}"',
                    '        description: "two"',
                )
            ),
            "CATALOG_SCHEMA",
        ),
        ('fixture_refs: ["fixture_{}"]'.format("9" * 32), "CATALOG_UNRESOLVED_REFERENCE"),
    ],
)
def test_invalid_atomic_or_referential_scenarios_fail_closed(
    tmp_path: Path, replacement: str, code: str
) -> None:
    content = valid_catalog()
    if replacement.startswith("protocol_ids"):
        content = content.replace(f'protocol_ids: ["{opaque("protocol", "b")}"]', replacement)
    elif replacement.startswith("injected_conditions"):
        content = content.replace(single_condition_block(), replacement)
    else:
        content = content.replace(f'fixture_refs: ["{opaque("fixture", "c")}"]', replacement)

    diagnostics = diagnostics_for(tmp_path, content)

    assert [diagnostic.code for diagnostic in diagnostics] == [code]
    assert diagnostics[0].path == "catalog.yaml"
    assert diagnostics[0].line is not None


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (
            valid_catalog().replace(
                'catalog_version: "1.0.0"', 'catalog_version: "1.0.0"\ncatalog_version: "1.0.1"'
            ),
            "CATALOG_DUPLICATE_KEY",
        ),
        (
            valid_catalog().replace(
                "scenarios:", "shared: &scenario\n  title: forbidden\nscenarios:\n  - *scenario\n"
            ),
            "CATALOG_YAML_FEATURE",
        ),
        (
            valid_catalog().replace(
                'title: "Verify synthetic catalog validation"', 'title: "${CATALOG_TITLE}"'
            ),
            "CATALOG_ENV_SUBSTITUTION",
        ),
    ],
)
def test_forbidden_yaml_features_have_source_located_diagnostics(
    tmp_path: Path, content: str, code: str
) -> None:
    diagnostics = diagnostics_for(tmp_path, content)

    assert [diagnostic.code for diagnostic in diagnostics] == [code]
    assert diagnostics[0].path == "catalog.yaml"
    assert diagnostics[0].line is not None
    assert diagnostics[0].column is not None


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        ("injected_conditions: []", "CATALOG_SCHEMA"),
        ("procedure: []", "CATALOG_SCHEMA"),
        ("pass_criteria: []", "CATALOG_SCHEMA"),
        ("resource_limits:\n      <<: {wall_seconds: 30, memory_mb: 256}", "CATALOG_YAML_FEATURE"),
    ],
)
def test_zero_atomic_parts_and_merge_keys_are_rejected(
    tmp_path: Path, replacement: str, code: str
) -> None:
    content = valid_catalog()
    if replacement.startswith("injected_conditions"):
        content = content.replace(single_condition_block(), replacement)
    elif replacement.startswith("procedure"):
        content = content.replace(procedure_block(), replacement)
    elif replacement.startswith("pass_criteria"):
        content = content.replace(pass_criteria_block(), replacement)
    else:
        content = content.replace(
            "resource_limits:\n      wall_seconds: 30\n      memory_mb: 256",
            replacement,
        )

    diagnostics = diagnostics_for(tmp_path, content)

    assert diagnostics[0].code == code


@pytest.mark.parametrize("literal", [".nan", ".inf", "-.inf"])
def test_non_finite_numbers_are_rejected_before_schema_validation(
    tmp_path: Path, literal: str
) -> None:
    diagnostics = diagnostics_for(
        tmp_path,
        valid_catalog().replace("wall_seconds: 30", f"wall_seconds: {literal}"),
    )

    assert [diagnostic.code for diagnostic in diagnostics] == ["CATALOG_NON_FINITE_NUMBER"]


def test_non_utf8_input_is_rejected(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_bytes(b"schema_version: '\xff'\n")

    with pytest.raises(CatalogValidationError) as raised:
        validate_catalog(catalog)

    assert raised.value.diagnostics[0].code == "CATALOG_ENCODING"


def test_duplicate_identifiers_are_rejected_across_reference_namespaces(tmp_path: Path) -> None:
    content = valid_catalog().replace(
        f'"{opaque("gate", "f")}"',
        f'"{opaque("risk", "e")}"',
        1,
    )

    diagnostics = diagnostics_for(tmp_path, content)

    assert "CATALOG_DUPLICATE_ID" in [diagnostic.code for diagnostic in diagnostics]


@pytest.mark.parametrize(
    "replacement",
    [
        opaque("arm", "9"),
        opaque("treatment", "9"),
    ],
)
def test_treatment_revealing_identifiers_are_rejected(tmp_path: Path, replacement: str) -> None:
    diagnostics = diagnostics_for(
        tmp_path,
        valid_catalog().replace(opaque("grader", "4"), replacement, 2),
    )

    assert diagnostics[0].code == "CATALOG_SCHEMA"


@pytest.mark.parametrize("source_path", ["../../outside.txt", "C:/outside.txt"])
def test_fixture_paths_must_remain_relative_to_the_fixture_root(
    tmp_path: Path, source_path: str
) -> None:
    diagnostics = diagnostics_for(
        tmp_path,
        valid_catalog().replace("fixtures/example.txt", source_path),
    )

    assert diagnostics[0].code == "CATALOG_SCHEMA"


def test_non_scalar_mapping_keys_are_typed_yaml_diagnostics(tmp_path: Path) -> None:
    diagnostics = diagnostics_for(tmp_path, "? [not, a, scalar]\n: value\n")

    assert diagnostics[0].code == "CATALOG_YAML_FEATURE"
    assert diagnostics[0].line == 1


@pytest.mark.parametrize(
    ("field", "namespace", "identifier"),
    [
        ("fixture_refs", "fixtures", opaque("fixture", "9")),
        ("risk_ids", "risks", opaque("risk", "9")),
        ("gate_ids", "gates", opaque("gate", "9")),
        ("endpoint_ids", "endpoints", opaque("endpoint", "9")),
        ("expected_evidence", "evidence", opaque("evidence", "9")),
        ("claim_ids", "claims", opaque("claim", "9")),
    ],
)
def test_every_reference_namespace_requires_closure(
    tmp_path: Path, field: str, namespace: str, identifier: str
) -> None:
    original = {
        "fixture_refs": opaque("fixture", "c"),
        "risk_ids": opaque("risk", "e"),
        "gate_ids": opaque("gate", "f"),
        "endpoint_ids": opaque("endpoint", "1"),
        "expected_evidence": opaque("evidence", "2"),
        "claim_ids": opaque("claim", "3"),
    }[field]
    content = valid_catalog().replace(
        f'{field}: ["{original}"]',
        f'{field}: ["{identifier}"]',
    )

    diagnostics = diagnostics_for(tmp_path, content)

    assert diagnostics[0].code == "CATALOG_UNRESOLVED_REFERENCE"
    assert namespace in diagnostics[0].message


def test_validation_is_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def network_forbidden(*_: object, **__: object) -> None:
        raise AssertionError("validation must not make network calls")

    monkeypatch.setattr("socket.create_connection", network_forbidden)

    assert validate_catalog(write_catalog(tmp_path, valid_catalog())).catalog["scenarios"]


def test_cli_validates_without_compiling_or_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = write_catalog(tmp_path, valid_catalog())
    args = build_parser().parse_args(["validate-catalog", "--catalog", str(catalog)])

    assert args.handler(args) == 0
    assert "valid (none change)" in capsys.readouterr().out


def test_golden_duplicate_key_diagnostic_is_stable() -> None:
    golden_root = Path(__file__).parents[2] / "golden" / "catalog"
    source = golden_root / "invalid-duplicate-key.yaml"
    expected = (golden_root / "invalid-duplicate-key.txt").read_text(encoding="utf-8").strip()

    with pytest.raises(CatalogValidationError) as raised:
        validate_catalog(source)

    assert str(raised.value) == f"evaluation/tests/golden/catalog/{expected}"


def test_invalid_catalog_never_creates_output_or_mutates_lock(tmp_path: Path) -> None:
    catalog = write_catalog(
        tmp_path,
        valid_catalog().replace(
            f'id: "{opaque("scenario", "5")}"',
            f'id: "{opaque("scenario", "5")}"\n  - id: "{opaque("scenario", "5")}"',
            1,
        ),
    )
    lock = tmp_path / "catalog-lock.json"
    lock.write_text('{"catalog_version": "1.0.0"}', encoding="utf-8")

    with pytest.raises(CatalogValidationError):
        validate_catalog(catalog, prior_lock=lock)

    assert lock.read_text(encoding="utf-8") == '{"catalog_version": "1.0.0"}'
    assert sorted(path.name for path in tmp_path.iterdir()) == ["catalog-lock.json", "catalog.yaml"]


def test_version_policy_requires_exact_semantic_increment(tmp_path: Path) -> None:
    original = write_catalog(tmp_path, valid_catalog())
    prior = tmp_path / "catalog-lock.json"
    prior.write_text(
        '{"catalog_version":"1.0.0","semantic_source":'
        + validate_catalog(original).semantic_json
        + "}",
        encoding="utf-8",
    )

    content = (
        valid_catalog()
        .replace('catalog_version: "1.0.0"', 'catalog_version: "1.0.1"')
        .replace(
            'title: "Verify synthetic catalog validation"', 'title: "Validate catalog semantics"'
        )
    )
    assert (
        validate_catalog(write_catalog(tmp_path, content), prior_lock=prior).change_kind
        == "content"
    )

    additive = content.replace(
        "scenarios:",
        f"""scenarios:
  - id: "{opaque("scenario", "9")}"
    title: "Additional synthetic scenario"
    protocol_ids: ["{opaque("protocol", "b")}"]
    priority: "P1"
    owner: "evaluation"
    preconditions: ["clean evaluator environment"]
    fixture_refs: ["{opaque("fixture", "c")}"]
    injected_conditions:
      - id: "{opaque("condition", "a")}"
        description: "One condition"
    procedure:
      id: "{opaque("procedure", "b")}"
      description: "Validate"
    expected_evidence: ["{opaque("evidence", "2")}"]
    pass_criteria:
      id: "{opaque("verdict", "c")}"
      assertion: "Passes"
    allowed_retries: 0
    risk_ids: ["{opaque("risk", "e")}"]
    gate_ids: ["{opaque("gate", "f")}"]
    endpoint_ids: ["{opaque("endpoint", "1")}"]
    claim_ids: ["{opaque("claim", "3")}"]
    data_classification: "synthetic"
    network_policy: "deny"
    resource_limits: {{wall_seconds: 30, memory_mb: 256}}
    grader_ref: "{opaque("grader", "4")}"
""",
    ).replace('catalog_version: "1.0.1"', 'catalog_version: "1.1.0"')
    assert (
        validate_catalog(write_catalog(tmp_path, additive), prior_lock=prior).change_kind
        == "additive"
    )

    breaking = (
        valid_catalog()
        .replace('catalog_version: "1.0.0"', 'catalog_version: "2.0.0"')
        .replace('priority: "P0"', 'priority: "P1"')
    )
    assert (
        validate_catalog(write_catalog(tmp_path, breaking), prior_lock=prior).change_kind
        == "breaking"
    )


def test_version_policy_rejects_unrelated_or_insufficient_movement(tmp_path: Path) -> None:
    source = write_catalog(tmp_path, valid_catalog())
    prior = tmp_path / "catalog-lock.json"
    prior.write_text(
        '{"catalog_version":"1.0.0","semantic_source":'
        + validate_catalog(source).semantic_json
        + "}",
        encoding="utf-8",
    )

    content = valid_catalog().replace('priority: "P0"', 'priority: "P1"')
    diagnostics = diagnostics_for(tmp_path, content, prior_lock=prior)
    assert [diagnostic.code for diagnostic in diagnostics] == ["CATALOG_VERSION_POLICY"]


def test_version_policy_accepts_only_unchanged_version_for_no_change(tmp_path: Path) -> None:
    source = write_catalog(tmp_path, valid_catalog())
    prior = tmp_path / "catalog-lock.json"
    prior.write_text(
        '{"catalog_version":"1.0.0","semantic_source":'
        + validate_catalog(source).semantic_json
        + "}",
        encoding="utf-8",
    )

    assert validate_catalog(source, prior_lock=prior).change_kind == "none"
    diagnostics = diagnostics_for(
        tmp_path,
        valid_catalog().replace('catalog_version: "1.0.0"', 'catalog_version: "1.0.1"'),
        prior_lock=prior,
    )
    assert [diagnostic.code for diagnostic in diagnostics] == ["CATALOG_VERSION_POLICY"]
