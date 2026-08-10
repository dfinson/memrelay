"""Fail-closed, value-free credential boundary scanning."""

from __future__ import annotations

import base64
import binascii
import io
import re
import tarfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from hashlib import sha256
from pathlib import Path

from memrelay_eval.domain.errors import SecretBoundaryViolationError

_MAX_SCAN_BYTES = 4 * 1024 * 1024
_MAX_DEPTH = 8
_SECRET_PATTERNS = (
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})\b"),
    ),
    (
        "copilot_credential",
        re.compile(r"\bcopilot(?:[_-]?(?:auth|token|subscription))?\s*[=:]\s*[A-Za-z0-9._-]{16,}"),
    ),
    ("synthetic_canary", re.compile(r"\bsynthetic-canary-[a-f0-9]{32}\b", re.IGNORECASE)),
)
_CREDENTIAL_NAMES = frozenset(
    {
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "COPILOT_AUTH_TOKEN",
        "COPILOT_GITHUB_TOKEN",
    }
)
_TREATMENT_TERMS = ("treatment", "control", "variant", "arm=")


@dataclass(frozen=True, slots=True)
class SecretScanFinding:
    """A non-secret, typed scan observation suitable for ledger-adjacent evidence."""

    location: str
    detector: str
    digest: str

    def __post_init__(self) -> None:
        if not self.location or not self.detector or len(self.digest) != 64:
            raise ValueError("secret scan findings require a non-secret typed projection")

    def to_dict(self) -> dict[str, str]:
        return {"location": self.location, "detector": self.detector, "digest": self.digest}


def scan_secret_boundaries(boundaries: Mapping[str, object]) -> tuple[SecretScanFinding, ...]:
    """Scan selected evidence boundaries and raise without exposing a matched value."""

    findings: list[SecretScanFinding] = []
    for location, value in boundaries.items():
        _scan(value, str(location), findings, 0)
    return tuple(findings)


def require_secret_boundary_clear(boundaries: Mapping[str, object]) -> None:
    """Fail closed when any selected evidence surface contains credential material."""

    findings = scan_secret_boundaries(boundaries)
    if findings:
        raise SecretBoundaryViolationError(findings)


def _scan(value: object, location: str, findings: list[SecretScanFinding], depth: int) -> None:
    if depth > _MAX_DEPTH:
        findings.append(_finding(location, "scan_depth_exceeded", b""))
        return
    if isinstance(value, Path):
        _scan_path(value, location, findings, depth)
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            key_digest = sha256(key_text.encode()).hexdigest()[:12]
            child_location = f"{location}.key_{key_digest}"
            _scan_bytes(key_text.encode(), f"{child_location}.name", findings, depth + 1)
            if key_text.upper() in _CREDENTIAL_NAMES and isinstance(nested, str) and nested:
                findings.append(_finding(child_location, "credential_named_value", nested.encode()))
            _scan(nested, child_location, findings, depth + 1)
    elif isinstance(value, (str, bytes, bytearray)):
        data = bytes(value) if not isinstance(value, str) else value.encode()
        _scan_bytes(data, location, findings, depth)
    elif is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            _scan(getattr(value, item.name), f"{location}.{item.name}", findings, depth + 1)
    elif isinstance(value, BaseException):
        _scan_bytes(str(value).encode(), location, findings, depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _scan(nested, f"{location}[{index}]", findings, depth + 1)


def _scan_path(path: Path, location: str, findings: list[SecretScanFinding], depth: int) -> None:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_SCAN_BYTES:
            findings.append(_finding(location, "scan_unavailable", str(path).encode()))
            return
        _scan_bytes(path.read_bytes(), location, findings, depth + 1)
    except OSError:
        findings.append(_finding(location, "scan_unavailable", str(path).encode()))


def _scan_bytes(data: bytes, location: str, findings: list[SecretScanFinding], depth: int) -> None:
    if depth > _MAX_DEPTH:
        findings.append(_finding(location, "scan_depth_exceeded", b""))
        return
    if len(data) > _MAX_SCAN_BYTES:
        findings.append(_finding(location, "scan_size_exceeded", b""))
        return
    text = data.decode("utf-8", errors="replace")
    for detector, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(_finding(location, detector, match.group(0).encode()))
    if "agent_visible" in location.casefold() and any(
        term in text.casefold() for term in _TREATMENT_TERMS
    ):
        findings.append(_finding(location, "treatment_label_exposed", text.encode()))
    _scan_base64(text, location, findings, depth)
    _scan_archive(data, location, findings, depth)


def _scan_base64(text: str, location: str, findings: list[SecretScanFinding], depth: int) -> None:
    candidates = re.findall(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/_-]{24,}={0,2}", text)
    candidates.extend(
        re.sub(r"\s+", "", candidate)
        for candidate in re.findall(
            r"(?:[A-Za-z0-9+/_-]{4,}\s+){1,}[A-Za-z0-9+/_-]{4,}={0,2}",
            text,
        )
    )
    for candidate in candidates:
        if len(candidate) < 24:
            continue
        try:
            decoded = base64.b64decode(candidate, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError):
            continue
        if decoded and decoded != candidate.encode():
            _scan_bytes(decoded, f"{location}.base64", findings, depth + 1)


def _scan_archive(
    data: bytes,
    location: str,
    findings: list[SecretScanFinding],
    depth: int,
) -> None:
    stream = io.BytesIO(data)
    try:
        if zipfile.is_zipfile(stream):
            with zipfile.ZipFile(stream) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    digest = sha256(member.filename.encode()).hexdigest()[:12]
                    member_location = f"{location}.zip_member_{digest}"
                    if member.file_size > _MAX_SCAN_BYTES:
                        findings.append(_finding(member_location, "scan_size_exceeded", b""))
                        continue
                    try:
                        _scan_bytes(archive.read(member), member_location, findings, depth + 1)
                    except (OSError, RuntimeError, zipfile.BadZipFile):
                        findings.append(_finding(member_location, "archive_scan_failed", b""))
            return
    except (OSError, zipfile.BadZipFile):
        findings.append(_finding(location, "archive_scan_failed", b""))
        return
    try:
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode="r:*") as archive:
            for member in archive.getmembers():
                if not member.isfile() or member.size > _MAX_SCAN_BYTES:
                    if member.isfile():
                        findings.append(
                            _finding(
                                f"{location}.tar_member_{sha256(member.name.encode()).hexdigest()[:12]}",
                                "scan_size_exceeded",
                                b"",
                            )
                        )
                    continue
                extracted = archive.extractfile(member)
                if extracted is not None:
                    _scan_bytes(
                        extracted.read(),
                        f"{location}.tar_member_{sha256(member.name.encode()).hexdigest()[:12]}",
                        findings,
                        depth + 1,
                    )
    except (OSError, tarfile.TarError):
        return


def _finding(location: str, detector: str, material: bytes) -> SecretScanFinding:
    return SecretScanFinding(location, detector, sha256(material).hexdigest())
