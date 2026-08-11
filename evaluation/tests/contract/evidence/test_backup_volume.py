from __future__ import annotations

import pytest
from memrelay_eval.cli.main import build_parser
from memrelay_eval.domain.errors import BackupConformanceError
from memrelay_eval.evidence.backup import preflight_backup_root


def test_preflight_rejects_same_volume_alias_and_accepts_independent_root(tmp_path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    with pytest.raises(BackupConformanceError) as same_volume:
        preflight_backup_root(source, target, volume_identity=lambda _: "volume-guid:one")
    assert same_volume.value.code == "backup_root_not_second_volume"

    assert preflight_backup_root(
        source,
        target,
        volume_identity=lambda path: (
            "volume-guid:source" if path == source else "volume-guid:target"
        ),
    ) == ("volume-guid:source", "volume-guid:target")


def test_terminal_backup_command_requires_explicit_evidence_authorities() -> None:
    args = build_parser().parse_args(
        [
            "backup-terminal",
            "--backup-root",
            "E:\\evidence",
            "--ledger",
            "C:\\ledger.sqlite",
            "--run-id",
            "run-00000000-0000-4000-8000-000000000000",
            "--attempt-id",
            "attempt-00000000-0000-4000-8000-000000000000",
        ]
    )

    assert args.command == "backup-terminal"
    assert args.artifacts_root == "artifacts"
