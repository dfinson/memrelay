"""Unit tests for the git-remote → ``owner/name`` parser behind namespace scoping.

``current_repo`` shells out to ``git remote get-url origin`` and hands the URL to
``_parse_owner_name``; the parsed ``owner/name`` is what ``resolve_namespace`` scopes shared
memory by (SPEC §5.2/§5.4). ``test_mcp_namespace.py`` pins the #94 stdin-detach fix and
``test_namespace_resolver.py`` covers the resolver; this file pins the **parser** itself —
every remote-URL shape it must accept, the ``None`` edges (empty / single-segment), and the
casing contract (``current_repo`` returns ``owner/name`` **verbatim**, mixed-case included,
because the #41 config map is what normalizes for lookup).
"""

from __future__ import annotations

import subprocess

import pytest

from memrelay.mcp import namespace
from memrelay.mcp.namespace import _parse_owner_name


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # SSH scp-like form, with and without the .git suffix.
        ("git@github.com:dfinson/memrelay.git", "dfinson/memrelay"),
        ("git@github.com:dfinson/memrelay", "dfinson/memrelay"),
        # HTTPS form, with and without .git and a trailing slash.
        ("https://github.com/dfinson/memrelay.git", "dfinson/memrelay"),
        ("https://github.com/dfinson/memrelay", "dfinson/memrelay"),
        ("https://github.com/dfinson/memrelay/", "dfinson/memrelay"),
        # Deep self-hosted path: the last two segments (subgroup/repo) win.
        ("https://gitlab.example.com/group/subgroup/repo.git", "subgroup/repo"),
    ],
)
def test_parse_owner_name_accepts_ssh_and_https_forms(url: str, expected: str) -> None:
    assert _parse_owner_name(url) == expected


def test_parse_owner_name_preserves_case_verbatim() -> None:
    # current_repo returns the remote's owner/name verbatim; normalization for lookup happens
    # later (config #41 / resolver), so the parser must NOT lower-case here.
    assert _parse_owner_name("git@github.com:Dfinson/MemRelay.git") == "Dfinson/MemRelay"


@pytest.mark.parametrize(
    "url",
    [
        "",  # empty URL -> nothing to parse
        "justname",  # a bare single token, no owner
        "https://github.com/onlyowner",  # only one path segment after the host
        "https://github.com/",  # host only, no path
    ],
)
def test_parse_owner_name_returns_none_when_owner_name_incomplete(url: str) -> None:
    assert _parse_owner_name(url) is None


def test_current_repo_returns_none_when_git_reports_no_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero ``git remote get-url origin`` (no remote / not a repo) yields ``None``."""

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no such remote 'origin'")

    monkeypatch.setattr(namespace.subprocess, "run", fake_run)

    assert namespace.current_repo() is None
