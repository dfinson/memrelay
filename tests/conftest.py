"""Shared pytest fixtures for the memrelay test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
COPILOT_FIXTURE = FIXTURES_DIR / "copilot_session.jsonl"


@pytest.fixture
def copilot_fixture() -> Path:
    """Path to the committed, redacted Copilot ``events.jsonl`` fixture."""
    assert COPILOT_FIXTURE.is_file(), f"missing fixture: {COPILOT_FIXTURE}"
    return COPILOT_FIXTURE
