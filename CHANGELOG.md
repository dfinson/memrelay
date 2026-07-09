# Changelog

All notable changes to memrelay are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

memrelay is **pre-alpha** (`0.x`): the public surface may change between minor
releases without a deprecation cycle.

## [Unreleased]

## [0.1.0] - 2026-07-09

First public release to PyPI — `pip install memrelay`.

### Added
- **Zero-config, key-less default stack.** `pip install memrelay` → `memrelay init`
  → `memrelay start` brings up a working memory layer with no API keys and no host
  agent required. `init` provisions the home, writes `config.toml`, registers the MCP
  server, and prefetches the local embedding model.
- **Automatic session observation** across ~18 agents (Copilot CLI, Claude Code,
  Codex, Cursor/Continue, Cline, Aider, and more) via
  [TraceForge](https://github.com/dfinson/traceforge), normalized into a common event
  stream and assembled into episodes.
- **Graph-backed memory** powered by [Graphiti](https://github.com/getzep/graphiti):
  bitemporal facts, LLM-assisted extraction, dedup, and reciprocal-rank-fusion retrieval.
- **Three MCP tools** exposed to the agent — `memory_recall`, `memory_detail`, and
  `memory_note` — for cross-session, cross-repo, and cross-agent recall.
- **Embedded storage out of the box** via [LadybugDB](https://pypi.org/project/ladybug/)
  (a maintained Kuzu-API/Cypher drop-in), with opt-in cloud backends behind extras:
  `memrelay[falkordb]`, `memrelay[neptune]` (Neo4j needs connection config only).
- **`memrelay` CLI**: `init`, `start`, `status`, `stop`, `mcp`, and memory maintenance
  commands.
- **Packaging**: wheel + sdist built with hatchling; console script `memrelay`;
  Trusted-Publishing release pipeline (`.github/workflows/release.yml`).

[Unreleased]: https://github.com/dfinson/memrelay/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dfinson/memrelay/releases/tag/v0.1.0
