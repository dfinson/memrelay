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

    assert bootstrap.command == "bootstrap"
    assert qualification.command == "lock-models"
    assert qualification.credit_cap == 8
