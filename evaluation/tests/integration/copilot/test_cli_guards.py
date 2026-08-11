from __future__ import annotations

from memrelay_eval.cli.main import build_parser


def test_live_commands_require_explicit_operator_input() -> None:
    parser = build_parser()

    bootstrap = parser.parse_args(["bootstrap", "--backup-root", "E:\\evidence"])
    qualification = parser.parse_args(
        [
            "lock-models",
            "--credit-cap",
            "8",
            "--token-cap",
            "8000",
            "--active-seconds-cap",
            "120",
            "--wall-seconds-cap",
            "180",
        ]
    )
    sealing = parser.parse_args(
        [
            "seal-reproduction-bundle",
            "--parquet-root",
            "artifacts/parquet",
            "--dataset-version",
            "parquet-v1.0.0-fixture",
            "--queries",
            "retained/queries.json",
            "--grader-result",
            "retained/grader.json",
            "--normalized-evidence",
            "retained/evidence.json",
            "--protocol-sha256",
            "a" * 64,
            "--runtime-lock",
            "uv.lock",
            "--output-root",
            "retained/reproduction",
        ]
    )

    assert bootstrap.command == "bootstrap"
    assert qualification.command == "lock-models"
    assert qualification.credit_cap == 8
    assert sealing.command == "seal-reproduction-bundle"
    assert sealing.handler.__name__ == "seal_reproduction_bundle_command"
