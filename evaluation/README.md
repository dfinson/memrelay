# memrelay evaluator

This independently packaged Python 3.13 project contains the treatment-neutral
evaluation domain foundation. It is intentionally separate from the memrelay product
wheel and currently supports only deterministic, unpaid conformance paths.

```powershell
uv sync --extra dev
uv run memrelay-eval --help
uv run pytest
```

The `future-adapters` extra records frozen dependency pins for later stories. This
story does not import, execute, or configure those adapters.
