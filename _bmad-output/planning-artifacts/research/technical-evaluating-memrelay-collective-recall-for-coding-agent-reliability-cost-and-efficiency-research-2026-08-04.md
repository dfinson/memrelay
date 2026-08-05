---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments: []
workflowType: 'research'
lastStep: 6
research_type: 'technical'
research_topic: 'Evaluating memrelay collective recall for coding-agent reliability, cost, and efficiency'
research_goals: 'Design a scientifically defensible, estimation-first evaluation program for whether, how, when, and by how much memrelay changes coding-agent reliability, total cost, and wall-clock efficiency across intra-session, inter-session, cross-agent, and later authorized cross-repository recall; use synthetic/public data, local infrastructure, committed evaluation tooling, executable graders, causal designs chosen by workload and estimand, harm/safety guards, reproducibility, and prospectively frozen claim criteria without presuming benefit.'
user_name: 'Davidfinson'
date: '2026-08-05'
web_research_enabled: true
source_verification: true
---

# Research Report: technical

**Date:** 2026-08-05  
**Author:** Davidfinson  
**Research Type:** technical

---

## Research Overview

This 2026-08-05 owner-corrected revision preserves the prior causal, statistical, grading, ITT, safety, reproducibility, and release-gate corrections while fixing the execution plane. Reliability and total-cost/wall-time efficiency are co-equal objectives with quality and harm guards; intra-session, cross-session, cross-agent, and later cross-repository scopes remain in program. Every evaluated coding/task agent is a local Python 3.11 process driven through a pinned GitHub Copilot SDK and authenticated by the owner's current GitHub Copilot subscription; no alternate task-agent inference plane is permitted. The primary and limited secondary model cells are selected only from the catalog and capabilities returned by `CopilotClient.list_models()` at experiment start. Sixteen pilot tasks still produce 128 assignment units; the expanded primary matrix still has 32 tasks and 512 units. Paired/repeated or independently blocked assignment is chosen by history/estimand.

Inspect AI is a local orchestration wrapper around a custom Copilot SDK solver adapter, not a model provider. Local OTel/OTLP, OpenInference plus versioned memrelay semantics, Parquet/DuckDB, and local content-addressed artifacts remain committed. The task-agent path is local orchestrator → local Copilot SDK session/runtime → GitHub Copilot subscription model service. A separate local memrelay/framework path may call the OpenAI API only for instrumented framework-internal extraction, summarization, or embeddings. Initial data is synthetic/public only. The pilot estimates effect distributions, reliability–cost–time Pareto frontiers, heterogeneity, harm tails, and intervals; practical release thresholds are generated from evidence and operating economics, then frozen only before a fresh confirmatory claim. No reviewed evidence yet establishes that memrelay improves coding-agent outcomes.

## Technical Research Scope Confirmation

**Research Topic:** Evaluating memrelay collective recall for coding-agent reliability, cost, and efficiency  
**Research Goals:** Design a scientifically defensible, dual-objective, estimation-first program for reliability, total cost, wall time, retrieval, harm, and safety across every supported memory scope without presuming benefit.

**Technical Research Scope:**

- Architecture Analysis - experimental and evidence-system architecture
- Implementation Approaches - falsification-first adoption and executable contracts
- Technology Stack - languages, frameworks, storage, telemetry, and analysis
- Integration Patterns - MCP, agent adapters, graders, evidence, and governance
- Performance Considerations - success, latency, cost, scale, and uncertainty

**Research Methodology:** current web data, primary/official sources, multi-source verification for consequential claims, explicit confidence, and evidence before advocacy.  
**Scope Confirmed:** 2026-08-05

## Technology Stack Analysis

### Programming Languages

Python 3.11 is the control-plane baseline because memrelay and its deterministic evaluation are Python and Inspect exposes Python task, dataset, solver, agent, scorer, and analysis APIs. Preserve the repository's supported interpreter range when executing product code, but pin one interpreter and dependency lock per trial. SQL is an analysis language over canonical tables; an independent R implementation is optional for confirmatory cross-checking, not a runtime dependency. Provider/model latency will dominate orchestration, so process isolation, bounded concurrency, and measured overhead are preferable to a language rewrite. [Inspect AI](https://inspect.aisi.org.uk/)

_Popular language:_ Python.  
_Emerging/specialized:_ SQL for derived evidence; R for independent statistical reproduction.  
_Evolution:_ pin rather than chase interpreter versions during a study.  
_Performance:_ benchmark the runner; do not assume its overhead is negligible.

### Development Frameworks and Libraries

Keep the existing pytest retrieval fixture as L0/L1 deterministic wiring evidence only. The evaluation stack is committed: Inspect AI orchestrates tasks, datasets, limits, assignment metadata, custom agents/solvers, and scorers; a custom `CopilotSdkSolver` invokes the local Python SDK directly and returns native events, patches, and terminal state to Inspect. Inspect's ordinary model provider and generic model bridges are not in the task-agent inference path. OpenTelemetry SDK plus a local Collector and OTLP are the required transport; OTLP's lack of multi-hop end-to-end delivery guarantees makes explicit registry reconciliation mandatory. OpenInference is the pinned interoperability vocabulary, extended by versioned `memrelay.eval.*` history, validity, scope, arm, provider, credential-domain, and cost-source semantics. [Inspect custom agents](https://inspect.aisi.org.uk/agent-custom.html) [Inspect agents](https://inspect.aisi.org.uk/agents.html) [Copilot SDK Python](https://github.com/github/copilot-sdk/blob/main/python/README.md) [OTLP 1.11.0](https://opentelemetry.io/docs/specs/otlp/) [OpenInference](https://arize-ai.github.io/openinference/spec/)

_Major framework:_ Inspect AI with native-runtime adapters.  
_Deterministic baseline:_ stdlib + pytest.  
_Telemetry:_ OTel SDK/local Collector + OTLP with reconciliation.  
_Semantics:_ pinned OpenInference subset plus versioned memrelay fields.

### Database and Storage Technologies

The memrelay graph is the treatment, not the experiment ledger. Canonical evidence is versioned Parquet queried with DuckDB; append-only JSON manifests and native OTLP/provider/Inspect records remain immutable source artifacts. Every Parquet table has a declared Arrow-compatible schema, row-count/type/null/unit checks, deterministic derivation code, and round-trip validation. Raw and derived artifacts are content-addressed locally by SHA-256 with a manifest index, while governed deletion uses tombstones, reference tracking, index rebuild, and verified removal rather than treating hashes as retention authority. Feasibility tests validate these committed integrations and pinned-version assumptions; failure triggers repair or a documented temporary JSONL fallback without reopening an indefinite tool bake-off. [Apache Parquet](https://parquet.apache.org/docs/) [DuckDB Parquet](https://duckdb.org/docs/stable/data/parquet/overview) [OTLP](https://opentelemetry.io/docs/specs/otlp/)

_Relational/analytical:_ Parquet canonical tables and DuckDB analysis.  
_Graph:_ system under test only.  
_In-memory:_ cache only, never sole evidence.  
_Warehouse:_ none in the baseline; local Parquet/DuckDB is authoritative.

### Development Tools and Platforms

Record Git SHAs, dirty-tree hashes, dependency locks, container digests, task/harness versions, model/provider identifiers, prompts, tool policy, arm code, assignment seed escrow ID, retry lineage, and price-table version. Benchmark-native executable graders remain outcome authority. Inspect viewers or other dashboards may aid inspection but cannot own the only evidence copy. CI should run deterministic evaluator contracts and a no-network smoke test, not paid efficacy experiments. [Inspect evaluation logs](https://inspect.aisi.org.uk/eval-logs.html) [SWE-bench](https://www.swebench.com/)

_IDE/viewer:_ optional.  
_Version control:_ immutable revision plus dirty-state digest.  
_Build:_ locked environments and digest-pinned task images.  
_Testing:_ pytest contracts plus benchmark-native graders.

### Cloud Infrastructure and Deployment

Use local, ephemeral, arm-isolated agent processes with identical CPU, memory, filesystem, network policy, Copilot identity, caches, and concurrency. Containers are used only where the benchmark requires them. A local OTel Collector writes to the local evidence store; no managed/cloud telemetry, experiment tracker, warehouse, or evidence service belongs in the baseline. External calls are limited to GitHub Copilot service calls made by the Copilot SDK, OpenAI API calls made by explicitly instrumented framework-internal memrelay/Graphiti operations, and acquisition of pinned public dependencies/fixtures; grading is offline wherever feasible. Expected-record reconciliation remains mandatory because OTLP does not guarantee delivery across the entire chain. [Inspect sandboxing](https://inspect.aisi.org.uk/sandboxing.html) [OTLP](https://opentelemetry.io/docs/specs/otlp/)

_Cloud provider:_ no required vendor.  
_Containers:_ digest-pinned where supported.  
_Serverless:_ ingestion only, not default agent execution.  
_Edge/CDN:_ artifact distribution only, always hash-verified.

### Technology Adoption Trends

Compose the committed tools behind canonical interfaces and preserve repair/fallback paths. The GitHub Copilot SDK exposes the Copilot CLI engine through JSON-RPC, supports Python 3.11+, local sessions, runtime model discovery, custom tools/MCP configuration, event history, permissions, reasoning effort, session limits, and optional OTel telemetry. Standard SDK use authenticates with the signed-in GitHub user and requires a Copilot subscription; its SDK billing follows Copilot CLI usage. The study forbids the SDK's own BYOK provider mode because that would replace the owner-subscription inference plane. MCP standardizes the memrelay treatment boundary; Inspect supplies local scheduling and scoring only; OpenInference still requires versioned memrelay temporal semantics and privacy minimization. Tool commitment is not evidence of memrelay efficacy. [GitHub Copilot SDK](https://github.com/github/copilot-sdk) [Copilot SDK Python](https://github.com/github/copilot-sdk/blob/main/python/README.md) [Copilot SDK authentication](https://github.com/github/copilot-sdk/blob/main/docs/auth/authenticate.md) [OpenInference](https://arize-ai.github.io/openinference/spec/)

**Fresh-search quality assessment (2026-08-05):** high confidence in cited protocol/framework capabilities and the committed local architecture; medium confidence in Copilot/Inspect adapter parity until the integration tests run; unknown confidence in memrelay effects. Consequential facts were checked directly against official documentation and primary repositories.

## Integration Patterns Analysis

### API Design Patterns

The treatment path is the local memrelay MCP server. Inspect owns orchestration but does not replace or proxy the native agent loop: `CopilotSdkSolver` starts a local Python Copilot SDK client/session over the pinned bundled Copilot CLI JSON-RPC runtime. Each run provisions a clean workspace and arm-isolated memory snapshot, applies an opaque arm code, launches the same SDK agent configuration, collects native events/patch/exit state, and cleans up. The local registry and manifest writer manage assignments without a network control plane. Explicitly set Copilot's built-in `memory` and cross-session store off in every arm so memrelay is the only manipulated memory plane. MCP's specification supplies the JSON-RPC tool boundary, while GitHub documents that the SDK controls the local Copilot CLI engine and supports MCP/custom tools. [MCP specification](https://modelcontextprotocol.io/specification/2025-11-25) [GitHub Copilot SDK](https://github.com/github/copilot-sdk) [Copilot SDK Python](https://github.com/github/copilot-sdk/blob/main/python/README.md)

### Communication Protocols

Use MCP `stdio` for supported local hosts and Streamable HTTP only when needed. The MCP transport specification requires UTF-8 JSON-RPC; `stdio` messages are newline-delimited and stdout must contain only protocol messages. Streamable HTTP requires Origin validation and recommends localhost binding and authentication. Propagate W3C `traceparent`/`tracestate` where possible; otherwise correlate with an opaque run ID and span links rather than inventing parentage. Never place prompts, repository names, user identifiers, secrets, or treatment labels in W3C Baggage; its Candidate Recommendation warns about confidential information crossing trust boundaries. [MCP transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) [W3C Trace Context](https://www.w3.org/TR/trace-context/) [W3C Baggage](https://www.w3.org/TR/baggage/)

### Data Formats and Standards

Use five representations with explicit ownership:

1. Native MCP JSON-RPC, provider logs, benchmark results, and OTLP Protobuf remain immutable source artifacts.
2. A versioned JSON run manifest records assignment, frozen inputs, environment, expected evidence, classification, retention, retry lineage, and hashes.
3. OTel spans/logs/metrics cross the local OTLP boundary through the local Collector; OpenInference attributes plus versioned `memrelay.eval.*` attributes encode AI and memory semantics.
4. Canonical event and analysis tables are Parquet with pinned schemas and DuckDB views; JSONL is the temporary repair fallback only.
5. Markdown/HTML reports are generated projections whose cells link to DuckDB query hashes, Parquet rows, and source-artifact hashes.

CSV is export-only. Inspect `.eval` files are read through the official Log API and retained natively. OpenInference is an interchange overlay; large prompts, code, patches, transcripts, and candidate lists remain governed local artifacts referenced by digest. [Inspect logs](https://inspect.aisi.org.uk/eval-logs.html) [OpenInference](https://arize-ai.github.io/openinference/spec/) [Apache Parquet](https://parquet.apache.org/docs/) [DuckDB Parquet](https://duckdb.org/docs/stable/data/parquet/overview)

### System Interoperability Approaches

Use ports and adapters: `AgentAdapter`, `MemoryArmAdapter`, `TaskAdapter`, `ScorerAdapter`, `TraceAdapter`, `CostAdapter`, and `ArtifactStore`. Every adapter must pass the same golden synthetic run and preserve raw native records. The canonical contract—not a viewer—defines IDs, units, clocks, statuses, nullable fields, classifications, and derivation formulas. A compatibility matrix records which product/provider exposes model requests, tokens, billed amounts, tool calls, filesystem actions, memory events, and errors; unsupported fields remain explicitly unavailable rather than zero.

### Microservices Integration Patterns

Use a modular local control plane and isolated local workers: Inspect task/randomization metadata, the custom Copilot SDK solver/adapter, one arm-isolated memrelay instance, benchmark grader, Parquet evidence writer, DuckDB analysis, content-addressed artifact store, and local Collector. The durable state machine is `planned → assigned → provisioned → running → terminal → graded → reconciled → locked`, with append-only reason-coded transitions. Retried runs retain the failed original and receive a new attempt ID; they never overwrite randomized outcomes. Circuit breakers cap spend/time/provider faults but produce observable terminal records. Cleanup is compensating operational work, not rollback of evidence.

### Event-Driven Integration

Lifecycle events include `run.assigned`, `workspace.ready`, `agent.started`, `memory.read`, `memory.write`, `agent.terminal`, `grader.terminal`, `artifact.persisted`, `cost.reconciled`, and `trace.reconciled`. Consumers are idempotent by event ID. Reconciliation joins the registry, native events, expected event classes, OTLP, grader artifacts, and invoices. OTLP acknowledges one hop and explicitly does not guarantee multi-hop end-to-end delivery, so absence of a span cannot mean absence of an operation. [OTLP 1.11.0](https://opentelemetry.io/docs/specs/otlp/)

### Integration Security Patterns

Every arm receives equivalent least privilege, network policy, tool approvals, credentials, and filesystem roots. Repository content, memories, tool descriptions, web text, and model output are untrusted. Use short-lived per-run credentials, encrypted raw evidence, default-deny telemetry attributes, and Collector allowlist/redaction where the spike proves no evidence loss. OTel states that implementers must identify sensitive fields, obtain necessary consent, minimize collection, and review instrumentation; hashing predictable identifiers may not anonymize them. MCP similarly requires explicit user consent and control for data/tool access. [OTel sensitive-data guidance](https://opentelemetry.io/docs/security/handling-sensitive-data/) [MCP security principles](https://modelcontextprotocol.io/specification/2025-11-25)

**Fresh-search quality assessment:** high confidence in protocol requirements from MCP/W3C/OTLP; medium in normalized cross-product comparability; low until each adapter proves its event and billing coverage. Search-result summaries were not used as evidence.

## Architectural Patterns and Design

### System Architecture Patterns

Use a claim ladder: L0 evaluator conformance; L1 deterministic/production retrieval; L2 randomized single-run downstream effect; L3 randomized longitudinal effect; L4 operational release fitness. A lower layer never licenses a higher-layer claim. The control plane freezes protocol and assignments; isolated workers execute; benchmark-native graders score; reconciliation locks evidence; analysis consumes only immutable, reconciled tables.

#### Corrected runtime architecture and trust boundaries

```text
local Inspect task/scorer/control plane
  -> local CopilotSdkSolver
    -> local GitHub Copilot SDK session + bundled Copilot CLI runtime
      -> GitHub Copilot model service (owner's current Copilot subscription)

local memrelay MCP thin client
  -> local memrelay daemon / Graphiti framework / local graph and embeddings
    -> OpenAI API only for explicitly configured framework-internal LLM or embedding calls

local SDK + memrelay instrumentation
  -> local OTel Collector -> local raw evidence/CAS -> local Parquet/DuckDB
```

These are separate credential, inference, telemetry, and cost domains. The Copilot SDK uses the current signed-in GitHub user; the study does not pass a custom SDK provider and does not use SDK BYOK. The OpenAI API credential is injected only into the memrelay daemon or another named framework-internal process. The solver constructs a minimal child environment that excludes that credential and `OPENAI_API_KEY`/`OPENAI_BASE_URL`; no key value or key-bearing environment dump may enter an agent workspace, Copilot runtime, MCP thin client, tool result, prompt, trace, manifest, or report.

Memrelay's shipped zero-key default is not suitable for this causal experiment. Its `borrow-host` client flattens Graphiti prompts and launches `copilot --session-id … -p … -s`; it neither pins the study model nor separates ingestion consumption from agent consumption, and those internal sessions consume the same Copilot identity/quota. Its `byo-key` client instead lazily wraps Graphiti's OpenAI client, reads the configured `llm.api_key_env` only on extraction, uses `llm.model`, and optionally honors `OPENAI_BASE_URL`; the current implementation does not branch on `llm.provider`, so the protocol must not treat that field as a provider selector. Local embeddings remain local unless separately configured. Because the current strategy selector can fall back from a missing `byo-key` credential to installed `borrow-host`, every run must preflight `llm.strategy=byo-key`, key presence, pinned framework model, expected OpenAI base URL, and the selected concrete client type, then fail closed if any assertion fails. No silent borrow-host fallback is admissible. [memrelay `strategy.py`](../../../src/memrelay/engine/llm/strategy.py) [memrelay `borrow_host.py`](../../../src/memrelay/engine/llm/borrow_host.py) [memrelay `byo_key.py`](../../../src/memrelay/engine/llm/byo_key.py)

#### Experimental ontology: units and clustering

| Concept | Operational definition | Role in assignment/analysis |
|---|---|---|
| **Task** | Frozen problem statement, repository revision, environment/image, protected tests, budgets, and grading contract | Blocking factor; repeated runs on one task are correlated |
| **Run** | One agent attempt on one task under one assigned arm, fresh workspace/state, and one attempt ID | Observation unit for run endpoints; experimental unit only in single-run designs |
| **Replicate** | Preregistered independent rerun of a task×arm×configuration with fresh model randomness and mutable state | Nested in task×agent×model×arm; not independent if it shares a history |
| **History** | Ordered, immutable manifest of episodes, writes, revisions, actors, scopes, and validity intervals available before a probe | Assignment/experimental unit whenever earlier episodes can affect later outcomes |
| **Repository** | Versioned code lineage and authorization boundary, not merely an owner name | Clustering and governance level; tasks are nested/cross-classified here |
| **Model configuration** | Provider, model ID/version/fingerprint, reasoning effort, sampling, tool policy, and date | Randomization stratum or separately reported population; not pooled silently |
| **Sequence** | Ordered tasks plus their history-generation policy, state transitions, and probe positions | Analysis unit for cumulative/longitudinal estimands |

- **Assignment unit:** the entity randomized: run for independently provisioned single-session trials; entire history/sequence for longitudinal trials; collaborating team/history for cross-agent trials; authorized repository relation cluster for any future cross-repository trial.
- **Experimental unit:** smallest independently assigned entity. It equals the assignment unit, never a tool call, retrieval result, test, token, or turn.
- **Observation unit:** one recorded measurement: run success/cost, retrieval query, memory write, test result, or sequence outcome. Observation units do not define independent N.
- **Analysis unit:** run for the primary single-session ITT contrast; history/sequence for longitudinal and cross-agent contrasts; authorized relation cluster for future cross-repository contrasts. Retrieval analyses aggregate at query then bootstrap/permutation at the experimental unit.
- **Clustering:** turns/retrievals within run; replicates/runs within task; tasks and histories within repository; tasks within sequence; collaborating agents within history; repositories within authorized relation clusters; temporal provider batches within model configuration. Models/products are prespecified strata unless enough independently sampled levels support population inference.

The manifest must materialize every level and parent ID. Uncertainty uses randomization inference or models matching assignment, with task, repository, history/sequence, and provider-time clustering as applicable. Effective N is the number of independent assignment units.

#### Longitudinal histories and estimands

Only these history regimes are permitted, and each is reported separately:

1. **Independent histories (`H_ind`):** each arm independently generates a history from the same frozen task distribution but no shared mutable artifacts. Estimand: total effect of assigning the complete memory policy, including altered history creation, on later sequence utility. This is the most ecological and least paired regime.
2. **Identically seeded/replayed histories (`H_replay`):** one arm-blind, pre-treatment episode bundle is frozen, hashed, copied read-only to every arm, and writes are disabled or discarded before the probe. Estimand: controlled effect of retrieval/rendering access to identical prior evidence. Provider seeds are recorded but do not imply deterministic trajectories.
3. **Treatment-generated histories (`H_dynamic`):** the whole sequence is randomized before episode 1 and each arm's writes influence later episodes. Estimand: ITT effect of the dynamic memory policy over a stated horizon, including beneficial/harmful feedback. No later conditioning on memory calls, retrieved content, or surviving histories.

Never replay a treatment-generated history into control and call it a total-policy effect; never share writable state, caches, worktrees, credentials, or graph namespaces across arms. Durable memory has no credible washout, so cross-over designs are disallowed unless the history is immutable `H_replay`. Attrition, failed history generation, and unavailable probes remain assigned outcomes. Per-use and mediation analyses are exploratory because retrieval/use is post-treatment. [ICH E9(R1) estimand guidance](https://database.ich.org/sites/default/files/E9-R1_Step4_Guideline_2019_1203.pdf)

#### Reproducible arms and ablations

| Code | Exact intervention | Purpose/family |
|---|---|---|
| `N0` | Memrelay/tool unavailable; equivalent nonmemory startup and budget | No-memory baseline; confirmatory comparator only if tool availability is part of the product policy |
| `E0` | Tool visible; every read returns the canonical zero-item response immediately; writes discarded | Empty/interface control; mechanism family |
| `YL` | Same tool and call accounting; empty response delivered by preregistered latency-yoke schedule sampled before outcome from treatment-pilot strata | Latency-yoked empty control; primary comparator for retrieval benefit |
| `MI` | Same format, source count, token envelope, and latency as the pre-outcome treatment-retrieval envelope frozen below; evidence independently certified irrelevant, valid, scope-authorized, and non-instructional | Matched-irrelevant negative control; confirmatory mechanism family |
| `TR` | Actual production extraction, storage, retrieval, rerank, provenance, validity, and rendering | Treatment |
| `OR` | Curated minimum necessary evidence, treatment-matched format/token envelope, no hidden solution | Oracle ceiling; exploratory unless powered/preregistered |
| `AI` | Actual retrieval pipeline with relevant candidates masked/replaced by certified irrelevant candidates | Irrelevant-evidence ablation isolating pipeline/context effects; exploratory |
| `WO` | Writes enabled, reads unavailable | Ingestion overhead ablation; exploratory |

`E0`, `YL`, and `MI` are distinct arms; “C1” must not denote more than one. The yoke schedule is generated from a blinded pilot or independent treatment runs and frozen by task/provider strata; it cannot use the paired treatment outcome. `MI` passages pass injection and accidental-relevance review. Oracle evidence cannot include the patch or protected-test answer.

**Treatment-parity invariant.** Within every randomized block, both arms use the same local Copilot SDK/CLI releases, signed-in Copilot identity, exact catalog model ID, reasoning effort, context tier, system/user prompt bytes, built-in Copilot memory disabled, cross-session store disabled, working-directory layout, available/excluded tools, permission decisions, MCP tool schema, network policy, concurrency slot, randomization-independent limits, timeout, and retry policy. The OpenAI key is absent from both agent environments. Only the assigned memrelay access/content behavior differs. A parity hash over these fields is checked before launch; mismatch is a pre-exposure infrastructure failure, never a run.

#### Matched-irrelevant and yoke envelope freeze

The `MI` arm's token, source, and latency envelopes are frozen before any treatment outcome exists, from pre-outcome pilot strata, and are never matched to treatment outcomes:

- **Source strata:** envelopes are computed per prespecified stratum (task family x observed Copilot model x scope) from `TR`-arm retrieval observed in the blinded pilot **before** outcomes are graded, or from arm-blind escrow statistics; only pre-outcome retrieval shape (source counts, per-source and total token lengths, delivery latency) is used, never whether the run succeeded.
- **Frozen envelope:** each stratum fixes a target source count, a per-source and total token envelope, and a delivery-latency distribution; `MI` passages are drawn from the certified-irrelevant corpus to hit that stratum envelope.
- **Tolerances:** `MI` uses exact source count, total tokens within ±10%, and delivered latency within ±15% or 200 ms; sensitivity reruns use token 5–20% and latency 10–25%. Out-of-tolerance draws are rejected and resampled.
- **Substitution rules:** if a stratum has no in-tolerance irrelevant passage, substitute from the nearest coarser stratum (drop provider, then drop task family) and record the substitution; if none exists, the stratum is under-covered and excluded from the `MI` confirmatory family rather than matched loosely. The `YL` latency yoke follows the same pre-outcome, stratum-frozen derivation and likewise never uses the paired treatment outcome.
- **Prohibition:** treatment success, cost, or quality never enter envelope construction; any envelope value derived after outcomes exist invalidates the `MI`/`YL` comparison for that stratum.

#### Nested scope contrasts and assignment

Scopes are monotone sets: `S` current session; `P` authorized prior sessions by the same agent/configuration; `A` authorized prior sessions from other agents in the same repository; `R` explicitly authorized related repositories.

| Contrast | Exact interventions | Assignment unit | Family |
|---|---|---|---|
| Session retrieval | `TR(S)` vs `YL(S)` on independent fresh runs | run within task×model×replicate block | Confirmatory primary |
| Cross-session increment | `TR(S+P)` vs `TR(S)` with identical `H_replay`, or separately dynamic `H_dynamic` policies | whole history/sequence | Confirmatory longitudinal; replay and dynamic estimands separate |
| Cross-agent increment | `TR(S+P+A)` vs `TR(S+P)` | collaborating team/history sequence | Exploratory until adapter parity and interference scenarios pass; may become a separately powered confirmatory family after ratification |
| Cross-repository increment | `TR(S+P+A+R)` vs `TR(S+P+A)` | authorized repository-relation cluster/history | **Not currently eligible.** Exploratory/confirmatory only after per-record governance gates pass |

Do not estimate an incremental broader-scope effect by comparing unrelated tasks or histories. Each wider contrast uses the same narrower content plus one scope, identical probe distribution, and assignment at the highest interference level. The primary family cannot mix replay and dynamic histories.

#### Research-owned initial agent, model, task, and history matrix

The study runs local agent processes only, and every task-agent inference call goes through the GitHub Copilot SDK under the owner's current Copilot subscription. Inspect never supplies a model, and no alternate task-agent client exists in this matrix.

**Experiment-start catalog lock.** With the exact Python SDK wheel, bundled Copilot CLI runtime, signed-in GitHub identity, plan/policy context, and timestamp recorded, call `CopilotClient.list_models()` before task enrollment. Persist the complete native response as a content-addressed JSON artifact and a canonical projection containing every returned model ID, display name, capability/support field (including reasoning-effort and vision support), context/other limits, and billing metadata the SDK returns; record an explicit `unavailable` for fields not exposed. Hash both artifacts. The public GitHub model list is a contemporaneous cross-check only: GitHub states that availability depends on plan and surface and can change, while the SDK states that `list_models()` returns the models available to that runtime. [Copilot supported models](https://docs.github.com/en/copilot/reference/ai-models/supported-models) [GitHub Copilot SDK](https://github.com/github/copilot-sdk) [Copilot SDK Python](https://github.com/github/copilot-sdk/blob/main/python/README.md)

**Research-time preflight, not a catalog substitute:** on 2026-08-05 this machine had authenticated GitHub Copilot CLI 1.0.78 but did not have the Python `github-copilot-sdk` distribution installed. This workflow did not install or query an unpinned SDK and therefore does not fabricate “currently available” IDs from the public documentation. Catalog capture is the first implementation-stage gate and must occur again at actual experiment start.

**Deterministic selection rule.** On the observed catalog only, discard models that cannot complete the frozen local SDK tool/permission/conformance smoke, do not support the required context, or cannot accept one common reasoning setting. On an arm-blind `N0` synthetic qualification set, select `M0` by highest executable-success rate, then lower median Copilot AI-credit consumption, then lower median active latency, then lexicographic model ID. Freeze its exact returned ID, capability record, context tier, and one supported reasoning effort before treatment assignment. Select at most two secondary models without looking at any `TR` result: `M1` is the lowest-credit eligible model whose qualification success is no more than 5 percentage points below `M0`; `M2` is the highest-success remaining eligible model, with ties broken by lower credits, lower latency, then ID. If no model qualifies for a role, omit that role. Never invent or substitute a public model name.

| Cell | Exact runner and model | Memrelay path | Role |
|---|---|---|---|
| `R-COP-M0` | Python 3.11 `github-copilot-sdk`, pinned release and bundled local Copilot CLI runtime over `stdio`; exact observed `M0` ID; frozen common reasoning effort/context tier; explicit identical permission, tool, prompt, and budget policy | Existing local Copilot MCP registration plus canonical Copilot event ingestion | Sole primary task-agent cell |
| `R-COP-M1` | Same SDK/runtime/configuration contract; exact observed `M1` ID | Same path; separate model stratum | Optional lower-consumption generalization |
| `R-COP-M2` | Same SDK/runtime/configuration contract; exact observed `M2` ID | Same path; separate model stratum | Optional higher-capability generalization |

All treatment/control comparisons within a stratum use the identical model ID, reasoning setting, context tier, prompt/system message, Copilot built-in-memory setting, session-store setting, tools and permission handler, limits, and runtime release. Only memrelay access/content differs. If a frozen model becomes unavailable or its capability record changes, pause the affected block; do not substitute mid-block. A replacement creates a new catalog artifact, protocol version, and separately reported model stratum.

The repository fit is concrete rather than assumed: memrelay's Copilot provider supplies MCP registration and canonical event ingestion. Cross-agent scenarios mean distinct local Copilot SDK sessions/actor IDs sharing only authorized memrelay state; they do not introduce a second task-agent framework or provider.

#### Task and history families

The expanded corpus freezes 32 tasks, four in each family; the minimal pilot takes two blinded tasks from every family. Half are short and half medium. Each family contains synthetic tasks and, where licensing and reproducibility permit, public-derived tasks rebuilt on a clean cutoff.

| ID | Family and plausible memory mechanism | Scope/history | Primary contrast | Design |
|---|---|---|---|---|
| `F1` | Short intra-session recovery: a non-obvious tool result, build flag, or repository invariant discovered early must be reused after context pressure | `S`, one run | `TR(S)` vs `YL(S)` | Paired/repeated task blocks |
| `F2` | Medium intra-session implementation: diagnosis, edit, and test phases require recall of an earlier constraint or failed approach | `S`, one run | `TR(S)` vs `YL(S)` | Paired/repeated task blocks |
| `F3` | Cross-session replay: a prior diagnostic/session establishes an API contract, environment gotcha, or accepted design needed by a later patch | identical read-only `H_replay`, `S+P` | `TR(S+P)` vs `TR(S)` | Paired/repeated history blocks |
| `F4` | Cross-session dynamic learning: episode 1 discovers and writes a fact; episodes 2–3 must reuse or revise it | writable `H_dynamic`, `S+P` | whole broad-memory policy vs session-only policy | Independently blocked whole sequences |
| `F5` | Stale/contradictory/superseded evidence: old guidance conflicts with a newer valid decision; the agent must prefer provenance and validity, or abstain | replay and separate dynamic strata | production `TR` vs corresponding narrower/yoked policy | Paired for replay; independent for dynamic |
| `F6` | Cross-agent handoff: one local agent diagnoses or maps the code; another local agent implements without its transcript, using only authorized collective memory | team-level `S+P+A` | `TR(S+P+A)` vs `TR(S+P)` | Independently blocked whole teams/histories |
| `F7` | Short negative control: issue, code, and tests are self-contained; memory should not change success and may add overhead | irrelevant or empty history | `TR(S)` vs `YL(S)` | Paired/repeated task blocks |
| `F8` | Medium negative control: realistic prior history is certified irrelevant to the patch; useful behavior is to ignore it | matched irrelevant `H_replay` | `TR(S+P)` vs `TR(S)` | Paired/repeated history blocks |
| `F9` | Later cross-repository transfer: an authorized upstream/downstream contract or migration lesson is needed in a related repository | `S+P+A+R` | broader vs narrower scope | Independently blocked repository-relation clusters; **not enrolled before governance gate** |

These scenarios fit the short-to-medium envelope: repeated environment gotchas, hidden build constraints, prior failed approaches, API migrations, adjacent fixes, cross-session design decisions, and diagnostic-to-implementer handoffs can plausibly benefit from persistent recall without requiring days-long autonomous work. `F7/F8` are mandatory falsification controls: a system that appears to help equally on self-contained or irrelevant-history tasks is probably measuring extra context, tool salience, or grading bias rather than useful memory.

#### Assignment rule: both designs, selected by estimand

“Paired” never means reusing mutable state or crossing one agent from treatment to control. In `F1/F2/F3/F5-replay/F7/F8`, each task or immutable history creates a matched set of fresh, isolated runs. Arm order and provider-time slot are counterbalanced, calls remain independently stochastic, and paired task-level differences improve precision for the controlled access estimand. Repeats are repeated measurements of the task, not independent task N.

`F4/F5-dynamic/F6` use independent blocked assignment because treatment changes the history or creates interference. Randomize the entire sequence/team before episode 1 within Copilot-model×family×difficulty×provider-time blocks; analyze at sequence/team level. No crossover or washout is credible. `F9`, when authorized, randomizes repository-relation clusters. This design-by-estimand rule replaces the former owner choice between one global paired or independent design.

#### Dual-objective estimation, not an intersection-only pilot

Reliability and efficiency are co-equal objective domains:

- **Reliability distribution:** executable success risk difference, regression-free quality difference, failure taxonomy, and stale/contradictory evidence-induced harm.
- **Efficiency distribution:** total product marginal cost per assigned unit and active wall-clock time per assigned unit, with token/tool/storage/telemetry components reported separately.
- **Guards:** success, regression-free quality, scope/canary/tamper/deletion safety, and harm tails. Efficiency is never celebrated when savings arise from early failure or harmful memory.

The pilot reports task-level and family-level effect distributions, medians, means, 10th/25th/75th/90th percentiles, worst observed tails, heterogeneous effects by memory-necessity/family/observed Copilot-model stratum, and joint reliability–cost–time Pareto plots with confidence or credible intervals. It does **not** force a single intersection-union primary, scalar utility, or binary “worthwhile” threshold. Pareto-dominated policies are identifiable without value weights; trade-off points remain explicit.

Candidate practical thresholds are generated after the blinded pilot from: baseline variability and failure costs; realistic run volume; observed cost/time distributions; the smallest effect that changes an actual operating decision; and conservative harm/safety bounds. The derivation rule and a finite candidate set are written before decoded efficacy is released. Any promotional confirmatory claim then freezes one candidate set, endpoint graph, target population, and fresh holdout before enrollment. No confirmatory threshold may be selected because it makes the pilot positive. Implementation, conformance work, and exploratory trials proceed now without a practical-effect threshold.

#### Executable dual-objective endpoint hierarchy

There is no owner-selectable endpoint mode in the pilot: reliability **and** efficiency are both in scope, with success/quality/harm/safety as guards against false savings. Existing IDs are retained so earlier traceability work remains stable; the word `PRIM` in `EP-PRIM-SUCCESS` is a legacy identifier, not a declaration that success is the sole pilot primary.

| Endpoint ID | Construct | Pilot output |
|---|---|---|
| `EP-PRIM-SUCCESS` | ITT executable-success risk difference per assigned unit | Mean, task/family distribution, heterogeneous effects, interval |
| `EP-QUAL` | Protected regression-free binary and bounded quality score | Difference distribution and lower harm tail |
| `EP-COST` | Total product marginal cost per assigned unit | Difference, ratio, component quantities, price sensitivity |
| `EP-WALL` | Active wall time per assigned unit | Difference, ratio, timeout mass, tail quantiles |
| `EP-HARM` | Stale/contradictory/misleading-evidence-induced failure | Risk difference, attribution uncertainty, worst-case bound |

Safety gates remain separate immutable IDs: `SG-SCOPE`, `SG-CANARY`, `SG-TAMPER`, and `SG-DELETE`. The private-data-specific `SG-CONSENT` is unnecessary in the initial synthetic/public program; its ID is reserved and inactive rather than silently repurposed. Corpus-license, provenance, canary, secret-scan, and publication-hygiene checks are ordinary eligibility gates. Exploratory families remain `EX-RET`, `EX-MOD`, `EX-SCOPE`, `EX-MECH`, and `EX-PROC`.

The pilot estimates every endpoint and the joint reliability–cost–time Pareto frontier with marginal and simultaneous descriptive intervals. It makes no confirmatory efficacy claim, spends no promotional alpha, and does not test a practical bound selected after seeing effects. Safety and budget monitoring may stop collection at any time without declaring benefit.

Before a later confirmatory claim, candidate thresholds generated by the prespecified pilot rule are reviewed against operating costs and failure consequences, then frozen with a fresh holdout, endpoint graph, family, alpha, power, and claim language. If several confirmatory claims are tested, use a closed/graphical multiplicity procedure with compatible simultaneous intervals; Holm is the equal-weight special case. No marginal interval or adjusted p-value may substitute for a compatible decision bound. [Strassburger & Bretz](https://pubmed.ncbi.nlm.nih.gov/18618415/) [FDA multiple-endpoint guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/multiple-endpoints-clinical-trials) [COS preregistration](https://www.cos.io/initiatives/prereg)

**Estimator by design.** Randomization inference follows the actual assignment mechanism. Paired/repeated families (`F1/F2/F3/F5-replay/F7/F8`) use fresh-run matched task/history sets, task-level paired differences, paired permutation/sign-flip inference, and task-cluster bootstrap intervals. Ratio outcomes use within-set log ratios when denominators are positive and separately report zero-cost mass; no arbitrary constant is added. Independently blocked families (`F4/F5-dynamic/F6`) randomize and permute whole sequences or teams inside frozen blocks and use equal-sequence/team-weighted effects. Future `F9` uses repository-relation clusters. Replay, dynamic, cross-agent, runner, and model-tier estimands are never silently pooled.

Secondary hierarchical models use only frozen pretreatment task, repository, family, model, and provider-time strata; covariance is cluster-robust at the assignment unit with CR2/bias reduction. With fewer than 20 independent clusters, report wild-cluster-bootstrap intervals and mark material disagreement as unstable. Repeated runs increase precision but do not inflate task or history N. The assignment, estimator, resampling unit, and power simulation always use the same design branch.

**Frozen attempt/retry and ITT outcome rules.** The ITT analysis set is every assigned experimental unit; outcomes are assigned by the table below before unblinding, and the more favorable of multiple attempts is never substituted.

| Terminal condition | ITT success/quality outcome | Harm/cost/wall treatment | Retry rule |
|---|---|---|---|
| Executable success | success = 1; quality = measured | actual cost; wall = measured | none |
| Agent crash / no patch / wrong patch | success = 0; quality measured (0 if protected checks broken) | actual cost incl. zero-cost; wall = measured or budget cap | none |
| Timeout / budget exhausted | success = 0; grade the last durably persisted patch for quality, or set quality = 0 if no gradeable patch exists | wall censored at the budget cap; cost = actual | none |
| Grader failure | re-grade the identical frozen artifact; if unresolved, outcome is unavailable and bounded worst/best and by the registered pattern-mixture analysis | cost/wall from the run remain observed | deterministic re-grade only, never a fresh agent run |
| Infrastructure failure, independently verified pre-exposure (before the agent acts) | the failed launch is not exposed to treatment; if the registered `infra_retry=1`, the sole linked replacement supplies the assigned unit's ITT outcome, otherwise the outcome is unavailable and bounded | failed-launch operations reported separately; replacement cost/wall supplies the endpoint | exactly one linked replacement only when `infra_retry=1`, arm-blind and automatic |
| Infrastructure failure after agent start | success = 0; quality from the last gradeable patch, otherwise 0 | actual cost; measured wall or cap | none |
| Unavailable / missing primary field | evidence-integrity blocker; worst/best and registered pattern-mixture bounds | observed values retained; unavailable values bounded | none |
| Linked pre-exposure replacement exists | use the sole replacement as the assigned unit's outcome; retain both attempt records and the link | replacement endpoint values; failed-launch overhead secondary | no additional retry |

A verified pre-exposure infrastructure replacement is the only attempt replacement permitted; whether it exists (`infra_retry=0` or `1`) is registered before enrollment. It must be triggered automatically by an arm-independent, pre-exposure, arm-blind certification, and both records and their link are retained. After any treatment exposure, the first assigned attempt is terminal. Favorable substitution, best-of-N scoring, repeated “until success” execution, and dropping unfavorable attempts are prohibited evidence-integrity violations.

**Quality and harm operational definitions.**

- **Regression-free quality (`EP-QUAL`).** Scale: a bounded score in the interval 0 to 1 per assigned run, and a derived binary no-regression indicator (score = 1). Executable source: the fraction of the task's frozen, protected regression checks that pass on the agent's final patch under the grading contract's protected-test set and digest; any protected-test tamper or out-of-scope patch scores 0. Aggregation: per-run score averaged within task, then combined across tasks with the same weights and randomization inference as the primary; the confirmatory contrast is the risk difference in the no-regression indicator between arms, with the continuous mean reported secondarily. Attribution: a regression is a protected check that passes under `YL(S)`/baseline but fails under the run; only checks frozen before assignment count, so newly added checks cannot manufacture a regression. Denominators: every assigned run in the ITT set, including failures (a non-successful run with broken protected checks is a regression, not a missing value). Missingness: unavailable protected-test results are an evidence-integrity blocker bounded worst/best, never imputed as passing. Margin: non-inferiority at `-quality_NI_margin`.
- **Stale/harmful-evidence-induced failure (`EP-HARM`).** Scale: a per-run binary indicator that the run failed and the failure is attributable to memory-supplied evidence (stale, contradictory, poisoned, or misleading retrieved content that entered the agent's context and is causally linked to the failing action by the frozen attribution rubric), aggregated to the risk difference `TR(S)-YL(S)`. Executable source: the terminal executable failure joined to the immutable retrieval/event record showing the offending evidence was retrieved and used before the failing edit. Aggregation and denominators: the same ITT set and weighting as the primary; the denominator is all assigned `TR`-arm runs and their yoked comparators, so the induced-failure rate is not diluted by excluding successes. Attribution: two blinded raters apply a frozen rubric to the retrieval-and-action trace; ties and disputes are adjudicated and sensitivity-analyzed. Missingness: runs lacking the retrieval record needed for attribution are counted as induced-harm in the worst-case bound. Margin: one-sided no-harm at `harm_margin`; any confirmed `SG-*` event remains a separate zero-tolerance gate regardless of this rate.

#### Simulation-based power contract

Delete the prior "30–50 pairs" heuristic. Before confirmatory enrollment, simulate the exact assignment, estimator, mode-specific confirmatory family, missingness mechanism, and sequential rule over the fully enumerable grid below. Every axis is a finite set of discrete levels, so the grid is a finite Cartesian product of named cells; ranges are replaced by explicit levels.

- **Baseline success `p0`** (levels): 0.10, 0.30, 0.50, 0.70.
- **Within-task dependence for the default independent-run design:** task latent-scale ICC levels 0.05, 0.25, 0.50. The symbol `p_disc` is not used in this design because there are no observed pairs. If and only if the owner instead registers the one-to-one paired design, replace this axis with discordant-pair probability `p_disc` at levels 0.10, 0.25, 0.40, 0.50 and use the paired estimator.
- **Practical effect** = ratified `success_MPID` (levels): 0.03, 0.05, 0.08, 0.10, plus the null 0.00 for type-I calibration.
- **Other nested/crossed ICCs** (levels each): repository 0.01, 0.10, 0.30; history/sequence 0.05, 0.20, 0.50; provider-time batch 0.00, 0.05, 0.20.
- **Attrition/infrastructure terminal rate** (levels): 0.00, 0.05, 0.10, 0.15, each crossed with a named missingness mechanism below.
- **Endpoint correlation structure** (named finite levels): (A) independent identity matrix; (B) exchangeable Gaussian-copula matrix with `rho` 0.25 or 0.50; (C) negative exchangeable matrix with `rho=-0.25` for the three-endpoint family or `rho=-0.20` for a five-endpoint family; and (D) a fixed two-factor matrix generated by loadings 0.20 on a global factor for every endpoint, +0.50 on a reliability factor for success/quality, -0.50 on that factor for harm, and +0.50 on a resource factor for cost/wall, with endpoint-specific residual variance making each diagonal one. Each construction is positive definite by design and is verified numerically; a failed check rejects and logs the cell rather than silently repairing it.
- **Cost marginal levels:** zero-cost point-mass probability 0.00, 0.05, 0.15 crossed with log-normal log-scale standard deviation 0.25, 0.75, 1.25.
- **Wall marginal levels:** gamma coefficient of variation 0.25, 0.75, 1.25, with its mean set by the sampled arm ratio and censoring at the registered timeout.
- **Fixed per run**: `alpha_total`, `power_target`, replicate count, task/repository allocation, cost/time skew, censoring rule, and any ratified sequential boundaries.

**Named data-generating process.** Binary endpoints (`EP-PRIM-SUCCESS`, the `EP-QUAL` no-regression indicator, `EP-SUCC-NI`, `EP-HARM`) use a Bernoulli-logit GLMM with nested, mean-zero Gaussian random effects for repository, task-within-repository, history/sequence, and provider-time batch, whose variances reproduce the selected latent-logit ICCs. `EP-COST` uses the enumerated hurdle-log-normal marginal; `EP-WALL` uses the enumerated gamma marginal. Both share the same random-effect hierarchy. A Gaussian copula couples endpoints using exactly one named positive-definite structure above. No nearest-positive-definite repair is allowed because it would change the registered cell. Timeouts censor wall-time at the budget cap, and the enumerated zero-cost mass remains in the cost marginal.

**Named missingness mechanisms.** Each attrition level is crossed with: **MCAR** (loss independent of arm and outcome); **MAR-2** and **MAR-4** (loss odds multiplied by 2 or 4 for a frozen observed high-difficulty pre-treatment stratum); **MNAR-2** and **MNAR-4** (loss odds multiplied by 2 or 4 when the would-be outcome is failure); and **ARM-DIFF** (a frozen differential telemetry-loss stress level). Probabilities are calibrated to the selected overall attrition level and clipped only by a registered deterministic calibration algorithm. Every cell carries failed/missing-as-worst, best, and pattern-mixture bounds.

**Pilot-derived plausible cells for a future confirmation.** The blinded nuisance report generates a named allowlist of jointly plausible grid cells using a rule fixed before decoded effects are released. A future confirmatory N is sized from the worst cell inside that set; excluded cells remain stress tests. The set, rule, rationale, and winning worst-case cell are registered before fresh-holdout enrollment.

Run at least 10,000 Monte Carlo trials per retained cell with a reproducible seed set and an independent implementation spot-check; select future N from the worst retained plausible cell, then publish power curves and budget.

**Blinded pilot input derivation.** Use arm-blind escrow computation for all families. A role-separated local script holds the arm key and releases task ICC/paired discordance as appropriate, sequence/team ICC, attrition, endpoint correlation, cost/time distributions, and arm-independent infrastructure failure without decoded arm effects. The computation, code, inputs, and output hashes are retained locally. Pilot effects are later released for exploratory estimation and threshold-candidate generation, but they never enter the future confirmatory analysis or select favorable tasks. Candidate thresholds and the fresh holdout are frozen after the exploratory report and before confirmation.

### Design Principles and Best Practices

#### Blinded memory-necessity and shortcut rubric

Two domain raters who did not author the task inspect only the current-context packet and a separately labelled oracle packet:

- `0`: current context fully reveals the needed fact or task is not memory-sensitive;
- `1`: memory is convenient but unlikely to change correctness or material effort;
- `2`: memory is materially useful, but a documented shortcut in issue/code/tests/tooling reveals it;
- `3`: prior evidence is needed to avoid a specific failure or major search burden and no shortcut is found;
- `4`: prior evidence is necessary for executable correctness/safety under the frozen budget.

Pilot eligibility uses median score ≥3, neither rater <2, zero unresolved shortcut flags, and blinded oracle gain ≥20 percentage points or ≥20% active-time reduction; sensitivity reports median ≥2.5/3.5 and oracle gain 10–30%. The shortcut audit searches issue text, code, docs, exposed git history, public patches, test names/messages, filenames, and lexical canaries. Task authors cannot rate necessity, adjudicate their evidence, or analyze decoded outcomes. The exploratory matrix may be predominantly synthetic; any later ecological claim requires a separately frozen natural-task stratum and replication.

#### Contamination and holdout contract

1. **Temporal eligibility:** freeze task creation, code revision, issue, oracle patch, and protected tests after a declared cutoff. Record each provider/model's official cutoff statement URL, retrieval date, and content hash. If provider provenance/cutoff is unavailable, mark `cutoff_provenance=unavailable`; the task is not confirmatory unless it is access-controlled, created after model release, and passes independent leak checks.
2. **Duplicate detection:** exact hashes plus normalized token/AST fingerprints, MinHash/LSH, and semantic embeddings compare issue, patch, tests, and solution summary against public issues/PRs, benchmark train/dev/test, prior study tasks, and model-visible repository history. Pilot quarantine uses semantic cosine ≥0.92 or normalized token overlap ≥0.80, with sensitivity at 0.88–0.96 and manual review of near matches.
3. **Canary search:** use unique nonsecret strings embedded only in protected metadata; before enrollment search authorized public code/web indexes and task corpora, then run a no-context model probe. Any exact canary hit triggers quarantine and investigation, not task relabelling.
4. **Disposition:** `eligible`, `quarantined_pending_review`, or `rejected`, with immutable reason, reviewer, evidence hashes, and affected splits. Rejected tasks never migrate into confirmation under a new ID.
5. **Holdout:** separate development, pilot, confirmatory, and replication repositories/tasks/curators/access groups. No prompt, retrieval, threshold, or model selection on confirmatory outcomes. Holdout access is logged; any early exposure invalidates the affected confirmatory family.

SWE-rebench explicitly targets decontaminated task collection, while RE-Bench asks users to protect solutions from model training; neither removes the need for local provenance and leak audit. [SWE-rebench](https://arxiv.org/abs/2505.20411) [RE-Bench](https://github.com/METR/RE-Bench)

#### Benchmark grading contract

Every scenario freezes grader SHA, protected-test digest, image digest, dependency lock/mirror snapshot, network policy, timeout, resource limits, allowed patch roots/file/line limits, and expected exit taxonomy.

- **Flaky tests:** before enrollment run clean baseline and gold patch five times each in fresh containers. Eligibility is 5/5 stable and gold 5/5 passing. At grading, a failing candidate is rerun twice only to classify a preregistered flaky signature; score remains the policy-defined aggregate, never best-of-three.
- **Dependency nondeterminism:** lock dependencies and disable network or use a dated immutable mirror. Resolver/download failure is infrastructure only when the unchanged baseline/gold fails under the same image and a health artifact confirms the dependency fault; otherwise it is agent failure.
- **Test tampering:** hash protected paths before/after. Modification, deletion, bypass, or test-selection manipulation is agent failure and a safety endpoint, even if visible tests pass.
- **Network failure:** an allowed external service failure is infrastructure only with independent timestamped health/network evidence and identical policy across arms. Unauthorized network use, ignored offline requirements, or agent-caused exhaustion is agent failure.
- **Patch scope:** changes outside the task allowlist, generated artifacts, vendored dependencies, or ratified file/line ceiling fail unless an exception was frozen before assignment.
- **Image drift:** digest/architecture/kernel mismatch before agent start makes the run infrastructure-ineligible; drift discovered after start remains preserved and triggers family quarantine until impact is blinded-assessed.
- **Retries:** no retries for agent failures, timeouts, or post-start infrastructure failures. Exactly one fresh linked retry is permitted only for an independently verified, arm-blind pre-exposure infrastructure failure; the original remains preserved, the retry carries a linked attempt ID, and the retry outcome is never chosen for being favorable. See the frozen attempt/retry and ITT outcome table.
- **Classification:** `agent_failure`, `infrastructure_failure`, `grader_failure`, `policy_violation`, or `success`. Ambiguous cases are blinded-adjudicated; unresolved ambiguity is sensitivity-analyzed both ways. Zero-token/zero-cost failures remain in denominators and cost distributions. The ITT outcome for each class is fixed by the frozen outcome table; favorable attempt substitution and best-of-N scoring are prohibited evidence-integrity violations.

SWE-bench uses containerized executable evaluation and identifies reproducibility/resource requirements, but each imported benchmark still needs this local contract. [SWE-bench repository](https://github.com/SWE-bench/SWE-bench)

#### Blinded views and adjudication

Create a deterministic blinded-view transform with version/hash that: maps arm/tool/server names to neutral IDs; removes assignment fields, memory backend/config names, namespace labels, source-owner names, provider request IDs, raw timing, and billing; normalizes response wrappers and whitespace; token-buckets evidence length; randomizes candidate display order only for relevance annotation while preserving rank in a separately scored machine table; and replaces timestamps with relative validity bins. Preserve executable patch/code and content needed for judgment.

Likely leaks include tool presence/count, empty-response wording, latency, evidence length/format, citation prefixes, source paths, model error strings, trace span names, and characteristic provenance fields. Pilot leak classifiers and human guesses on transformed views; revise transformation without outcomes. Every adjudicator guesses arm and confidence after each case. Report accuracy and interval against chance, confidence calibration, mutual-information/leak classifier AUC, and by-provider/task subgroup. Calibrate raters on at least 20 nonstudy cases and double-label at least 25%; report raw agreement, Krippendorff's alpha or weighted kappa, category confusion, adjudication rate, and rater-specific errors. Sensitivity analyses use executable-only outcomes and vary disputed labels. Guessed-arm exclusion is **diagnostic only** because the guess is post-treatment and filtering can induce collider bias. Use prespecified leakage bounds; guess/confidence are exploratory covariates, never a filter that manufactures support.

### Scalability and Performance Patterns

#### Conditional telemetry completeness contract

**Zero-missingness primary fields (100% of assignments, every arm/model/task/failure):** experiment/run/task/replicate/history/sequence/repository/model IDs; opaque arm code; assignment record/hash; workspace/image/repository/prompt/tool-policy/budget/grader hashes; start/terminal timestamps; terminal class; executable outcome; patch hash or explicit `no_patch`; attempt/retry lineage; exclusion/quarantine reason; expected-artifact inventory; reconciliation status. Any missing primary field is an evidence-integrity blocker, not imputed.

Conditional requirements are declared in a telemetry-domain×arm×model×event-class×task-type×failure-state matrix before execution:

| Condition | Required evidence |
|---|---|
| Every initiated Copilot SDK model request | native request/session ID where exposed, exact catalog model ID, status, `assistant.usage` record and API endpoint, input/cached-input/cache-write/output/reasoning token categories where exposed, AI credits/usage units, latency, rate-limit/quota status; unsupported fields are explicit and bound affected claims |
| Every initiated framework-internal OpenAI API request | framework operation and parent memory event, OpenAI request/model ID, status, metered input/cached/output tokens, actual API cost from the versioned price/invoice record, latency/retries; credential fields prohibited |
| `E0/YL/MI/TR/OR/AI/WO` tool-capable arms | invocation count/status/latency; explicit observed zero only when instrumentation was active |
| Retrieval-producing `MI/TR/OR/AI` | ordered candidates, source scope/provenance/validity, channel/ranks/scores, selected/token counts, query and result hashes |
| Write-capable `TR/WO` and dynamic histories | input/output hashes, extractor/embedder/config, target scope, provenance, write status, invalidation links |
| Executable tasks | protected-test inventory/hash, command, exit, duration, per-test result, tamper and scope verdict |
| Failure/timeout/no patch | heartbeat, last successful event, error/exit source, resource/budget state, partial artifact inventory, cleanup status |
| Cost-comparable provider | native usage plus billed or versioned-price record and reconciliation lag/status |

Fields unsupported by a provider are `unavailable`, never null-as-zero. Report differential nonprimary missingness per matrix cell against a 1 percentage-point pilot alert and 0–2-point sensitivity. Bound every affected result with failed/missing-as-worst, best, and pattern-mixture analysis. OTLP's lack of end-to-end guarantee makes registry reconciliation mandatory. [OTLP](https://opentelemetry.io/docs/specs/otlp/)

#### Distinct cost estimands and non-conflated provider ledgers

- **Copilot task-agent consumption:** per assigned unit report requests, model calls, input/cached-input/cache-write/output/reasoning tokens when exposed, Copilot AI credits or legacy usage units, model ID, active/provider latency, throttles, quota rejections, allowance remaining at start/end, and reset/billing period. Report actual incremental cash only when an invoice/additional-usage charge identifies it. Otherwise report subscription consumption and resource quantities, plus normalized sensitivity under (a) published token-to-credit rates, (b) included allowance valued at plan subscription cost/allowance, (c) incremental-overage price, and (d) 0.5x/1x/2x shadow prices. A subscription-included request is neither a metered OpenAI API dollar charge nor economically “free.”
- **Framework-internal OpenAI API spend:** per memrelay/Graphiti operation report actual metered OpenAI input/cached/output tokens, request/tool charges, retries, model, service tier, region, and the versioned API price or invoice amount. This ledger contains only extraction, summarization, embedding, or other explicitly instrumented framework-internal calls; it contains no coding/task-agent inference.
- **Product marginal cost:** ITT difference/ratio in variable cost per assigned run and per valid success, formed from the two ledgers without merging their provenance: Copilot subscription consumption/sensitivity; actual framework OpenAI API spend; incremental local compute; storage reads/writes; network; and telemetry. Includes zero-cost failures and failed runs; shared fixed engineering is excluded.
- **Fully loaded operational cost:** product marginal cost plus amortized platform development, licenses, reserved/idle capacity, shared databases/collectors, monitoring, on-call/support, security/privacy operations, backup/deletion, and expected incident burden over a ratified volume and useful-life horizon.
- **Study cost:** all experiment-only spend: task curation, pilot and confirmatory runs, discarded/failed runs, harness work, compute/storage, adjudication, analysis, finance, legal, privacy, security, and independent replication. It is not a product unit-economics claim.

Each record carries currency, billing region, service date, tax treatment, discounts/credits, contract tier, price-table version/hash/effective interval, estimated-versus-invoiced status, invoice ID/lag, and FX source/date. Normalize the primary sensitivity view to USD at dated published pretax rates and report actual-invoice cost separately. Shared infrastructure uses CPU-seconds, GB-months, and request counts with alternative allocations. Fully loaded views use explicit volume/useful-life scenarios; sunk study cost is never hidden in marginal cost. Late invoices trigger a scheduled versioned reconciliation.

#### Frozen confirmatory cost and wall-time contract

`EP-COST` and `EP-WALL` use a single frozen denominator and clock; all other cost views (fully loaded operational cost, study cost, per-success and per-token cuts) are secondary and reported separately, never substituted for the confirmatory endpoints.

- **Confirmatory cost denominator:** product marginal cost **per assigned run** (ITT), including zero-cost and failed runs, defines `EP-COST`. Its evidence record is always decomposed into (a) Copilot tokens/requests/latency and AI credits or other native subscription units, (b) actual metered framework-internal OpenAI API tokens/USD, and (c) local variable resources. The pilot reports the components and subscription-normalized sensitivity surfaces separately. Any later confirmatory scalar ratio `TR(S)/YL(S)` must freeze one named Copilot allowance/shadow-price scenario before enrollment and may not be called actual cash cost unless invoice evidence supports it. Cost per valid success is secondary because conditioning on success is post-treatment.
- **Confirmatory wall clock:** `EP-WALL` is the ratio of **active agent wall-time per assigned run**, measured from the first agent action after workspace-ready to the terminal event on one monotonic clock recorded by the control plane. Provider-side latency inside an active call counts; control-plane provisioning and cleanup are excluded and reported separately.
- **Queue treatment:** time a run waits in the scheduler queue or under provider rate-limit backoff is excluded from `EP-WALL` (reported as a separate operational metric), and queueing policy is arm-balanced so it cannot differ by arm.
- **Censoring and timeout:** a run that hits a budget/timeout cap has wall-time censored at the cap and is a failure for success; record actual framework API spend, actual Copilot consumption units, and the frozen subscription-normalized sensitivity value to the cap. Censored wall-times use the same cap across arms and are analyzed by the prespecified censoring rule, never dropped.
- **Longitudinal horizon:** for `CF-XSESSION-REPLAY` and `CF-XSESSION-DYNAMIC`, cost and wall are summed over the registered sequence horizon (a fixed episode count) and reported per sequence; the horizon is never truncated at the point a difference appears.
- **Amortization:** confirmatory `EP-COST` is variable cost only; capital, setup, and shared fixed cost are excluded from `EP-COST` and appear only in the secondary fully loaded view over the ratified amortization horizon and target volume.
- **Currency, region, tax, discount, invoice lag:** framework API and local-cash records are normalized to one ratified base currency using the registered FX source/date, tax/discount treatment, and region. Copilot records additionally carry plan, allowance, billing period, included-versus-overage status, and the named normalization scenario. Estimated spend is reconciled against invoices; a late invoice triggers a scheduled versioned revision, never a silent overwrite. Estimated, subscription-normalized, and invoice-reconciled values remain distinct on every row.

Changing base currency, region, tax/discount treatment, amortization horizon, target volume, or price table versions the cost analysis; these are recorded scenario inputs, not prerequisites to the pilot.

#### Safety evidence with exposure denominators

Active safety gates (`SG-SCOPE`, `SG-CANARY`, `SG-TAMPER`, `SG-DELETE`) are zero-tolerance: one confirmed event blocks the affected scope. `SG-CONSENT` is reserved and inactive because private/personal data is prohibited. Because absence of observed events is not proof of safety, every active gate reports positive evidence with explicit denominators and detector characterization, not a bare no-events-seen statement.

- **Exposure denominator:** the number of independent opportunities for the event, defined per gate (assigned `TR` runs with retrieval for `SG-SCOPE`; runs whose context could reach a canary for `SG-CANARY`; runs with protected tests for `SG-TAMPER`; deletion requests for `SG-DELETE`). Every rate names its denominator.
- **Ascertainment coverage:** the fraction of exposures actually inspected by each detector (automated scan, canary tripwire, hash/tamper check, authorization-log audit, deletion verification), reported per gate; uninspected exposures are stated as a coverage gap, not assumed clean.
- **Detection sensitivity:** each detector's sensitivity is estimated from preregistered injected positives (unauthorized reads, fake canaries, simulated tamper, and undeleted residue), with catch rate and interval reported.
- **Injected-test scope:** the count, type, and placement of injected positives are registered before enrollment, span every arm and observed Copilot-model cell, and are excluded from efficacy denominators; a detector that misses an injected positive fails its own gate independent of the efficacy result.
- **Confidence upper bound on zero events:** when zero events are observed in `n` exposures, compute the one-sided 95% Clopper-Pearson upper bound `q_U` for the detected-event probability, then report `p_U = min(1, q_U/(c_L*s_L))`, where `c_L` and `s_L` are the registered conservative lower confidence bounds for ascertainment coverage and detector sensitivity. Also report the rule-of-three approximation `min(1, 3/(n*c_L*s_L))`. This requires the registered independent-detection model; violations are sensitivity-analyzed, and the bound, never zero, is reported.
- **Bounded claim language:** with zero observed events the only admissible statement is that the data are consistent with a true rate at or below the computed upper bound at the stated exposure, coverage, and sensitivity. Never state or imply safe, no-risk, or zero-rate; a wider bound (small n, low coverage, or low sensitivity) is reported as weak evidence, and the gate remains blocking on any single confirmed event.

### Integration and Communication Patterns

One root evidence record exists per run; longitudinal runs link by `history_id/sequence_id`, not one months-long trace. Raw native events are preserved beside normalized events. Required spans/events cover workspace provision, memory snapshot restore, agent invocation/turns, model calls, tool execution, memrelay note/search suboperations, grader, artifact persistence, cost reconciliation, and evidence reconciliation. Copilot SDK/CLI spans carry `agent.provider=github_copilot_sdk`, the exact catalog model ID, `credential.domain=github_copilot_subscription`, and `cost.source=copilot_subscription_usage`. Memrelay/Graphiti OpenAI spans use a separate service/resource and carry `agent.provider=framework_internal_openai`, `framework.operation`, and `cost.source=openai_api_metered`. A span can never claim both cost sources.

The local Collector applies a default-deny attribute allowlist, redacts authorization headers, token/key-like values, environment dumps, local usernames/paths, prompts/code/tool content, and provider request payloads, and keeps content capture off. Key names may appear only as nonsecret configuration metadata where necessary; key values never do. Reconcile SDK `assistant.usage` events, Copilot OTel token/cost attributes, framework OpenAI usage responses, memrelay events, and stage ledgers before lock. Treatment labels remain restricted until data lock. W3C Trace Context supports interoperable propagation; OpenInference supplies AI operation vocabulary but not experiment semantics. [Copilot SDK OpenTelemetry](https://github.com/github/copilot-sdk/blob/main/docs/observability/opentelemetry.md) [W3C Trace Context](https://www.w3.org/TR/trace-context/) [OpenInference](https://arize-ai.github.io/openinference/spec/)

### Security Architecture Patterns

Threats include cross-arm/cross-scope leakage, persistent prompt injection, memory poisoning, stale contradiction, secret capture, treatment leakage, telemetry disclosure, grader tampering, and benchmark-solution exposure. Controls are isolated stores/workspaces/caches; allowlisted scopes; canaries; short-lived credentials; encrypted restricted artifacts; default-deny telemetry; signed manifests; immutable audit; incident kill switch; and role-separated adjudication. Security blockers below override aggregate benefit. OTel assigns sensitive-data identification/protection to implementers; NIST AI RMF remains a risk-governance cross-check, not efficacy evidence. [OTel sensitive-data guidance](https://opentelemetry.io/docs/security/handling-sensitive-data/) [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)

### Data Architecture Patterns

#### Cross-repository authorization gate

Cross-repository recall remains an explicit later phase of the overall program, not a scope exclusion. Current repository-owner aggregation is insufficient for it. Before `R` scope can enroll, every source record must carry repository and revision, authorizing principal, permitted target repositories/agents/purposes, classification, validity/retention, provenance chain, and policy version. Reads enforce authorization per record at query and render time. Revocation blocks future reads, invalidates caches/derived summaries, propagates tombstones, and produces evidence. Migration re-evaluates every record under the target policy. Deletion covers graph nodes/edges, embeddings, indexes, caches, exports, viewer copies, backup expiry, and encryption keys. Until product authorization/deletion conformance passes, `F9` is planned but not run.

#### Field-level governance contract

The initial program uses only synthetic and public/open-source material. Every field/artifact records technical necessity and endpoint; source URL/revision; SPDX license and obligations; classification (`synthetic`, `public`, or `secret-prohibited`); allowed purpose; repository/agent scope; local path; retention/expiry; viewer/export destinations; derivations; deletion method; and verification evidence. Credentials, personal data, private user sessions, and proprietary source are prohibited. No managed processor, region transfer, private-data consent flow, or data-subject withdrawal workflow is needed in this baseline.

Ordinary governance still requires license compliance, fixture provenance, secret-canary handling, retention, and publication hygiene. Deletion tests cover primary artifacts, derived Parquet, DuckDB views, indexes/caches, generated reports, and content-address references. A deletion bundle contains inventory before/after, tombstones, index/cache rebuild, negative retrieval tests, and report purge proof. Hashes alone are not deletion proof.

Use two local zones: access-separated protected tests/solutions/canaries and publication-ready synthetic/public evidence. Lineage resolves report cell → DuckDB query → Parquet row → native artifact hash/source run. Publish code, schemas, manifests, permitted fixtures, and source-data hashes; withhold protected tests/solutions only when benchmark or contamination policy requires it. [AEA Data and Code Availability Policy](https://www.aeaweb.org/journals/data/data-code-policy)

### Deployment and Operations Architecture

#### Reproducibility claims and tolerances

| Claim | Required rerun | Tolerance | Claim implication |
|---|---|---|---|
| **Analysis reproducibility** | Same locked canonical data, environment, code, and seed | Exact cohort/counts and categorical outputs; numeric estimate ≤1e-10 absolute or ≤1e-8 relative; figure source-data hashes exact | Required for any reported result |
| **Grader reproducibility** | Same patch, image, dependency snapshot, grader | Binary/test set exact; timing excluded; continuous score ≤1e-6 absolute unless benchmark specifies tighter | Failure blocks outcome claim for affected tasks |
| **Deterministic evidence replay** | Raw events → canonical tables/report with network disabled | Artifact/table hashes exact after canonical timestamp/path normalization | Proves evidence pipeline replay, not agent behavior |
| **Stochastic trajectory rerun** | Same task/config/arm on new provider calls | No transcript/patch equality claim; preregistered distributional equivalence bounds for success, cost, and tool-use across sufficient reruns | Supports stability for tested configuration only |
| **Faithful replication (any registered conclusion)** | Separate team/site implementation and fresh tasks or held-out corpus, replaying the registered analysis | Reproduces the registered conclusion class (null, harm, indeterminate, or positive); the replication interval is consistent with the original against the ratified MPID; all safety blockers pass; discrepancies explained | Confirms reproducibility of the registered finding, whatever it was: a replicated null or harm is a successful replication, not a failure, and does not by itself license an efficacy claim |
| **Repeated-positive efficacy** | Independent positive replications across the registered population strata (separate team/site, fresh or held-out tasks) | The primary lower simultaneous bound clears the ratified MPID in the original **and** in the independent replication(s); guards and safety blockers pass in each; a single positive plus a null replication does not qualify | Required before any broad product or generalization efficacy claim: the bar is repeated positives, not one positive |

A second analyst must reproduce locked analysis before unblinding-derived narrative. A separate independent team performs replication; the original task authors cannot be the sole replicators. Package provenance, instructions, data access, hardware/software, and expected runtime. [AEA policy](https://www.aeaweb.org/journals/data/data-code-policy)

#### Categorical release blockers

No aggregate “P1 score” can average these away. Any confirmed item blocks the affected confirmatory family and release claim until a new preregistered study after remediation:

- **Security zero tolerance:** unauthorized secret/credential capture; cross-arm, cross-user, cross-agent, or cross-repository disclosure; successful protected-test tampering; unresolved high-severity injection/poison path.
- **Governance zero tolerance:** incompatible or missing corpus license/provenance; prohibited private/personal/secret collection; inability to delete governed artifacts; undisclosed downstream publication; or cross-repository processing before its authorization gate.
- **Evidence-integrity zero tolerance:** missing assignment or primary outcome; mutable/overwritten run; hash/signature mismatch; decoded treatment before lock; irreconcilable randomization; fabricated trace parentage; missing raw-to-report lineage.
- **Grading zero tolerance:** grader/gold nonreproducibility beyond contract; arm-dependent grader/environment; protected-test leak; unresolved image/dependency drift affecting treatment; scoring rule changed after unblinding.
- **Causal-validity zero tolerance:** shared writable history/cache across arms; run-level assignment when interference requires sequence/team assignment; post-randomization exclusion based on outcome/use; treatment-generated histories reused as control; arm-specific budget/prompt/tool privilege; material unblinding with no valid sensitivity bound.

Quantitative operational failures (latency, cost, nonprimary telemetry, ordinary availability) use separately ratified thresholds and may yield no-go/indeterminate. Zero-tolerance means one confirmed event is sufficient; sampling absence is not proof of safety.

#### Correct scope of the deterministic fixture

The existing deterministic note→storage→search fixture and the capture→spool→daemon→MCP release roundtrip are two **separate** evidence artifacts with separate IDs, scopes, and claims; neither proves the product trust contract.

- **`EV-FIXTURE-RETRIEVAL` (direct note→search deterministic retrieval fixture).** The `tests/eval` precision@k/hit@k harness runs the real `engine.search` path (the hybrid RRF recipe fusing the BM25 full-text and vector channels, `group_ids` namespace filtering, and the repository boost) under frozen deterministic doubles (an in-process mock LLM for extraction and an offline hashed bag-of-words embedder), enforced offline by the `retrieval-eval` CI job against a checked-in baseline. **Claim `CL-WIRING-RANK`:** deterministic retrieval and ranking wiring is regression-free under the frozen doubles and corpus. **Out of scope:** production embedding semantic quality, real-LLM extraction quality, continuous observation, downstream coding benefit, cost, and safety.
- **`EV-ROUNDTRIP-MCP` (capture→spool→daemon→MCP release roundtrip).** The `test_release_gate_roundtrip` test drives one fixture session end to end (raw session events → `run_observe` → the durable `Spool` → the daemon's independent ingester → a real `MemoryEngine` on the embedded graph → recall **through** the daemon socket and the `memory_recall` MCP tool and its renderer), asserting the ingested fact appears in the rendered memory map, the stub-backend sentinels are absent, health reports the real embedded backend, and recall is namespace-scoped. **Claim `CL-PIPELINE-SEAM`:** one fixture session survives the capture→spool→ingest→namespace-derivation→daemon-transport→MCP-renderer seam against the real embedded graph. **Out of scope:** it is one fixture session under deterministic extraction/embedding doubles, so it does not prove production extraction/embedding quality, retrieval quality at scale, continuous observation, downstream benefit, cost, safety, or the whole trust contract.

**`BL-RELEASE-GATE` (open implementation blocker).** The current `docs/release-gate.md` wording claims the single roundtrip proves the whole trust contract memrelay sells, and the gate is documented as a step a human runs before cutting a release. The release pipeline (`release.yml`) runs only the build and clean-install verification and neither invokes nor depends on the roundtrip; the general `ci.yml` pytest job runs it on pull requests and main, but the publish-gating release pipeline does not. That over-claiming wording plus the absence of mechanical release-CI enforcement is an implementation blocker: before either artifact may be cited as a release-blocking trust gate, the release-gate wording must be corrected to the bounded `CL-PIPELINE-SEAM` and `CL-WIRING-RANK` claims (not the whole trust contract), and the roundtrip must be enforced in release CI so a red gate mechanically blocks publish. Until both are done the release gate remains documentation, not an enforced gate, and every production and continuous-observation claim requires its own sentinels and evidence.

#### Scenario catalog contract

The machine-readable scenario catalog and generated traceability matrix are **mandatory implementation artifacts**, produced during implementation before TEA or harness work; this research workflow defines but does not create them. The normative catalog format is UTF-8 JSON validated by JSON Schema draft 2020-12. It declares semantic `schema_version`, monotonic `catalog_version`, stable per-scenario `id`, and `content_sha256`, where the digest is over RFC 8785 JSON Canonicalization Scheme bytes with the digest field omitted. The protocol references both versions and the digest. Every scenario has: atomic preconditions; immutable fixtures and SHA-256 hashes; one procedure; expected evidence by event class; objective pass/fail criteria; priority (`P0 blocker`, `P1 confirmatory`, `P2 exploratory`); risks addressed; owner; allowed retries; and decision-gate dependencies. Composite scenarios are split until one failed condition has one diagnosis. The generated UTF-8 JSON traceability artifact declares its own schema/version/hash and links every scenario `id` to the endpoint IDs (`EP-*`), safety gates (`SG-*`), evidence IDs, claim IDs, and decision gates it supports; generation is deterministic from the catalog and locked protocol and the result is never hand-edited.

Minimum catalog:

1. clean `N0/E0/YL/MI/TR/OR/AI/WO` provisioning and budget parity;
2. namespace, cross-agent, and unauthorized-repository denial;
3. valid, stale, superseded, contradictory, poisoned, and irrelevant memory;
4. empty and latency-yoked response fidelity;
5. dropped/duplicated/out-of-order OTLP events and reconciliation;
6. provider timeout, zero-cost failure, partial usage, and late invoice;
7. flaky test, dependency outage, network denial, image drift, grader crash, test tamper, out-of-scope patch;
8. blinded-view arm leak and rater calibration;
9. license/provenance rejection, secret-canary handling, retention, deletion, index rebuild, viewer/report purge, and later cross-repository revocation/migration;
10. JSONL/Parquet round-trip, content-address rebuild/delete, viewer export/restore;
11. replay, analysis, grader, stochastic rerun, and independent replication;
12. independently generated, identically replayed, and treatment-generated histories.

Validation invariants (checked in release CI before any gate): both artifacts validate against their pinned schema versions; IDs are unique, immutable across catalog versions, and referentially closed; every fixture SHA-256 resolves; canonical regeneration is byte-identical and both hashes match the protocol; every `P0`/`P1` scenario maps to at least one gate and at least one endpoint, safety, evidence, or claim ID; every referenced ID exists; every gate's dependent scenarios exist; no scenario is composite; and no hand-authored traceability row can survive regeneration. A gate cannot pass unless all dependent P0/P1 scenarios produce expected evidence against the referenced immutable catalog version and hash. Breaking schema changes require a major version and migration validator; additive compatible changes require a minor version; content-only corrections require a patch version. These artifacts are built and enforced in implementation, not produced by this research workflow.

**Architectural quality assessment:** high confidence in explicit units, history regimes, design-by-estimand assignment, endpoint distributions, execution-plane separation, and categorical gates; medium in benchmark transfer and feasible blinding; unknown in effect size and Copilot/framework telemetry completeness.

## Implementation Approaches and Technology Adoption

### Technology Adoption Strategies

#### Committed-stack integration and version-validation spike

Inspect AI, OTel/OTLP, OpenInference plus versioned memrelay semantics, Parquet/DuckDB, and local content-addressed artifacts are adopted. The 8-scenario spike validates integration and pinned-version assumptions; it does not decide whether to adopt them. Failures trigger bounded repair and then a documented temporary fallback so evidence collection can continue without an indefinite bake-off.

| Committed component | Required integration evidence | Repair trigger and temporary fallback |
|---|---|---|
| **Inspect AI** | The custom Copilot SDK solver completes `YL/TR`; proves Inspect made no model call; frozen SDK inputs/budgets and parity hashes match; lifecycle/errors/patches export; benchmark grader agrees 100%; no hidden retries; overhead distribution recorded | Repair the custom solver for up to five engineer-days; temporary direct Python Copilot-SDK state-machine runner, still imported into Inspect logs before analysis |
| **OTel SDK/local Collector/OTLP** | 100% expected P0 events reconcile normally; injected drop/duplicate/partial-success/shutdown is detected; raw native events preserved; sensitive-field allowlist passes | Repair instrumentation/Collector for up to three engineer-days; temporary append-only JSONL event transport with the same schema and reconciliation |
| **OpenInference + `memrelay.eval.*`** | Required agent/model/tool/retriever/evaluator fields map losslessly; unknowns survive; golden reverse mapping passes; schema/version recorded | Repair mapping/migration; temporary versioned memrelay-only fields, never lossy OpenInference coercion |
| **Parquet/DuckDB** | Two independent readers preserve rows, types, nulls, units, ordering keys, and canonical cell hashes; schema evolution/corruption tests pass | Repair writer/schema; temporary canonical JSONL converted to Parquet before locked analysis |
| **Content-addressed local artifacts** | Put/get hash exact; duplicate handling; manifest index rebuild; access rules; migration; revocation; selective delete and downstream-reference evidence pass | Repair manifest/index; temporary governed hierarchical paths plus signed SHA-256 manifest |

Each fallback receives an expiry condition and migration test. Generated local HTML is the baseline viewer; no managed or local third-party experiment tracker is required. Inspect's official docs support custom agents/solvers, limits, and API-readable logs; the study uses those orchestration interfaces but deliberately does not route model inference through an Inspect provider. These capabilities justify the selected architecture but do not establish its conformance or memrelay benefit. [Inspect custom agents](https://inspect.aisi.org.uk/agent-custom.html) [Inspect agents](https://inspect.aisi.org.uk/agents.html) [Inspect logs](https://inspect.aisi.org.uk/eval-logs.html) [OTel Collector](https://github.com/open-telemetry/opentelemetry-collector) [Parquet format](https://parquet.apache.org/docs/file-format/)

#### Public and synthetic corpus contract

Initial data is synthetic or public/open-source only. No private user session, proprietary repository, credential, personal record, or production history is eligible.

- **Primary synthetic corpus:** generate compact Python/TypeScript repositories under a permissive project-owned license, with executable tests and synthetic episode histories for all `F1–F8` families. Secret canaries are fake high-entropy strings with no credential value. Synthetic issue text and histories are generated before assignment and released with seeds, generators, and licenses.
- **Public coding transport and natural-task stratum:** sample a small, license-audited subset of SWE-bench Verified and newly collected SWE-bench-style tasks, using the native Docker graders. SWE-bench code/data is MIT, but each source repository and issue/patch retains its upstream license and provenance; redistribute only what that license permits. Older public benchmark tasks are generalization evidence, not contamination-resistant confirmation. [SWE-bench](https://github.com/SWE-bench/SWE-bench)
- **Synthetic mutation source:** SWE-smith is MIT and can create executable program-repair tasks from public repositories, but it is developed for Ubuntu/Docker and its generated tasks inherit source-repository constraints. Use only permissively licensed source snapshots and independently audit realism, shortcuts, and duplicate solutions. [SWE-smith](https://github.com/SWE-bench/SWE-smith)
- **Memory-mechanism secondary corpus:** LongMemEval's cleaned synthetic histories and LongMemEval-V2's public harness can test retrieval, updates, premise awareness, and latency. Their very long 115K-to-115M-token regimes are outside the primary runtime envelope, so use bounded small slices only and never treat QA accuracy as coding-patch evidence. Avoid ShareGPT/UltraChat-derived filler and enterprise-domain material unless item-level license, provenance, and personal-data checks pass. [LongMemEval](https://github.com/xiaowu0162/LongMemEval) [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2)
- **Deferred sources:** MemoryAgentBench is scientifically relevant for conflict resolution, but it aggregates multiple upstream datasets and its repository notes dependency/metric caveats. It is secondary-only after split-level license/provenance review. SWE-rebench's refreshed pipeline is a collection pattern, not automatic decontamination of locally selected tasks. [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench) [SWE-rebench](https://arxiv.org/abs/2505.20411)

Every corpus item records origin URL, revision/hash, SPDX license and obligations, redistribution status, creation/publication date, contamination risk, permitted split, and canary scan. Publication strips API keys, provider request IDs, local paths/usernames, and any accidental personal content. Protected solutions/tests remain access-separated until release policy permits publication.

#### Staged run, dual-provider budget, tool, and wall-clock envelope

| Stage | Matrix | Assigned units / agent runs | Copilot subscription envelope | Framework-internal OpenAI envelope | Wall-clock cap |
|---|---|---:|---|---|---:|
| Catalog/conformance | One catalog snapshot; arm-blind `N0` smoke for each returned eligible candidate | Not an efficacy sample; bounded by catalog size | Hard 1M task-agent tokens; model-specific AI-credit circuit breaker frozen from the observed catalog/pricing record | Hard 0.1M input + 0.05M output tokens and corresponding frozen-API-price USD cap, only if framework validation needs writes | 15 min/run; 4 local elapsed hours |
| Integration | 8 synthetic scenarios × `R-COP-M0` × `YL/TR` × 2 repeats | 32 runs | Expected 1–2M; hard 3.2M task-agent tokens; separate stage AI-credit cap computed and frozen before launch | Hard 0.6M input + 0.2M output tokens and corresponding USD cap; actual metered spend reconciled | 15 min/run; 8 local elapsed hours |
| Minimal blinded pilot | 16 tasks (2 per `F1–F8`) × `R-COP-M0` × 2 arms × 4 repeats | 128 assigned units; dynamic/team units contain 2–3 bounded episodes | Expected 12–24M; hard 32M task-agent tokens; separate frozen AI-credit cap | Hard 6M input + 2M output tokens and corresponding USD cap, tightened from blinded integration usage if lower | Short 20 min, medium 45 min, sequence/team 75 min; 5 local elapsed days at concurrency 2 |
| Expanded primary | 32 tasks (4 per `F1–F8`) × `R-COP-M0` × 2 arms × 8 repeats | 512 assigned units | Expected 48–96M; hard 128M task-agent tokens; separate frozen AI-credit cap | Hard 24M input + 8M output tokens and corresponding USD cap, tightened from blinded pilot usage if lower | Same per-unit caps; 10 local elapsed days at concurrency 4 |
| Secondary generalization | 16-task balanced subset × each available `R-COP-M1/M2` × 2 arms × 3 repeats | Up to 192 units; 96 if only one secondary qualifies | Expected 14–28M; hard 48M task-agent tokens across both cells; per-model AI-credit subcaps | Hard 9M input + 3M output tokens and corresponding USD cap | Same caps; 5 additional elapsed days |
| Later cross-repository | 4 authorized relation tasks × `R-COP-M0` × 2 arms × 3 independent clusters | 24 clusters, only after authorization/deletion gate | Separately approved hard task-agent cap ≤6M and AI-credit cap | Separately approved hard 2.25M input + 0.75M output and corresponding USD cap | Separately approved cap ≤5 days |

An assigned unit is the inferential unit; a 3-episode sequence creates three agent sessions but one sequence outcome. The pilot therefore contains 128 units and approximately 160–192 native agent sessions, not 128 falsely independent sessions.

Per-run policy:

- **Short:** cumulative model input ≤80K tokens, output plus reasoning ≤20K, ≤60 tool calls, 20 active minutes.
- **Medium:** input ≤200K, output plus reasoning ≤50K, ≤120 tool calls, 45 active minutes.
- **Sequence/team:** maximum three episodes; per episode input ≤120K, output plus reasoning ≤30K, ≤80 tool calls and 30 minutes; aggregate cap 75 active minutes.
- Individual shell commands cap at 120 seconds; benchmark test commands cap at 600 seconds. Agent processes may reach only the GitHub Copilot service through the SDK; framework-internal processes may reach only the pinned OpenAI endpoint; predeclared fixture acquisition is separate, and no web search occurs during tasks. One automatic pre-exposure infrastructure retry is allowed; none after treatment exposure. A timeout or Copilot-token/AI-credit/tool/framework-OpenAI/wall cap is an ITT failure with actual consumption and capped wall time.

For each stage, the Copilot AI-credit circuit breaker is computed before launch from the observed model's returned billing metadata or the dated GitHub pricing table and the stage's frozen token vector; the manifest stores both formula and result. It is a soft operational cap because usage is known after a response. Framework OpenAI has an independent hard input/output-token vector and USD cap computed from the pinned internal model's dated API rates; collection stops at the first cap reached. Quota resets, throttles, unavailable models, and subscription-allocation contention are recorded as provider-time strata and reanalyzed under alternative allocation/order schedules.

#### Price-independent cost sensitivity

Store usage quantities, not only currency: uncached input, cached input, cache writes, output/reasoning tokens when exposed, tool/container calls, Copilot AI credits, local CPU/RAM/disk seconds, and memrelay storage/telemetry operations. Every run references a dated, hashed provider price table and plan/region/discount metadata. Recompute all cost results under the observed table and deterministic `0.5×`, `1×`, `2×`, plus model-specific relative-price scenarios; report break-even price surfaces and rank reversals. Never assume the 2026-08-05 prices persist.

For Copilot, report resource quantities and AI credits separately from incremental cash under the actual subscription allowance; included allowance is not “zero economic cost,” but a shadow/subscription-normalized amount is not an invoice. For framework-internal OpenAI calls, report actual metered token/tool cost under the versioned API table and separately model region, service tier, and caching. Never add OpenAI framework tokens to Copilot task-agent tokens and call the sum “model cost.” GitHub currently defines 1 AI credit as USD 0.01 and publishes per-token tables, but plan allowance and overage treatment still determine cash impact. [Copilot models and pricing](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing) [Individual usage-based billing](https://docs.github.com/en/copilot/concepts/billing/usage-based-billing-for-individuals) [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)

### Development Workflows and Tooling

Keep four access-separated streams: protocol/schema/scenario catalog; task corpus and protected holdout; runner/adapters; analysis/report. A trial release freezes protocol, scenario-catalog hash, assignment code/seed escrow, task list, images, dependencies, adapters, prompts, policies, memory config, price table, analysis code, and blinded-view transform. CI runs schema checks, deterministic fixture, golden graders, randomization balance simulations, metamorphic trace tests, privacy allowlists, and a no-network smoke scenario. Paid stochastic trials run only from immutable protocol releases.

Changes after pilot are permitted only while blinded and must create a new protocol version with rationale. Any outcome-informed change makes subsequent work exploratory or requires a fresh holdout. Operators cannot see decoded aggregate results; adjudicators receive transformed views; analysts receive arm codes only after reconciliation/data lock.

### Testing and Quality Assurance

Test the evaluator before memrelay: property tests for IDs/state/price arithmetic; adapter contracts; arm parity; randomization/permutation; grader gold/fail/flaky/tamper fixtures; trace drop/duplicate/corruption; late billing; namespace/poison/canary attacks; license/provenance rejection; deletion/index rebuild; and deterministic replay. LongMemEval-V2 demonstrates explicit no-retrieval and memory backends plus accuracy/latency evaluation, but its web-agent question-answering construct is mechanism evidence, not repository patch success. [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2)

### Deployment and Operations Practices

Workers are ephemeral and isolated; mutable treatment state never crosses assignment units. Concurrency/rate limits, machine types, caches, network, queue policy, and credentials are arm-balanced. Preserve partial runs, heartbeat, provider request IDs, cleanup state, and artifact inventory. Reconcile before analysis eligibility. Scheduled sentinels cover production extraction/embedder/retrieval, because the deterministic fixture lacks continuous observation. Model alias, adapter, schema, memory engine, task image, price table, or grader change triggers the dependent scenario subset and may invalidate comparability.

Incident response can halt collection, revoke credentials, freeze namespaces, preserve evidence, notify governance owners, and perform blinded impact analysis. A rollback threshold cannot override a categorical blocker; a single security/governance blocker stops the affected scope.

### Team Organization, Effort, and Dependencies

Replace the one-engineer duration with effort ranges. **Effort vocabulary:** each range is **person-weeks of one qualified contributor**, where one person-week = 40 person-hours (5 person-days), and person-weeks measure effort, not elapsed calendar time. **Included** in a workstream's effort: its design, implementation, review, test authoring, documentation, and rework. **Excluded** and tracked separately: external approval and provider/legal lead time, compute queue/wait time, meetings outside the workstream, and cross-workstream integration (counted once under platform/adapters). **Staffing and concurrency assumption:** each workstream is staffed by one or two contributors and workstreams run concurrently where dependencies allow, with no single person on two workstreams at once. These are planning inputs, not commitments:

| Workstream | Effort (person-weeks) | Key output | Depends on |
|---|---:|---|---|
| Product/evaluation leadership | 3–7 | claims, estimands, parameters, decision gates | owner economics/risk appetite |
| Platform/Copilot SDK adapter | 6–14 | isolated runner, parity, product lifecycle | SDK/runtime, Copilot identity, task contract |
| Data/observability | 5–10 | schema, reconciliation, replay, lineage | event inventory, security classification |
| Finance/FinOps | 1–4 | price/invoice/allocation/amortization contract | provider contracts, volume scenarios |
| Data/license governance | 1–3 | public-corpus licenses, provenance, retention, publication hygiene | selected fixtures |
| Security/red team | 4–9 | threat model, blockers, attack/deletion scenarios | architecture and governance contract |
| Compute/operations | 2–6 | images, capacity, mirrors, incident/backup operations | adapter and benchmark requirements |
| Task curation/contamination | 8–24 | necessary tasks, natural-task stratum, holdout | repository access, curator independence |
| Blinded adjudication | 6–18 | labels, calibration, agreement/error report | frozen rubric/views and completed runs |
| Statistical design/analysis | 4–10 | effect distributions, simulation, registration, locked analysis | pilot and prospectively frozen parameters |
| Independent replication | 4–12 | separate replay/analysis/fresh-task replication | frozen package and access approvals |

**Elapsed critical path (calendar weeks), distinct from summed effort:** roughly 3–6 weeks for protocol/integration, 4–10 additional weeks for curation/adapters/pilot, 4–12 for the expanded or powered trial, and 4–12 for independent replication. Workstreams overlap; these are three-point planning estimates, not commitments. Staffing, parallelism, Copilot subscription quota/rate limits, framework OpenAI account capacity, and independent-review availability determine the actual calendar schedule without changing the scientific matrix.

### Cost Optimization and Resource Management

Spend in uncertainty-reduction order: deterministic contracts → component spike → blinded pilot → simulation-sized confirmatory trial → replication → operations. Reuse immutable images and model-independent artifacts but never treatment-sensitive completions or mutable memory. Set provider concurrency and cost circuit breakers before assignment; a tripped run remains an outcome. Optimize the Pareto frontier of executable success, product marginal cost, wall/active time, safety, and fully loaded operations—not token cost alone. Study budget, fully loaded product economics, and product marginal unit cost remain separate ledgers.

### Risk Assessment and Mitigation

| Risk | Required control | Decision consequence |
|---|---|---|
| Task is not memory-necessary | rubric, shortcut audit, oracle pilot, natural-task confirmation | reject/quarantine; no efficacy interpretation |
| History carryover/post-treatment bias | regime-specific assignment, clean state, ITT | causal-validity blocker |
| Contamination | temporal/provider provenance, duplicate/canary checks, protected holdout | quarantine or exploratory-only |
| Grader/environment nondeterminism | frozen contract, baseline/gold repeats, drift evidence | task/family blocked |
| Blinding failure | deterministic transform, guess study, executable-only sensitivity | narrow or invalidate subjective claims |
| Differential telemetry | conditional matrix, zero-missing primary fields, bounds | indeterminate or evidence blocker |
| Cost misstatement | three estimands, invoice/price/allocation versions | withdraw economic claim |
| Cross-repository disclosure | per-record authorization and deletion | scope blocked entirely |
| Framework lock-in | independent canonical fallback and export/restore | abandon component |
| Underpowered multiplicity | simulation on worst ratified grid, Holm/BH plan | do not begin confirmation |
| Resource/staffing optimism | workstream ranges and dependency gates | revise schedule/budget |

### Framework Decision Summary

- **Retain:** current pytest fixture as deterministic wiring/ranking evidence only; benchmark-native executable graders.
- **Committed:** Inspect AI orchestration; local OTel Collector/OTLP transport; OpenInference plus versioned memrelay semantics; Parquet/DuckDB canonical evidence; local content-addressed artifacts.
- **Validate and repair:** pinned-version Copilot SDK solver integration, experiment-start catalog capture, arm parity, framework-key isolation, round-trip fidelity, reconciliation, deletion, and overhead. Time-boxed fallbacks preserve progress without reopening adoption.
- **Build narrowly:** one Copilot SDK solver/adapter, canonical schema, reconciliation, scenario catalog, curation tools, and locked analysis.
- **Avoid as foundations:** proprietary-only trace schemas, LLM-only grading, viewer-owned evidence, owner-level cross-repository authorization, and a composite release score.

## Technical Research Recommendations

### Implementation Roadmap

0. **Resolved research lock:** dual reliability/efficiency objective; `F1–F8`; paired or independently blocked assignment by estimand; one primary local Copilot SDK task-agent cell selected from the observed subscription catalog; synthetic/public-only data; local-only evidence infrastructure.
1. **Protocol/scenario lock:** ontology, history regimes, arms, endpoint distributions, grading, blinding, telemetry, cost quantities, public-data registry, blockers, and scenario catalog.
2. **Committed-stack integration:** implement and validate Inspect adapters, local OTLP, OpenInference/memrelay mappings, Parquet/DuckDB, content addressing, replay, and time-boxed fallbacks.
3. **L0/L1 validation:** arm parity, fault injection, namespace/canary/tamper/deletion scenarios, production retrieval characterization, and continuous sentinel design.
4. **Minimal blinded pilot:** 16 tasks, 128 assignment units, sole primary `R-COP-M0` cell; estimate nuisance parameters while concealed, then publish exploratory effect distributions and Pareto frontiers.
5. **Threshold-candidate workshop:** derive a finite candidate set from pilot distributions, realistic operating volume/cost, and harm bounds; write the derivation and fresh-holdout rule before any confirmatory claim.
6. **Expanded matrix:** 32 tasks, 512 primary units plus at most 192 bounded `M1/M2` Copilot-SDK generalization units; report heterogeneity and negative controls.
7. **Optional confirmation:** only after thresholds, alpha/power, endpoint graph, and holdout are frozen; use simulation-sized N and complete ITT.
8. **Replication and scope expansion:** replicate natural tasks; separately estimate replay, dynamic, and cross-agent effects.
9. **Later cross-repository phase:** activate `F9` only after product authorization, revocation, and deletion controls pass.

### Technology Stack Recommendations

Baseline: Python 3.11, Inspect AI, pytest, benchmark-native containers/graders, local OTel SDK/Collector/OTLP, pinned OpenInference plus `memrelay.eval.*`, Parquet/DuckDB, SHA-256 content-addressed local artifacts, and generated HTML. Pin all versions, preserve raw native events, and validate each temporary fallback.

### Skill Development Requirements

Causal estimands and clustered/randomized analysis; simulation and multiplicity; benchmark/task and flaky-grader engineering; GitHub Copilot SDK automation and catalog/capability capture; MCP and sandbox security; OpenAI framework-client isolation; OTel/OTLP/OpenInference; public-data licensing, content-addressed deletion, and publication hygiene; FinOps; contamination forensics; blinded adjudication; reproducible analysis; and independent replication.

### Success Metrics and KPIs

Evidence quality precedes uplift:

- 100% assignments have zero-missingness primary fields and lineage;
- every P0/P1 scenario passes for a gate; categorical blockers remain zero;
- exact deterministic replay and grader reproduction within stated tolerances;
- all preregistered analyses, failures, nulls, and harms reported;
- arm parity, blinding-success/error, adjudicator agreement, contamination, and telemetry completeness reports published;
- 100% of agent spans identify the Copilot SDK/subscription cost source, 100% of framework API spans identify metered OpenAI provenance, zero spans claim both, and credential scanners find no key value outside the framework process;
- any future confirmatory N is simulation-derived from prospectively frozen inputs;
- product marginal, fully loaded operational, and study cost reported separately;
- independent analyst replay and independent natural-task replication completed before broad claims;
- every release sentence maps to a passed estimand, population, history regime, scope, endpoint, and gate.

Product benefit remains unknown until the trial. “Insufficient evidence” and “harm” are valid outcomes.

### Decisions already resolved by this research run

The execution plane, catalog-based model-selection rule, symbolic `M0/M1/M2` matrix, task/history families, assignment designs, dual-provider staged budgets, synthetic/public-only corpus, local infrastructure, committed tooling, and estimation-first dual objective are fixed above. Exact model IDs are runtime observations from the experiment-start catalog, not owner-selected names.

**Implementation quality assessment:** high confidence in the selected architecture and bounded matrix; medium confidence in adapter parity, public-task yield, and operating-cost translation; unknown effect sizes remain the intended output of the pilot.

# Evidence Before Advocacy: Comprehensive Technical Research on Evaluating Memrelay Collective Recall

## Research Synthesis

### Executive Summary

Memrelay's deterministic fixtures establish bounded wiring evidence, not downstream benefit. The selected program measures both reliability and total-cost/wall-time efficiency across intra-session, cross-session, cross-agent, stale/contradictory, and negative-control scenarios. Cross-repository recall remains in the program as a later phase gated by product authorization and deletion controls. No current evidence establishes causal benefit.

Every coding/task agent is a local Python 3.11 process driven by the pinned GitHub Copilot SDK under the owner's current Copilot subscription. At experiment start, the SDK's returned model catalog and capabilities are archived; deterministic arm-blind rules select and pin `M0` plus at most two secondary generalization models. Sixteen pilot tasks (128 assigned units) and 32 expanded tasks (512 primary units) span `F1–F8`. Immutable replay and self-contained tasks use paired/repeated task blocks; treatment-generated cross-session and cross-agent histories use independently blocked whole sequences/teams. Executable graders, complete ITT accounting, and negative controls keep the causal interpretation bounded.

The stack is committed: Inspect AI as a local wrapper around a custom Copilot SDK solver, local OTel Collector/OTLP, OpenInference plus versioned memrelay semantics, Parquet/DuckDB, and local content-addressed artifacts. The task-agent Copilot ledger is separate from actual framework-internal OpenAI API usage. The OpenAI key exists only in the local memrelay/framework process, and the experiment forces memrelay's `byo-key` strategy with a fail-closed preflight so its default `borrow-host` path cannot consume Copilot quota or confound treatment. Integration tests validate and repair pinned versions; bounded fallbacks prevent a tool failure from reopening adoption. All initial data is synthetic or public/open-source.

**Key technical findings**

- Assignment, experimental, observation, and analysis units differ; independent N is runs only for isolated single-run trials and histories/sequences or relation clusters for interference designs.
- Independently generated, identically replayed, and treatment-generated histories answer different causal questions and cannot be pooled.
- Reliability and efficiency are co-equal objective domains; success, quality, harm, and safety guard against false cost/time savings.
- Inspect schedules and scores but does not provide the model; every task-agent call follows local orchestrator → local Copilot SDK/runtime → GitHub Copilot subscription service.
- Memrelay/Graphiti framework calls use a separate local-process → OpenAI API path, credential boundary, telemetry service, and actual-cost ledger.
- Agent model IDs are observed rather than assumed: archive `list_models()` output, select by frozen arm-blind rules, and never substitute a model inside a block.
- The pilot estimates effects and nuisance parameters. Candidate practical thresholds come from pilot evidence plus operating economics, then are frozen before any fresh-holdout promotional confirmation.
- Benchmark eligibility requires blinded memory necessity, shortcut and contamination audits, independent natural-task confirmation, frozen grading, measured blinding, conditional telemetry completeness, and three distinct cost estimands.
- Reproducible analysis, grader reproduction, deterministic evidence replay, stochastic trajectory rerun, and independent replication are separate claims with separate tolerances.

**Top recommendations**

1. Implement the scenario catalog and committed-stack integration contracts.
2. Run the 32-run integration stage, then the 128-unit blinded pilot.
3. Publish dual-objective distributions, negative controls, heterogeneity, harm tails, Pareto frontiers, and price sensitivity.
4. Run the 512-unit expanded matrix only after pilot task/adapter quality checks pass.
5. Freeze thresholds and a fresh holdout before any confirmatory claim; require categorical gates and replication before broad release language.

## Table of Contents

1. Technical Research Introduction and Methodology  
2. Technical Landscape and Architecture Analysis  
3. Implementation Approaches and Best Practices  
4. Technology Stack Evolution and Current Trends  
5. Integration and Interoperability Patterns  
6. Performance and Scalability Analysis  
7. Security and Compliance Considerations  
8. Strategic Technical Recommendations  
9. Implementation Roadmap and Risk Assessment  
10. Future Technical Outlook and Innovation Opportunities  
11. Technical Research Methodology and Source Verification  
12. Technical Appendices and Reference Materials

## 1. Technical Research Introduction and Methodology

### Technical Research Significance

Persistent collective recall changes both information and trust boundaries. Plausible retrieval can make an agent slower or confidently wrong; null average results can hide benefits on genuinely memory-dependent work; and low cost can simply mean early failure. METR's 2026 productivity update illustrates the danger of extrapolation: it described task/developer selection and concurrent-agent time measurement as making later estimates unreliable. The target question must therefore name task, run/history, repository, agent/model, scope, date, endpoint, and estimand. [METR 2026 update](https://metr.org/blog/2026-02-24-uplift-update/)

_Technical importance:_ durable memory changes inputs, state, interference, authorization, and deletion obligations.  
_Business impact:_ credible null/harm evidence prevents misinvestment; bounded positive evidence supports only the tested claim.

### Technical Research Methodology

- **Scope:** intra-session, cross-session, cross-agent, and gated cross-repository recall; retrieval, downstream outcomes, cost, efficiency, safety, and operations.
- **Sources:** official specifications and project documentation, primary papers/benchmark repositories, and local artifact context; noisy search summaries were not evidence.
- **Analysis:** claim ladder, explicit unit ontology, history-specific estimands, paired or independent assignment by workload, executable truth, blinded review, effect distributions/Pareto analysis, simulation, and threat/governance analysis.
- **Period:** fresh web verification through 2026-08-05.
- **Non-goals:** no efficacy claim, harness implementation, product-policy choice, silently approved threshold, universal coding-agent claim, or authorization of cross-repository trials.

### Goals and Achieved Objectives

The revision preserves the prior scientific, statistical, grading, ITT, safety, reproducibility, and release-gate corrections while resolving the owner questions: objective, scopes, design assignment, corrected local Copilot-SDK runtime, separated framework API path, committed local data/tooling, estimation-first thresholds, dual-provider staged budgets, catalog-based model matrix, task families, and corpus governance.

## 2. Technical Landscape and Architecture Analysis

### Current Architecture

A modular control plane freezes protocol and concealed assignments; isolated workers execute product-native runs; benchmark graders produce outcomes; raw and canonical evidence reconcile; locked analysis generates reports. The detailed ontology and history contracts above govern every trial. Ports-and-adapters permit product/provider changes without changing causal definitions. Dominant trade-off: realism adds selection, drift, cost, and governance burden; determinism reduces external validity.

### System Design Principles

Claim before metric; randomize access, not observed use; assign at the highest interference level; preserve every failure; separate replay and dynamic histories; blind and measure blinding; keep executable outcomes primary; version everything; and never average a categorical blocker into a composite score. Preregistration distinguishes confirmation from exploration and requires registered nulls to remain visible. [COS preregistration](https://www.cos.io/initiatives/prereg)

## 3. Implementation Approaches and Best Practices

### Current Methodology

Protocol, scenario catalog, and public-corpus governance precede paid runs. The committed stack is validated with golden scenarios and repaired behind canonical interfaces. Task authors, necessity raters, adjudicators, decoded analysts, and independent replicators are separated where conflicts affect claims.

### Framework and Tooling

Inspect is the committed local orchestrator, OTel/OTLP the local transport, OpenInference plus `memrelay.eval.*` the semantic layer, Parquet/DuckDB the canonical evidence layer, and SHA-256 local content addressing the artifact layer. A custom solver invokes and observes the native local Copilot SDK loop without routing inference through an Inspect model provider; generated HTML is the replaceable viewer. [Inspect custom agents](https://inspect.aisi.org.uk/agent-custom.html) [Inspect agents](https://inspect.aisi.org.uk/agents.html) [Inspect logs](https://inspect.aisi.org.uk/eval-logs.html)

## 4. Technology Stack Evolution and Current Trends

### Current Stack

Python 3.11, Inspect, pytest, local OTel Collector/OTLP, OpenInference plus memrelay semantics, Parquet/DuckDB, SHA-256 manifests, benchmark-native graders, and generated reports form the first evidence loop. MCP is the product integration surface. JSONL is a repair fallback, not the planned canonical store. [MCP](https://modelcontextprotocol.io/specification/2025-11-25) [Inspect](https://inspect.aisi.org.uk/) [DuckDB Parquet](https://duckdb.org/docs/stable/data/parquet/overview)

### Adoption Pattern

Compose behind versioned canonical contracts; preserve native events; validate round-trips and deletion; and retain time-boxed repair fallbacks. No orchestration, telemetry, storage, or UI component supplies efficacy, grading truth, or governance by itself.

## 5. Integration and Interoperability Patterns

### Current Integration Approaches

MCP connects product hosts to memrelay through UTF-8 JSON-RPC over `stdio` or Streamable HTTP. Anti-corruption adapters normalize launch/configuration without hiding raw events. W3C Trace Context propagates trace identity when supported; opaque correlation and span links handle closed subprocesses. OTLP transports telemetry, while registry reconciliation determines completeness. [MCP transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) [W3C Trace Context](https://www.w3.org/TR/trace-context/) [OTLP](https://opentelemetry.io/docs/specs/otlp/)

### Standards and Protocols

- MCP: product-facing treatment path and user-control/security principles.
- W3C Trace Context: interoperable request identity.
- W3C Baggage: no secrets, task content, or treatment labels across trust boundaries.
- OTLP 1.11.0: stable traces/metrics/logs, but no multi-hop end-to-end guarantee.
- OpenInference: AI span vocabulary; pinned subset plus memrelay experiment semantics.

## 6. Performance and Scalability Analysis

### Performance and Endpoints

Performance is a dual-objective distribution over executable success/quality and total cost/wall time, with failure, harm, safety, tokens, memory latency, resources, and storage retained. The pilot estimates marginal distributions, heterogeneous effects, harm tails, and the Pareto frontier without a binary practical threshold. Cost/time improvement is never inferred from failed runs disappearing; zero-cost failures stay in ITT.

### Statistical Scalability

Power comes from independent assignments, not spans. Simulation uses the exact estimator and correlation/attrition structure. Retrieval metrics aggregate at query but resample at run/history. Models/products remain strata or separate populations unless independently sampled levels justify generalization. Missingness bounds determine whether an effect is robust or indeterminate.

## 7. Security and Compliance Considerations

### Security Best Practices

Isolate arm and scope state; use short-lived credentials; treat memory/repository/tool text as untrusted; test injection, poisoning, stale contradiction, unauthorized reads, canaries, tampering, and deletion; minimize telemetry; encrypt restricted data; and maintain kill/rollback procedures. OTel explicitly places sensitive-data responsibility on implementers. [OTel sensitive-data guidance](https://opentelemetry.io/docs/security/handling-sensitive-data/)

### Governance and Compliance

Source/revision, license, classification, necessity, scope, retention, derivation, export, deletion, and evidence are mandatory for the synthetic/public baseline. Private sessions, proprietary source, credentials, and personal data are prohibited. Cross-repository scope is a later phase gated on per-record product authorization and deletion conformance. [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)

## 8. Strategic Technical Recommendations

### Decision Framework

Approve a claim only when: the target population and history regime are named; assignment and analysis match interference; all P0/P1 scenarios pass; primary evidence is complete; uncertainty clears a ratified practical threshold; safety/governance blockers are zero; cost uses the correct estimand; confirmation and exploration are separate; and independent replay/replication supports the intended scope. “Insufficient evidence” is not a release pass.

### Recommended Study Portfolio

1. deterministic pipeline-wiring fixture and evaluator conformance;
2. frozen production extraction/retrieval characterization and sentinel;
3. primary `TR(S)` versus `YL(S)` randomized executable coding trial;
4. separate `H_replay` and `H_dynamic` cross-session sequence trials;
5. exploratory cross-agent trial after parity/interference gates;
6. memory mechanism replication with LongMemEval/LongMemEval-V2/MemoryAgentBench;
7. ordinary coding transport checks with SWE-bench and curated memory-dependent natural tasks;
8. governed shadow/canary only after blockers pass.

LongMemEval provides 500 questions spanning extraction, multi-session reasoning, updates, temporal reasoning, and abstention with evidence labels. LongMemEval-V2 reports 451 curated questions over long multimodal agent histories and accuracy/latency. MemoryAgentBench covers retrieval, test-time learning, long-range understanding, and conflict resolution, with metric heterogeneity. These are useful mechanism tests, not repository-patch proof. [LongMemEval](https://github.com/xiaowu0162/LongMemEval) [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2) [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench)

## 9. Implementation Roadmap and Risk Assessment

### Phases and Decision Gates

Protocol/scenario lock → committed-stack integration/repair → conformance/security/governance validation → 128-unit blinded pilot → threshold-candidate derivation → 512-unit expanded matrix → optional fresh-holdout confirmation → natural-task replication → later authorized cross-repository phase. Each phase has an immutable exit bundle and stop condition; failure returns to repair or redesign, not threshold relaxation.

### Principal Risks

Construct mismatch, contamination, carryover, post-treatment adjustment, arm leakage, grader/image/dependency drift, missing telemetry, cost misallocation, cross-repository authorization failure, framework lock-in, underpowered multiplicity, selection in ecological work, and staffing optimism dominate. The contracts above map each risk to evidence and a decision consequence. Synthetic task generation such as SWE-smith can scale candidates but requires human necessity, realism, shortcut, and contamination review. [SWE-smith](https://github.com/SWE-bench/SWE-smith)

## 10. Future Technical Outlook and Innovation Opportunities

### Near- and Medium-Term Evolution

Expect stronger agent-memory benchmarks, refreshed coding tasks, OTel-compatible semantics, and provider-native traces, but also more training contamination, model drift, and opaque product behaviors. LongMemEval-V2's trajectory-history and latency framing is relevant to experienced-colleague memory, while SWE-rebench's refreshed collection addresses but does not solve local contamination. Keep adapters and schemas replaceable.

### Research Opportunities

Interference-aware multi-agent designs; causal mediation under explicit assumptions; value-of-information retrieval; temporal/provenance calibration; privacy-preserving relation graphs; adaptive memory budgets under registered policies; stochastic stability diagnostics; and standardized memory-dependent coding scenario formats. A five-year efficacy claim is unjustified; the durable asset is an auditable capability that re-estimates bounded effects.

## 11. Technical Research Methodology and Source Verification

### Primary Standards and Official Documentation

- [MCP specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25) and [transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/) and [W3C Baggage](https://www.w3.org/TR/baggage/)
- [OTLP 1.11.0](https://opentelemetry.io/docs/specs/otlp/), [OTel Collector](https://github.com/open-telemetry/opentelemetry-collector), and [sensitive-data guidance](https://opentelemetry.io/docs/security/handling-sensitive-data/)
- [OpenInference specification](https://arize-ai.github.io/openinference/spec/)
- [Apache Parquet format](https://parquet.apache.org/docs/file-format/) and [DuckDB Parquet](https://duckdb.org/docs/stable/data/parquet/overview)
- [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)
- [ICH E9(R1) estimands](https://database.ich.org/sites/default/files/E9-R1_Step4_Guideline_2019_1203.pdf), [FDA multiple-endpoint guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/multiple-endpoints-clinical-trials), and [COS preregistration](https://www.cos.io/initiatives/prereg)
- [AEA Data and Code Availability Policy](https://www.aeaweb.org/journals/data/data-code-policy)

### Framework and Benchmark Sources

- [Inspect AI](https://inspect.aisi.org.uk/), [custom agents](https://inspect.aisi.org.uk/agent-custom.html), [agents](https://inspect.aisi.org.uk/agents.html), [sandboxing](https://inspect.aisi.org.uk/sandboxing.html), and [logs](https://inspect.aisi.org.uk/eval-logs.html)
- [GitHub Copilot SDK](https://github.com/github/copilot-sdk), [Python SDK](https://github.com/github/copilot-sdk/blob/main/python/README.md), [OpenTelemetry](https://github.com/github/copilot-sdk/blob/main/docs/observability/opentelemetry.md), [supported models](https://docs.github.com/en/copilot/reference/ai-models/supported-models), and [models/pricing](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing)
- [Copilot SDK authentication](https://github.com/github/copilot-sdk/blob/main/docs/auth/authenticate.md), [SDK BYOK distinction](https://github.com/github/copilot-sdk/blob/main/docs/auth/byok.md), [Copilot usage-based billing](https://docs.github.com/en/copilot/concepts/billing/usage-based-billing-for-individuals), and [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) for framework-internal calls only
- [SWE-bench](https://github.com/SWE-bench/SWE-bench), [SWE-rebench](https://arxiv.org/abs/2505.20411), and [SWE-smith](https://github.com/SWE-bench/SWE-smith)
- [LongMemEval](https://github.com/xiaowu0162/LongMemEval), [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2), and [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench)
- [RE-Bench](https://github.com/METR/RE-Bench), [METR 2026 productivity update](https://metr.org/blog/2026-02-24-uplift-update/), and [HELM](https://github.com/stanford-crfm/helm)

### Search and Verification Record

Fresh query families executed through 2026-08-05: GitHub Copilot SDK runtime, signed-in-user authentication, model catalog/capabilities, telemetry, MCP/tools, limits, and subscription billing; Inspect custom agents/solvers, limits, logs, and Inspect SWE; OpenAI API pricing only for framework-internal calls; OTel/OTLP/OpenInference; Parquet/DuckDB; memrelay's local `borrow-host`, `byo-key`, strategy-selection, Copilot-provider, and framework code; SWE-bench/SWE-smith/SWE-rebench; LongMemEval/V2 and MemoryAgentBench; estimands, clustered/paired assignment, endpoints, multiplicity, preregistration, and reproducibility. Consequential claims were checked directly against official documentation, specifications, papers, primary repositories, and the current local source tree.

### Quality Assurance and Limitations

High confidence: protocol facts, corrected trust boundaries, unit/history distinctions, need for assignment matching interference, executable grading, evidence lineage, and categorical governance. Medium confidence: task transfer, feasible blinding, custom-solver conformance, tool fit, and effort ranges. Unknown until integration/pilot: returned subscription catalog, metering completeness, effect size, powered N, task yield, and economics. The research can support harm, null, positive, or indeterminate conclusions; it cannot itself establish efficacy.

## 12. Technical Appendices and Reference Materials

### Future Confirmatory Protocol Checklist

- frozen claim, population, estimand, DAG, unit ontology, history regime, and scope;
- arm specifications and parity/yoke artifacts;
- dual-objective claim wording, frozen practical bounds, sidedness, alpha, multiplicity, power, and sequential rule;
- simulation inputs/grid/code/N and pilot separation;
- task necessity, shortcut, contamination, natural-task, and author-separation evidence;
- grading, retry, infrastructure classification, and image/dependency contracts;
- blinded transform, leak study, calibration, agreement/error, and sensitivities;
- telemetry matrix and zero-missing primary fields;
- cost-estimand ledger and economic parameters;
- field governance, authorization, revocation/deletion, and cross-repository gate;
- scenario catalog, categorical blockers, reproducibility package, and replication plan.

### Pilot-fixed policies versus future claim thresholds

Pilot policies are fixed here: dual-objective estimation; the `F1–F8` matrix; paired/independent assignment by estimand; one pre-exposure infrastructure retry; five baseline/gold grader checks; source-count exact, token ±10%, and latency ±15% or 200 ms for matched controls; necessity median ≥3; semantic duplicate quarantine at cosine .92; and exact-binomial safety upper bounds. Success MPID, quality/harm margins, cost ratio, wall ceiling, confirmatory alpha/power, and multiplicity weights are deliberately **not** fixed now. The pilot produces candidate values under a prespecified derivation; any later claim freezes them before fresh-holdout enrollment.

### Genuinely Remaining Owner Decisions

No further choice of task-agent SDK, provider, or named model is open: the execution plane and catalog-based rule are fixed. Implementation can begin, but enrollment is blocked until two experiment-start facts are materialized: the current subscription's raw `list_models()` catalog/capabilities and the current plan/allowance/price record. The exact `M0/M1/M2` IDs are intentionally unknown until that observed snapshot. The framework-internal OpenAI model, API price record, key-bearing process, and dollar caps must also be pinned after the catalog/conformance compatibility check and before the 32-run integration stage; this is an operational protocol lock, not permission to use that key for task agents.

Later owner decisions are:

1. After the pilot publishes candidate practical bounds and operating-cost scenarios, choose the claim/release threshold set, confirmatory alpha/power/multiplicity policy, and fresh-holdout population. This choice cannot use a threshold selected merely because it is positive.
2. After the minimal pilot, authorize the expanded 512-unit and optional secondary stage (up to 192 units) against the separately measured Copilot subscription forecast, framework OpenAI API spend, wall time, and provider quota/rate-limit capacity.
3. Decide when to fund and enable the product authorization/revocation/deletion controls required for later `F9` cross-repository experiments. The overall research scope remains cross-repository-capable even while that phase is gated.

### Scenario Catalog Dependency Rule

No downstream TEA artifact is executable unless it references the frozen, machine-readable scenario catalog by schema version, catalog version, and canonical SHA-256, together with its atomic preconditions, fixture hashes, procedure, expected evidence, pass criteria, priority, risks, and gate dependencies, and unless the generated traceability matrix links it to the endpoint (`EP-*`), safety-gate (`SG-*`), evidence, and claim IDs it exercises. The catalog and traceability matrix are mandatory implementation artifacts and are not produced by this research document. TEA must not reinterpret a narrative recommendation as a test case.

---

## Technical Research Conclusion

The defensible path is not to expand a deterministic retrieval fixture and call it a trust or product evaluation. Keep that fixture for its narrow wiring/ranking purpose, add production continuous observation, and construct a separately governed experiment system whose units, histories, arms, scopes, endpoints, power, grading, evidence, cost, and blockers are explicit. Run every coding agent locally through the Copilot SDK and current subscription; use the separate OpenAI path only inside instrumented memrelay/framework operations.

Start with the experiment-start catalog/conformance lock, scenario catalog, and 32-run committed-stack integration stage, then execute the 128-unit blinded pilot across all eight families. Use its distributions and separate Copilot/framework cost surfaces to generate prospective threshold candidates; proceed to the 512-unit expanded matrix and optional fresh-holdout confirmation without changing units or guards. Longitudinal and cross-agent effects keep their sequence/team estimands. Cross-repository remains a planned later phase.

A promotional release claim is justified only after prospectively frozen bounds are cleared on fresh evidence and every categorical blocker passes. Null, harmful, or indeterminate findings are valuable evidence. Implementation and exploratory research do not wait for those future claim thresholds.

**Technical Research Completion Date:** 2026-08-05  
**Research Period:** fresh web and artifact research through 2026-08-05  
**Source Verification:** primary and official sources prioritized; current facts rechecked  
**Overall Confidence:** high in evaluation architecture; medium in tool/benchmark fit; unknown in memrelay efficacy and economics

_This artifact is an evaluation-design reference. It authorizes no efficacy claim and does not enable cross-repository processing before the `F9` gate; it does not block implementation, integration validation, or exploratory/pilot research._
