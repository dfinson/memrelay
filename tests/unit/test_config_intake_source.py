"""Unit tests for ``IngestConfig.intake_source`` (#11 additive daemon intake knob).

The knob selects the daemon poller's per-session capture: ``"replay"`` (default) keeps #8's
periodic ``run_observe`` capture verbatim — so zero-config behaviour is byte-identical — and
``"file_watch"`` opts into the live tail. It is a plain additive field, so it must auto-flow
through the existing config machinery (default, ``[ingest]`` TOML section, and the
``MEMRELAY_INGEST__INTAKE_SOURCE`` env override with env beating file) with no parser change.
"""

from __future__ import annotations

from pathlib import Path

from memrelay.config import load_config


def test_intake_source_defaults_to_replay() -> None:
    # RULING 2: default stays "replay" so #8 remains the shipping default (green, off-by-default
    # file_watch). Flipping the default is a deliberate one-line follow-up.
    cfg = load_config(environ={})
    assert cfg.ingest.intake_source == "replay"


def test_intake_source_via_kwargs() -> None:
    cfg = load_config(environ={}, ingest={"intake_source": "file_watch"})
    assert cfg.ingest.intake_source == "file_watch"


def test_intake_source_via_env_override() -> None:
    cfg = load_config(environ={"MEMRELAY_INGEST__INTAKE_SOURCE": "file_watch"})
    assert cfg.ingest.intake_source == "file_watch"


def test_intake_source_via_file_and_env_precedence(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
        [ingest]
        intake_source = "file_watch"
        """,
        encoding="utf-8",
    )
    cfg = load_config(path=cfg_file, environ={})
    assert cfg.ingest.intake_source == "file_watch"

    # env beats file.
    cfg2 = load_config(path=cfg_file, environ={"MEMRELAY_INGEST__INTAKE_SOURCE": "replay"})
    assert cfg2.ingest.intake_source == "replay"
