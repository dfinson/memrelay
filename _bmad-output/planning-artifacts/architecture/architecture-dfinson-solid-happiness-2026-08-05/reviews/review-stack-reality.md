---
name: review-stack-reality
type: architecture-review
lens: stack-reality-check
target: ARCHITECTURE-SPINE.md
reviewer: independent-reviewer-gate
date: 2026-08-05
status: complete
verdict: conditional-pass
---

# Stack Reality Review

## Verdict

Conditional pass before fixes. The named stack exists and fits the local evaluator, but one ambiguous package name and one incorrect Inspect API name required correction.

## Findings

### F-01 - Ambiguous OpenAI instrumentation package

The spine listed `OpenAI instrumentation 0.1.53`, which could be read as the unrelated `opentelemetry-instrumentation-openai` package. The intended package is `openinference-instrumentation-openai` 0.1.53. The spine must name the exact package and validate compatibility with the pinned OpenAI client and OTel SDK.

Sources:

- https://pypi.org/project/openinference-instrumentation-openai/
- https://pypi.org/project/opentelemetry-instrumentation-openai/

### F-02 - Incorrect Inspect bridge symbol

`AgentBridge` is not the current public Inspect API symbol. Current official surfaces include the `@agent` decorator and the `agent_bridge()` context manager. The Copilot integration should prefer a custom `@agent` that calls the official Copilot SDK directly and use `agent_bridge()` only if required by the pinned release.

Sources:

- https://inspect.aisi.org.uk/agents.html
- https://inspect.aisi.org.uk/agent-bridge.html

## Verified Stack

- Inspect AI 0.3.252 is current and Beta-classified.
- OpenTelemetry Python SDK and OTLP exporters 1.44.0 are Stable.
- OpenInference semantic conventions 0.1.31 are Stable.
- DuckDB 1.5.5 and PyArrow 25.0.0 are current Stable packages.
- OTel GenAI semantic conventions remain Development and require a pinned compatibility mapping.
- `github-copilot-sdk` is an official GitHub Alpha package. Its exact package, runtime, and model catalog must be pinned during conformance.
- SQLite WAL and SHA-256 are available through the Python standard library.
- Current source contains `SessionDiscoveryPoller`, `RunObserveCapture`, and `LiveTailCapture`.

## Disposition

F-01 and F-02 were clear autofixes. All other version and brownfield checks passed or were correctly deferred to implementation-time conformance.
