# Releasing memrelay

memrelay is published to [PyPI](https://pypi.org/project/memrelay/) using **PyPI
Trusted Publishing** (OIDC). No long-lived API token is stored in the repository —
GitHub Actions mints a short-lived, scoped token per release. The real publish is
gated on a **published GitHub Release**; nothing is ever published from a branch,
a pull request, or a merge to `main`.

The pipeline lives in [`.github/workflows/release.yml`](.github/workflows/release.yml).

---

## One-time setup (maintainer, out of band)

These steps configure the trust relationship and **must be done before the first
publish**. The automation cannot do them for you.

### 1. Register the trusted publisher on PyPI

On <https://pypi.org> → your account → **Publishing** → *Add a pending publisher*
(for a project that does not exist yet) with **exactly**:

| Field                   | Value               |
| ----------------------- | ------------------- |
| PyPI Project Name       | `memrelay`          |
| Owner                   | `dfinson`           |
| Repository name         | `memrelay`          |
| Workflow name           | `release.yml`       |
| Environment name        | `pypi`              |

### 2. Register the trusted publisher on TestPyPI (for dry-runs)

Repeat the same on <https://test.pypi.org> with **Environment name `testpypi`**.

### 3. Create the GitHub Environments

In the repo → **Settings → Environments**, create two environments whose names match
the trusted-publisher config above:

- `pypi` — the production publish. Consider adding **required reviewers** so a human
  must approve before the job releases to PyPI.
- `testpypi` — the rehearsal target.

That is all. There are **no repository secrets** to add.

---

## Version management

The version is single-sourced from **`src/memrelay/__init__.py`**:

```python
__version__ = "0.1.0"
```

`pyproject.toml` declares `dynamic = ["version"]` and hatchling reads it via
`[tool.hatch.version]`, so `memrelay --version`, the wheel metadata, and the PyPI
listing can never drift. To bump the version, edit that one line.

memrelay follows [SemVer](https://semver.org). While pre-alpha (`0.x`), breaking
changes may land in a minor bump.

---

## Cutting a release

1. **Bump the version** in `src/memrelay/__init__.py`.
2. **Update `CHANGELOG.md`**: rename the `[Unreleased]` section to the new
   `[X.Y.Z] - YYYY-MM-DD` (use the actual release date), start a fresh empty
   `[Unreleased]`, and update the compare/link references at the bottom.
3. **Open a PR and merge it.** On the PR the `build` and `install-verify` jobs run as a
   no-publish dry-run — both must be green (they prove the wheel builds and installs
   cleanly). The publish jobs stay dormant on PRs.
4. **(Recommended) Rehearse on TestPyPI.** From the **Actions** tab, run the *Release*
   workflow via **Run workflow** (`workflow_dispatch`) with *Publish to TestPyPI*
   enabled. Then verify the rehearsal in a throwaway venv:

   ```bash
   python -m venv /tmp/mr && . /tmp/mr/bin/activate
   pip install --index-url https://test.pypi.org/simple/ \
       --extra-index-url https://pypi.org/simple/ "memrelay==X.Y.Z"
   memrelay --version
   ```

   (The `--extra-index-url` lets TestPyPI resolve real runtime dependencies from PyPI.)
5. **Publish for real.** Create a **GitHub Release** with tag **`vX.Y.Z`** (matching
   the version) and publish it. The `publish-pypi` job fires, builds, verifies, and
   uploads to PyPI via Trusted Publishing. If you configured required reviewers on the
   `pypi` environment, approve the run when prompted.
6. **Verify the published release** in a clean environment:

   ```bash
   python -m venv /tmp/mr2 && . /tmp/mr2/bin/activate
   pip install "memrelay==X.Y.Z"
   memrelay --version   # should print X.Y.Z
   memrelay init        # should succeed in a fresh MEMRELAY_HOME
   ```

---

## What the automation does and does not do

- **Does:** build the wheel + sdist, `twine check` them, verify a clean-venv install of
  the built wheel (`install-verify`), and — only on a published Release — upload to PyPI
  with no stored secret.
- **Does not:** publish on push or merge to `main`, publish from a pull request, or tag
  releases for you. Cutting the tag/release is a deliberate human action.

## Yanking a bad release

If a released version is broken, **yank** it on PyPI (Manage project → Release →
*Options → Yank*). Yanking hides it from new installs while leaving it available to
pins that already reference it. Then cut a fixed patch release following the steps above.
