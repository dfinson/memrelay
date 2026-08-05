# Reviewer Gate — Rubric Walker Review

- **Target:** `ARCHITECTURE-SPINE.md` (memrelay Evaluation Platform, initiative altitude, draft)
- **Lens:** good-spine checklist (`.agents/skills/bmad-architecture/references/reviewer-gate.md`)
- **Mode:** Validate intent — report only, spine not edited
- **Date:** 2026-08-05

## Gate verdict

**PASS with minor fixes.** This is an unusually rigorous, internally coherent spine. It fixes the real
divergence points for the level below (23 enforceable `AD` rules), ratifies rather than contradicts the
brownfield source, and covers the TEA/research capability surface. No blocking issues. Three non-blocking
findings and a few suggestions are below — the highest-value one is an unnamed JSON canonicalization
algorithm that undercuts the spine's own byte-identical-hash guarantee.

---

## Checklist walk

| Checklist dimension | Assessment |
| --- | --- |
| Real divergence points fixed, none missed | **Good.** Strata separation, credential-by-process, concealment, evidence append-only, ITT/retry, catalog compilation, and telemetry semantics are exactly the cross-unit decisions the level below cannot each re-invent. |
| Every `AD` Rule enforceable & prevents its stated divergence | **Good.** Rules are mechanically checkable (import boundaries, schema validation, ledger transition order, redaction, reconciliation completeness). See F2 for one terminology clash. |
| Nothing under Deferred lets two units diverge | **Mostly good.** Each Deferred item carries a "pin once / freeze prospectively" condition, so a single shared choice is made, not per-unit. See F1 — the *convention* (not the Deferred table) has one open divergence vector. |
| Named tech verified-current | **Adequate for research date.** Volatile pins (Copilot SDK, OTel Collector, GenAI conventions, model IDs) are correctly pushed to implementation-time pins. Fixed seeds (Inspect 0.3.252 Beta, DuckDB 1.5.5, PyArrow 25.0.0, OpenInference 0.1.31, OpenAI-instr 0.1.53) are research-time and unverifiable offline — see S2. |
| Ratifies brownfield codebase | **Verified.** AD-20/AD-21 claims check out against source: `MemoryEngine.from_config/note/search/detail/health/close` (`engine/graphiti.py`), `SessionDiscoveryPoller`/`RunObserveCapture`/`LiveTailCapture` (`daemon/session_discovery.py`), daemon single-writer + Ladybug exclusive lock (SPEC §2, ADR 0001). Stack pins match `pyproject.toml` (`traceforge-toolkit >=0.1,<0.1.2`, `graphiti-core >=0.29,<0.30`, `ladybug >=0.18,<0.18.1`, `mcp >=1.0,<2`, product Python `>=3.11,<3.14`). |
| Covers driving spec's capabilities | **Good.** Reliability/cost/efficiency, controlled vs dynamic histories, blinding, grading, cost ledgers, reconciliation, governance, and CI/paid separation all map to `AD`s and the capability table. TEA epics 1–8 each land on an `AD`. |
| No new `AD` weakens an inherited parent | **N/A.** No parent spine inherited; this is the top-level initiative spine. |
| Every altitude-owned dimension decided/deferred/open — esp. operational/environmental envelope | **Weakest area.** Process topology, isolation, quotas, circuit breakers, retries, and CI/paid staging are covered. But the reference *execution environment* (host OS/hardware class) and *evidence durability* (backup/retention/RTO-RPO for the irreplaceable ledger + CAS) are silent. See F3. |

---

## Findings

### F1 — [Non-Blocking, High] JSON canonicalization algorithm is unnamed, undercutting the spine's own byte-identical-hash guarantee

- **Where:** Consistency Conventions rows *JSON* ("canonicalized before identity hashing") and *Hashes*
  ("SHA-256 hex over declared canonical bytes"); AD-10 ("byte-identical on regeneration"); AD-11 (content
  hashes as identity).
- **Issue:** The spine makes content hashes the backbone of identity, traceability, and reconciliation, and
  requires generated outputs to regenerate byte-identically — but never names the canonicalization scheme.
  "Canonicalized" and "declared canonical bytes" leave the choice (e.g. RFC 8785 / JCS vs. sorted-key
  `json.dumps` vs. another JSON canonical form) to each implementer. Two units that pick different schemes
  produce different `content_sha256` for the same logical object, silently breaking cross-artifact traceability
  and regeneration checks. This is precisely a "convention that could let two units diverge."
- **Consistency signal:** The TEA handoff already fixes this — it requires `content_sha256` via **RFC 8785**.
  The spine should ratify that, not leave it looser than its own source.
- **Recommended fix:** In Conventions, name the canonicalization standard explicitly (RFC 8785 / JCS) for all
  identity hashing, or add it as a Deferred item with a single-pin condition. Keep it consistent with the TEA
  handoff's RFC 8785 requirement.

### F2 — [Non-Blocking] "Credential-free framework" in AD-06 contradicts AD-09 giving the framework an OpenAI credential

- **Where:** AD-06 Rule ("Containers may run credential-free framework, engine, grader, and fault tests…")
  vs. AD-09 Rule ("The memrelay framework process receives only its configured OpenAI credential…").
- **Issue:** AD-06 labels the framework path "credential-free," but AD-09 explicitly provisions the framework
  process with an OpenAI credential. Taken literally the two rules conflict; a reader wiring the container
  isolation could either strip the OpenAI key the framework needs, or conclude AD-06 is wrong. The intent is
  clearly "free of the Copilot subscription credential," not "free of all secrets."
- **Recommended fix:** Reword AD-06 to "Copilot-credential-free" (or "subscription-auth-free") so the two
  rules are unambiguous and non-overlapping.

### F3 — [Non-Blocking] Operational/environmental envelope gaps: reference execution environment and evidence durability are unspecified

- **Where:** AD-05 (append-only ledger + CAS), AD-06 (host-native execution), Process Topology, Deferred
  ("Managed telemetry, warehouse, tracker, or evidence service").
- **Issue (two parts):**
  1. **Reference execution environment.** The primary Copilot worker is required to be host-native (AD-06,
     not containerized), and the platform spans Windows/macOS/Linux (`socket or port`, named-pipe vs. Unix
     socket). But no reference host OS / hardware class is fixed for trials. For an efficacy + cost/wall-time
     study, the execution environment is a confound and part of the claimed population/scope; leaving it
     silent risks two operators running the same protocol on materially different substrates.
  2. **Evidence durability.** The append-only SQLite WAL ledger + SHA-256 CAS + versioned Parquet are the
     irreplaceable output of a 40–105 person-week program, yet there is no backup / retention / recovery
     (RTO/RPO) policy. Append-only guards against *overwrite*, not against *loss* of the single local copy.
     The Deferred "managed evidence service" item gestures at recovery but defines no baseline durability
     posture.
- **Consistency signal:** TEA's Owner-Only Decision Stories list both "Population/history/scope … region"
  and "Operations … availability/RTO/RPO" as decisions that must not silently default.
- **Recommended fix:** Add these as explicit open questions or Deferred rows with revisit conditions: (a) a
  named reference execution environment for trials (or an explicit statement that OS/hardware is an owner
  scope parameter), and (b) a minimum evidence-durability posture (backup cadence + retention for ledger/CAS/
  Parquet) before the first paid stage.

---

## Suggestions (medium/low — optional)

- **S1 — Toolchain divergence unjustified.** AD-02 / Structural Seed prescribe `uv` + `uv.lock` for the
  evaluator, while the product uses hatchling + pip (`pyproject.toml`). Deliberate isolation is fine, but a
  one-line rationale would preempt "why two toolchains?" churn. (Suggestion)
- **S2 — Re-verify fixed Stack seeds at implementation.** The non-volatile pins (Inspect 0.3.252 Beta,
  DuckDB 1.5.5, PyArrow 25.0.0, OpenInference 0.1.31, OpenAI-instr 0.1.53) are research-time seeds; confirm
  currency at the conformance spike alongside the already-deferred volatile pins. (Suggestion)
- **S3 — Make cross-agent scope explicit.** AD-19 pins Copilot as the reference provider; the SPEC's core
  value is portability across ~18 agents. The spine implies cross-agent generalization is out of initial
  scope ("reference provider") but never states it. A one-line scope note (cross-agent = promotion, per TEA)
  would close the loop. (Suggestion)

---

## Notes on things that check out (so they aren't re-litigated)

- Deferred table is well-guarded: every item has a single-pin or freeze-prospectively condition (SDK/runtime,
  model IDs, Collector digest, framework model/price table, confirmatory thresholds), preventing per-unit
  divergence.
- Strata separation (AD-03), concealment (AD-11/AD-12), independent grading (AD-13), fail-closed
  reconciliation (AD-15), separated cost ledgers (AD-16), and restricted retry/ITT (AD-18) are enforceable
  and directly counter the causal-validity and evidence-integrity risks flagged as categorical in the TEA
  risk register.
- `binds` frontmatter, the Process Topology, and the Capability-to-Architecture map are mutually consistent —
  every bound domain has a home module and governing `AD`.
