"""Namespace + repo resolution for the MCP tools (SPEC §5.2, §5.4).

The MCP server tags every query with the caller's *namespace* (the shared-memory
scope) and, for notes, the *repo* provenance. Both are derived from the working
directory's git remote. This is intentionally dependency-free (a plain ``git``
subprocess) so the stateless MCP subprocess stays lightweight.
"""

from __future__ import annotations

import getpass
import os
import subprocess
from collections.abc import Mapping


def current_repo(cwd: str | os.PathLike[str] | None = None) -> str | None:
    """Return ``owner/name`` for the git repo at ``cwd``, or ``None``.

    Reads ``git remote get-url origin`` and parses the owner/name out of an SSH
    (``git@host:owner/name.git``) or HTTPS (``https://host/owner/name.git``) URL.
    Any failure (not a repo, no remote, git absent) yields ``None``.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _parse_owner_name(result.stdout.strip())


def _parse_owner_name(url: str) -> str | None:
    if not url:
        return None
    cleaned = url[:-4] if url.endswith(".git") else url
    # SSH scp-like form: git@github.com:owner/name
    if "@" in cleaned and ":" in cleaned and "//" not in cleaned:
        cleaned = cleaned.split(":", 1)[1]
    else:
        # URL form: strip scheme + host, keep the path.
        without_scheme = cleaned.split("://", 1)[-1]
        parts = without_scheme.split("/", 1)
        cleaned = parts[1] if len(parts) == 2 else parts[0]
    segments = [seg for seg in cleaned.strip("/").split("/") if seg]
    if len(segments) < 2:
        return None
    return f"{segments[-2]}/{segments[-1]}"


def resolve_namespace(repo: str | None, namespace_map: Mapping[str, str] | None = None) -> str:
    """Resolve a memory namespace from a repo id (SPEC §5.2).

    1. explicit ``namespace_map`` override, else
    2. the GitHub owner (``owner`` from ``owner/name``), else
    3. the OS username (local-only / no remote).
    """
    if repo and namespace_map and repo in namespace_map:
        return namespace_map[repo]
    if repo and "/" in repo:
        return repo.split("/", 1)[0]
    return getpass.getuser()


def resolve_context(
    cwd: str | os.PathLike[str] | None = None,
    namespace_map: Mapping[str, str] | None = None,
) -> tuple[str, str | None]:
    """Return ``(namespace, repo)`` for the current working directory."""
    repo = current_repo(cwd)
    return resolve_namespace(repo, namespace_map), repo
