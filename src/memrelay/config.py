"""memrelay configuration: TOML load with defaults, path resolution, env overrides.

Zero-config by design — :func:`load_config` returns a working default when no file
is present. Overrides layer in this precedence (highest wins):

1. explicit keyword overrides passed to :func:`load_config`
2. ``MEMRELAY_*`` environment variables (``__`` nests, e.g. ``MEMRELAY_LLM__STRATEGY``)
3. a config file — ``MEMRELAY_CONFIG`` path, else the first that exists of
   ``$XDG_CONFIG_HOME/memrelay/config.toml``, ``~/.config/memrelay/config.toml``,
   ``~/.memrelay/config.toml``
4. built-in defaults (below)

The env-override convention mirrors traceforge's ``TRACEFORGE_*`` scheme (prefix +
``__`` nesting + scalar coercion) so both layers feel the same to operators.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ENV_PREFIX = "MEMRELAY_"
#: Env vars that configure loading itself rather than a config field.
_META_ENV = frozenset({"MEMRELAY_CONFIG", "MEMRELAY_HOME"})


# ─── Schema ──────────────────────────────────────────────────────────────────


@dataclass
class GraphConfig:
    backend: str = "kuzu"
    path: str = "~/.memrelay/graph.db"


@dataclass
class LLMConfig:
    # borrow-host reuses the host agent's own model (zero API keys); see SPEC §6.2.
    strategy: str = "borrow-host"
    host: str = "copilot"


@dataclass
class EmbeddingsConfig:
    provider: str = "local"
    model: str = "BAAI/bge-small-en-v1.5"


@dataclass
class Config:
    """Fully resolved memrelay configuration."""

    home: str = "~/.memrelay"
    graph: GraphConfig = field(default_factory=GraphConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)

    @property
    def home_path(self) -> Path:
        """The resolved ``~/.memrelay`` data directory (``~`` / env expanded)."""
        return _expand(self.home)

    @property
    def graph_path(self) -> Path:
        """Absolute path to the Kuzu graph database file."""
        return _expand(self.graph.path)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Path resolution ─────────────────────────────────────────────────────────


def _expand(value: str) -> Path:
    """Expand ``~`` and ``$VAR`` / ``${VAR}`` references to an absolute path."""
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def default_home(environ: dict[str, str] | None = None) -> Path:
    """Resolve the memrelay data directory (``~/.memrelay`` by default).

    Honors ``MEMRELAY_HOME`` and ``XDG_DATA_HOME`` (→ ``$XDG_DATA_HOME/memrelay``).
    """
    env = os.environ if environ is None else environ
    if env.get("MEMRELAY_HOME"):
        return _expand(env["MEMRELAY_HOME"])
    if env.get("XDG_DATA_HOME"):
        return _expand(os.path.join(env["XDG_DATA_HOME"], "memrelay"))
    return _expand("~/.memrelay")


def candidate_config_paths(environ: dict[str, str] | None = None) -> list[Path]:
    """Config file locations in precedence order (first existing one wins).

    An explicit ``MEMRELAY_CONFIG`` short-circuits discovery.
    """
    env = os.environ if environ is None else environ
    if env.get("MEMRELAY_CONFIG"):
        return [_expand(env["MEMRELAY_CONFIG"])]

    paths: list[Path] = []
    if env.get("XDG_CONFIG_HOME"):
        paths.append(_expand(os.path.join(env["XDG_CONFIG_HOME"], "memrelay", "config.toml")))
    paths.append(_expand("~/.config/memrelay/config.toml"))
    paths.append(_expand("~/.memrelay/config.toml"))
    return paths


def resolve_config_path(environ: dict[str, str] | None = None) -> Path | None:
    """Return the first existing config file, or ``None`` (→ pure defaults)."""
    for path in candidate_config_paths(environ):
        if path.is_file():
            return path
    return None


# ─── Merge / env / coercion helpers ──────────────────────────────────────────


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` (dicts merge, scalars replace)."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _coerce(value: str) -> Any:
    """Best-effort coercion of an env-var string to a Python scalar."""
    low = value.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def env_overrides(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Build a nested override dict from ``MEMRELAY_*`` env vars.

    ``MEMRELAY_LLM__STRATEGY=byo-key`` → ``{"llm": {"strategy": "byo-key"}}``.
    Meta vars (``MEMRELAY_CONFIG``, ``MEMRELAY_HOME``) are excluded.
    """
    env = os.environ if environ is None else environ
    result: dict[str, Any] = {}
    for key, value in env.items():
        if not key.startswith(ENV_PREFIX) or key in _META_ENV:
            continue
        parts = key[len(ENV_PREFIX) :].lower().split("__")
        target = result
        for part in parts[:-1]:
            node = target.get(part)
            if not isinstance(node, dict):
                node = {}
                target[part] = node
            target = node
        target[parts[-1]] = _coerce(value)
    return result


# ─── Construction ────────────────────────────────────────────────────────────


def _config_from_dict(data: dict[str, Any]) -> Config:
    """Build a :class:`Config`, ignoring unknown keys defensively."""
    graph = GraphConfig(**_known(GraphConfig, data.get("graph")))
    llm = LLMConfig(**_known(LLMConfig, data.get("llm")))
    embeddings = EmbeddingsConfig(**_known(EmbeddingsConfig, data.get("embeddings")))
    home = data.get("home", Config.home)
    return Config(home=home, graph=graph, llm=llm, embeddings=embeddings)


def _known(cls: type, section: Any) -> dict[str, Any]:
    """Filter ``section`` to the fields declared on dataclass ``cls``."""
    if not isinstance(section, dict):
        return {}
    allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return {k: v for k, v in section.items() if k in allowed}


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
    **overrides: Any,
) -> Config:
    """Load configuration with full precedence (see module docstring).

    Args:
        path: force a specific config file; skips discovery. ``None`` uses the
            standard search (or pure defaults when nothing is found).
        environ: environment mapping to read (defaults to ``os.environ``); passing
            an explicit dict keeps tests hermetic.
        **overrides: highest-precedence explicit section overrides, e.g.
            ``load_config(llm={"strategy": "local"})``.
    """
    if path is not None:
        file_path: Path | None = _expand(str(path))
        if not file_path.is_file():
            raise FileNotFoundError(f"config file not found: {file_path}")
    else:
        file_path = resolve_config_path(environ)

    data: dict[str, Any] = {}
    if file_path is not None:
        with open(file_path, "rb") as fh:
            data = _deep_merge(data, tomllib.load(fh))

    data = _deep_merge(data, env_overrides(environ))
    if overrides:
        data = _deep_merge(data, overrides)

    if "home" not in data:
        # Reflect MEMRELAY_HOME / XDG_DATA_HOME in the resolved home directory.
        data["home"] = str(default_home(environ))

    return _config_from_dict(data)


def ensure_home(config: Config) -> Path:
    """Create the memrelay data directory if absent and return it."""
    home = config.home_path
    home.mkdir(parents=True, exist_ok=True)
    return home
