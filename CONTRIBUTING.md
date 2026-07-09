# Contributing to memrelay

Thanks for your interest in memrelay. It is **pre-alpha** (`0.x`) software — the public
surface may change between minor releases without a deprecation cycle — so contributions,
bug reports, and design feedback are all welcome.

Before diving in, skim [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the pieces
fit together and [`SPEC.md`](SPEC.md) for the product contract.

## Development environment

memrelay targets **Python 3.11–3.13** (`requires-python = ">=3.11,<3.14"` in
[`pyproject.toml`](pyproject.toml); the upper bound tracks traceforge-toolkit).

Clone the repo, create a virtual environment, and install in editable mode with the dev
extras:

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

`.[dev]` pulls `pytest`, `ruff`, and `pre-commit` on top of the runtime dependencies. The
default runtime stack is **key-less** — it ships the embedded LadybugDB backend, local
fastembed embeddings, and the `borrow-host` LLM strategy — so a plain dev install needs no
API keys and no external services.

To reproduce the **full Linux CI dependency set** (including the optional cloud-backend
client libraries), install their extras too:

```bash
pip install -e ".[dev,falkordb,neptune]"
```

The cloud extras are only needed if you are working on the FalkorDB/Neptune adapters or
running their tests locally; a normal contribution does not require them. (Neo4j needs no
extra — its client is already a hard dependency of `graphiti-core`.)

Verify the install:

```bash
memrelay --help     # the Click command surface
memrelay config     # print the resolved configuration as JSON
```

## Running the tests

Tests live under [`tests/`](tests/) and run with `pytest` (config in `pyproject.toml`,
`testpaths = ["tests"]`):

```bash
pytest
```

The suite is split into two directories:

- **`tests/unit/`** — fast, hermetic unit tests. No network, no graph, no API keys.
- **`tests/integration/`** — end-to-end tests that exercise the traceforge pipeline and
  the daemon/MCP/engine round-trips. These are marked with the `integration` marker
  (declared in `pyproject.toml`).

Select or skip the integration tests with the marker:

```bash
pytest -m integration        # only the end-to-end tests
pytest -m "not integration"  # skip them (fast inner loop)
```

## Linting & formatting

memrelay uses [Ruff](https://docs.astral.sh/ruff/) for both linting and formatting. CI
runs the check-only forms, so run these before pushing:

```bash
ruff check .            # lint
ruff format --check .   # formatting (verify only; drop --check to apply)
```

Ruff is configured in `pyproject.toml` (`[tool.ruff]`): line length **100**, target
**py311**, with the `E`, `F`, `I`, `UP`, `B`, `W`, `C4`, and `SIM` rule sets selected
(`SIM105` is ignored). Import sorting is handled by the `I` rules — no separate isort.

### Pre-commit hooks

A [`.pre-commit-config.yaml`](.pre-commit-config.yaml) wires ruff (lint + format) plus
standard hygiene hooks (end-of-file, trailing-whitespace, YAML/TOML checks,
merge-conflict, large-file guard). Install once and they run on every commit:

```bash
pre-commit install
pre-commit run --all-files   # optional: run against the whole tree now
```

## Branch & pull-request conventions

- **Branch off `main`.** Create a topic branch for your change and open a PR against
  `main`.
- **Keep PRs green.** Every PR must pass the full CI matrix below before merge — that means
  `ruff check`, `ruff format --check`, and `pytest` all pass on every job.
- **Update the changelog.** memrelay keeps a
  [Keep a Changelog](https://keepachangelog.com/)-style [`CHANGELOG.md`](CHANGELOG.md).
  Add user-facing changes under the `[Unreleased]` section (the release process later
  renames it — see [`RELEASING.md`](RELEASING.md)).
- **Co-author trailer.** If your commit was produced with an AI assistant, keep the
  `Co-authored-by:` trailer on the commit.
- **Match the neighbours.** Follow the style and structure of the surrounding code; the
  ruff config is the source of truth for formatting disputes.

## How the CI matrix works

Continuous integration is two workflows.

### `.github/workflows/ci.yml` — lint + test

Runs on every push to `main` and on every pull request. Three jobs:

| Job | Runner | Python | Installs | What it runs |
| --- | --- | --- | --- | --- |
| **build** | `ubuntu-latest` (matrix) | 3.11, 3.12, 3.13 | `pip install -e ".[dev,falkordb,neptune]"` | `ruff check .`, `ruff format --check .`, `pytest` |
| **build-windows** | `windows-latest` | 3.12 | `pip install -e ".[dev]"` | `ruff check .`, `ruff format --check .`, `pytest` |
| **first-run-smoke** | `ubuntu-latest` | 3.12 | non-editable `pip install .` (default extras only) | `python scripts/first_run_smoke.py` |

Notes:

- The **build** matrix is the primary gate: three Linux Python versions, each with the dev
  and cloud-backend extras installed.
- **build-windows** exists as a regression guard for a Windows-only stdio/loopback hang
  (#94) that the Linux jobs could never catch. It installs dev extras only; the two
  cloud-backend tests skip cleanly when those extras are absent.
- **first-run-smoke** installs memrelay the way a user would — a plain, non-editable
  `pip install .` with default extras only — and drives the zero-config, key-less first-run
  path, proving the out-of-the-box dependency set is self-sufficient.

### `.github/workflows/release.yml` — packaging & release

Also runs on every PR and push to `main`, but there it is a **no-publish dry run**:

- **build** — `python -m build` produces a wheel + sdist and `twine check`s them.
- **install-verify** — installs the built wheel into a clean venv (no source checkout) and
  checks the `memrelay` console script, its `--version` against the wheel metadata, and
  that every subpackage imports.

The publish jobs are gated off for PRs and pushes. On `workflow_dispatch` the pipeline can
additionally publish to **TestPyPI** (a rehearsal); the real **PyPI** publish fires only on
a **published GitHub Release**, via **PyPI Trusted Publishing (OIDC)** — no API token is
stored in the repo. Full details are in [`RELEASING.md`](RELEASING.md); you do not need to
run the release pipeline to contribute.

## Handy scripts

A few developer scripts live in [`scripts/`](scripts/):

- `scripts/first_run_smoke.py` — the zero-config first-run smoke test (also run in CI).
- `scripts/ingest_fixture.py` — replay a redacted Copilot fixture through the traceforge
  pipeline (SessionEvents only, no graph).
- `scripts/engine_demo.py` — a small end-to-end engine demo.
- `scripts/capture_fixture.py` — capture a fixture from a real session.

## Reporting issues

Please file bugs and feature requests on the
[issue tracker](https://github.com/dfinson/memrelay/issues). Since the CI matrix covers
Linux and Windows across Python 3.11–3.13, include your OS and Python version when a
problem looks environment-specific.
