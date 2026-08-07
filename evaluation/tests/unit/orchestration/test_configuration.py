from __future__ import annotations

import json

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.canonical import canonical_bytes, verify_digest
from memrelay_eval.cli.main import main
from memrelay_eval.domain.errors import (
    AmbiguousConfigurationKeyError,
    InvalidConfigurationError,
    SecretConfigurationError,
    UnknownConfigurationKeyError,
)
from memrelay_eval.orchestration.configuration import (
    load_evaluator_toml,
    persist_effective_configuration,
    resolve_effective_configuration,
)


def test_configuration_precedence_is_exact_for_every_source() -> None:
    configuration = resolve_effective_configuration(
        cli={"stage": "cli", "timeout_seconds": 12},
        protocol_stage={"stage": "protocol", "timeout_seconds": 24, "max_concurrency": 2},
        evaluator_file={
            "stage": "file",
            "timeout_seconds": 36,
            "max_concurrency": 3,
            "network_policy": {"mode": "file"},
        },
        safe_defaults={
            "stage": "default",
            "timeout_seconds": 48,
            "max_concurrency": 4,
            "network_policy": {"mode": "default"},
        },
    )

    assert dict(configuration.values) == {
        "max_concurrency": 2,
        "network_policy": {"mode": "file"},
        "stage": "cli",
        "timeout_seconds": 12,
    }
    assert dict(configuration.provenance) == {
        "max_concurrency": "protocol_stage",
        "network_policy": "evaluator_file",
        "stage": "cli",
        "timeout_seconds": "cli",
    }


def test_absent_sources_fall_back_to_safe_defaults() -> None:
    configuration = resolve_effective_configuration(
        cli={"stage": None},
        protocol_stage={},
        evaluator_file={},
        safe_defaults={"stage": "default", "timeout_seconds": 8},
    )

    assert dict(configuration.values) == {"stage": "default", "timeout_seconds": 8}
    assert set(configuration.provenance.values()) == {"safe_default"}


@pytest.mark.parametrize(
    ("source", "value", "error"),
    [
        ("cli", {"unknown": "value"}, UnknownConfigurationKeyError),
        ("protocol_stage", {"stage.rule": "bad"}, AmbiguousConfigurationKeyError),
        ("evaluator_file", {"timeout_seconds": 0}, InvalidConfigurationError),
        ("safe_defaults", {"max_concurrency": False}, InvalidConfigurationError),
        ("evaluator_file", {"endpoints": {"api_key": "not allowed"}}, SecretConfigurationError),
        ("evaluator_file", {"endpoints": {"root_path": "mutable"}}, InvalidConfigurationError),
        (
            "evaluator_file",
            {
                "credential_references": [
                    {"variable_name": "OPENAI_API_KEY", "target_process": "analysis"}
                ]
            },
            InvalidConfigurationError,
        ),
    ],
)
def test_unknown_ambiguous_and_invalid_configuration_is_rejected(
    source: str, value: dict[str, object], error: type[Exception]
) -> None:
    kwargs: dict[str, object] = {
        "cli": {},
        "protocol_stage": {},
        "evaluator_file": {},
        "safe_defaults": {"stage": "default"},
    }
    kwargs[source] = value

    with pytest.raises(error):
        resolve_effective_configuration(**kwargs)  # type: ignore[arg-type]


def test_toml_configuration_and_cli_parser_use_explicit_sources(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "evaluator.toml"
    source.write_text(
        'stage = "file"\ntimeout_seconds = 20\n[network_policy]\nmode = "deny"\n',
        encoding="utf-8",
    )

    loaded = load_evaluator_toml(source)
    assert loaded["stage"] == "file"
    assert (
        main(
            [
                "effective-config",
                "--config",
                str(source),
                "--stage",
                "cli",
                "--credential-reference",
                "OPENAI_API_KEY:memrelay_framework_daemon",
            ]
        )
        == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document["fields"]["stage"]["value"] == "cli"
    assert document["fields"]["stage"]["provenance"] == "cli"
    marker = document["fields"]["credential_references"]["value"][0]
    assert marker == {
        "kind": "credential_reference",
        "redacted": True,
        "target_process": "memrelay_framework_daemon",
        "variable_name": "OPENAI_API_KEY",
    }


def test_effective_configuration_artifact_is_canonical_redacted_and_provenanced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "canary-secret-value-never-persisted"
    monkeypatch.setenv("MEMRELAY_EVAL_STAGE", secret)
    configuration = resolve_effective_configuration(
        evaluator_file={
            "stage": "file",
            "credential_references": [
                {
                    "variable_name": "OPENAI_API_KEY",
                    "target_process": "memrelay_framework_daemon",
                }
            ],
        },
        safe_defaults={"timeout_seconds": 10},
    )
    store = InMemoryArtifactStore()
    artifact = persist_effective_configuration(configuration, store)
    raw = store.open_verified(artifact.artifact)
    document = json.loads(raw)

    assert secret.encode() not in raw
    assert verify_digest(document)
    assert raw == canonical_bytes(document)
    assert all("provenance" in field for field in document["fields"].values())
    assert document["fields"]["credential_references"]["value"][0]["redacted"] is True


def test_ordinary_secret_values_are_rejected_without_echoing_them() -> None:
    secret = "canary-secret-value-never-persisted"

    with pytest.raises(SecretConfigurationError) as error:
        resolve_effective_configuration(evaluator_file={"stage": secret})

    assert secret not in str(error.value)
