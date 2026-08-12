"""CLI command implementations."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from argparse import Namespace
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from memrelay_eval.analysis.claims import ClaimScope
from memrelay_eval.analysis.gates import ClaimGateDecision
from memrelay_eval.analysis.queries import (
    ANALYSIS_SCHEMA_VERSION,
    AnalysisQuery,
    DerivationPublisher,
    DerivationSpec,
    ReadOnlyDuckDbAnalysis,
)
from memrelay_eval.analysis.replay import (
    ReproductionBundle,
    allocate_stochastic_rerun,
    execute_sealed_replay,
    publish_comparison,
    seal_reproduction_bundle,
)
from memrelay_eval.analysis.reports import (
    ReportInput,
    build_stage_report_input,
    publish_report,
    render_report,
)
from memrelay_eval.application.copilot_services import (
    CopilotSdkClient,
    bootstrap_runtime,
    eligible_models,
    qualify_native_catalog,
)
from memrelay_eval.application.observation_services import (
    execute_product_observation_composition,
    product_observation_receipt_sha256,
    qualify_observation,
    resolve_product_observation_identity,
    verified_product_observation_evidence,
)
from memrelay_eval.application.telemetry_services import (
    DEFAULT_COLLECTOR_ARCHIVE_NAME,
    verify_local_telemetry_bootstrap,
)
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.catalog.compiler import compile_catalog_command
from memrelay_eval.catalog.validation import CatalogValidationError, validate_catalog
from memrelay_eval.domain.entities import QualificationCaps
from memrelay_eval.domain.errors import (
    AnalysisError,
    ConformancePauseError,
    CrossRepositoryDeniedError,
    InvalidConfigurationError,
    ObservationQualificationError,
    StageControlError,
)
from memrelay_eval.domain.observation import (
    ObservationContract,
    generate_sentinels,
    observation_contract_from_document,
    require_new_protocol,
)
from memrelay_eval.evidence.backup import preflight_backup_root
from memrelay_eval.evidence.conformance import (
    CONFORMANCE_LABEL,
    ConformanceContext,
    ProbeResult,
    ProofRegistry,
    build_bootstrap_receipt,
    build_conformance_report,
    load_bootstrap_receipt,
    observed_probe_result,
    provider_proof_registry,
    report_bytes,
    require_enrollment_conformance,
    unpaid_proof_registry,
    write_bootstrap_receipt,
    write_conformance_report,
)
from memrelay_eval.evidence.manifest import (
    observation_qualification_manifest,
    stage_command_manifest,
)
from memrelay_eval.orchestration.configuration import (
    load_evaluator_toml,
    resolve_effective_configuration,
)
from memrelay_eval.orchestration.control import (
    LockRepository,
    reuse_or_reject_model_lock,
    write_model_lock,
)
from memrelay_eval.orchestration.limits import (
    PrimaryModelStageLimits,
    SecondaryModelStageLimits,
)
from memrelay_eval.orchestration.pilot import (
    PilotExitStore,
    authorize_pilot_plan,
    load_pilot_exit_evidence,
    load_pilot_plan,
)
from memrelay_eval.orchestration.stages import (
    StageAuthorization,
    StageEntryBundle,
    StageExitBundle,
    authorize_stage_entry,
    load_authorization,
    load_entry_bundle,
    load_exit_bundle,
    load_primary_stage_conclusion,
    load_primary_stage_plan,
    refuse_cross_repository_stage,
    seal_primary_stage_plan,
    seal_secondary_stage_plan,
)


def show_foundation_status(_: Namespace) -> int:
    print("memrelay-eval foundation: unpaid conformance adapters only")
    return 0


def show_effective_configuration(args: Namespace) -> int:
    """Resolve explicit sources and print only the redacted canonical projection."""
    evaluator_file = load_evaluator_toml(Path(args.config)) if args.config is not None else {}
    cli: dict[str, object] = {
        "stage": args.stage,
        "timeout_seconds": args.timeout_seconds,
        "max_concurrency": args.max_concurrency,
    }
    if args.network_policy is not None:
        try:
            cli["network_policy"] = json.loads(args.network_policy)
        except json.JSONDecodeError as error:
            raise InvalidConfigurationError() from error
    if args.credential_reference:
        cli["credential_references"] = [
            {"variable_name": variable, "target_process": process}
            for variable, process in (
                _split_credential_reference(value) for value in args.credential_reference
            )
        ]
    configuration = resolve_effective_configuration(cli=cli, evaluator_file=evaluator_file)
    print(canonical_bytes(configuration.to_document()).decode("utf-8"))
    return 0


_CI_ENV_MARKERS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "JENKINS_URL",
    "TF_BUILD",
    "TEAMCITY_VERSION",
    "CIRCLECI",
)

# Ambient configuration that would silently redirect stage topology, authority,
# or paid execution is refused: authority must come from explicit sealed inputs.
_AMBIENT_STAGE_CONFIG_MARKERS = (
    "MEMRELAY_EVAL_STAGE",
    "MEMRELAY_EVAL_TOPOLOGY",
    "MEMRELAY_EVAL_FALLBACK",
    "MEMRELAY_EVAL_AUTHORIZATION",
    "MEMRELAY_EVAL_ENTRY_BUNDLE",
    "MEMRELAY_EVAL_PREDECESSOR_EXIT",
    "MEMRELAY_EVAL_PAID",
    "MEMRELAY_EVAL_AUTO_PROMOTE",
    "MEMRELAY_EVAL_PROMOTE",
)

_ENROLLABLE_STAGE_CHOICES = ("integration", "pilot", "primary", "secondary")


def run_stage(args: Namespace) -> int:
    """Route a noninteractive execution-stage request, failing closed by default.

    ``cross-repo`` preserves the Story 7.3 deny-before-discovery contract exactly.
    The paid enrollable stages fail closed before enrollment whenever their sealed
    predecessor authority is absent, corrupt, rejected, incomplete, or expired, and
    they never accept ambient configuration, automatic topology fallback, or CI-driven
    paid execution.
    """

    if args.stage == "cross-repo":
        try:
            refuse_cross_repository_stage()
        except CrossRepositoryDeniedError as error:
            print(f"execution denied: {error.reason}")
            return 2
        raise AssertionError("cross-repository execution must remain unavailable in evaluator v1")

    if args.stage not in _ENROLLABLE_STAGE_CHOICES:
        raise ValueError("unsupported evaluator stage")
    return _run_enrollable_stage(args)


def _run_enrollable_stage(args: Namespace) -> int:
    """Guard one paid enrollable stage entry and emit an immutable command manifest."""

    from memrelay_eval.domain.states import StageKind

    stage = args.stage
    environment = dict(os.environ)
    input_hashes: dict[str, str] = {}
    output_hashes: dict[str, str] = {}
    runtime_lock_sha256: str | None = None
    protocol_sha256: str | None = None
    error_code: str | None = None
    terminal_status = "succeeded"
    exit_code = 0

    try:
        _reject_ambient_stage_environment(environment)
        _reject_ci_paid_execution(environment)
        entry_path, predecessor_path, authorization_path = _require_stage_inputs(args)
        entry_bytes = _read_stage_input(entry_path, "stage_entry_bundle_unreadable")
        predecessor_bytes = _read_stage_input(predecessor_path, "predecessor_exit_unreadable")
        authorization_bytes = _read_stage_input(
            authorization_path, "stage_authorization_unreadable"
        )
        input_hashes = {
            "authorization": sha256(authorization_bytes).hexdigest(),
            "predecessor_exit": sha256(predecessor_bytes).hexdigest(),
            "stage_entry_bundle": sha256(entry_bytes).hexdigest(),
        }
        admission_entry = load_entry_bundle(entry_bytes)
        predecessor_exit = load_exit_bundle(predecessor_bytes)
        authorization = load_authorization(authorization_bytes)
        report_path = getattr(args, "conformance_report", None)
        if not report_path:
            raise StageControlError("conformance_report_missing")
        bootstrap_path = getattr(args, "bootstrap_receipt", None)
        if not bootstrap_path:
            raise StageControlError("bootstrap_receipt_missing")
        try:
            report_bytes_input = Path(report_path).read_bytes()
            bootstrap_bytes_input = Path(bootstrap_path).read_bytes()
        except OSError as error:
            raise StageControlError("conformance_authority_unreadable") from error
        # The initial paid stage must lock the complete conformance authority to
        # its exact immutable inputs. Later stages still require the intact passed
        # report, but link their own predecessor exit through Story 6.1 bundles.
        try:
            require_enrollment_conformance(
                report_bytes_input,
                bootstrap_data=bootstrap_bytes_input,
                stage_locks=admission_entry.locks if stage == "integration" else None,
            )
        except ConformancePauseError as error:
            raise StageControlError(error.code) from error
        runtime_lock_sha256 = admission_entry.locks["runtime_lock_sha256"]
        protocol_sha256 = admission_entry.locks["protocol_sha256"]
        authorize_stage_entry(
            stage_kind=StageKind(stage),
            entry_bundle=admission_entry,
            predecessor_exit=predecessor_exit,
            authorization=authorization,
            now=datetime.now(UTC),
        )
        if stage == "pilot":
            pilot_plan_path = getattr(args, "pilot_plan", None)
            if not pilot_plan_path:
                raise StageControlError("pilot_plan_required")
            pilot_plan_bytes = _read_stage_input(pilot_plan_path, "pilot_plan_unreadable")
            pilot_plan = load_pilot_plan(pilot_plan_bytes)
            authorize_pilot_plan(admission_entry, pilot_plan)
            input_hashes["pilot_plan"] = sha256(pilot_plan_bytes).hexdigest()
        output_hashes = {
            "stage_authorization": authorization.digest,
            "stage_entry_bundle": admission_entry.digest,
        }
        if stage == "pilot":
            output_hashes["pilot_plan"] = pilot_plan.digest
        if stage == "primary":
            plan_bytes = _seal_primary_plan(
                args,
                entry_bundle=admission_entry,
                predecessor_exit=predecessor_exit,
                authorization=authorization,
            )
            output_hashes["primary_stage_plan"] = sha256(plan_bytes).hexdigest()
            input_hashes.update(_model_stage_input_hashes(args, secondary=False))
            _write_immutable_stage_manifest(
                Path(args.output_root)
                / "stage-plans"
                / f"primary-{output_hashes['primary_stage_plan']}.json",
                plan_bytes,
            )
        elif stage == "secondary":
            plan_bytes = _seal_secondary_plan(
                args,
                primary_exit=predecessor_exit,
            )
            output_hashes["secondary_stage_plan"] = sha256(plan_bytes).hexdigest()
            input_hashes.update(_model_stage_input_hashes(args, secondary=True))
            _write_immutable_stage_manifest(
                Path(args.output_root)
                / "stage-plans"
                / f"secondary-{output_hashes['secondary_stage_plan']}.json",
                plan_bytes,
            )
    except StageControlError as error:
        error_code = error.code
        terminal_status = "refused"
        exit_code = 2

    manifest = stage_command_manifest(
        command="run",
        stage=stage,
        terminal_status=terminal_status,
        exit_code=exit_code,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        runtime_lock_sha256=runtime_lock_sha256,
        protocol_sha256=protocol_sha256,
        error_code=error_code,
    )
    digest = json.loads(manifest.decode("utf-8"))["digest"]
    manifest_path = Path(args.output_root) / "commands" / f"run-{stage}-{digest}.json"
    try:
        _write_immutable_stage_manifest(manifest_path, manifest)
    finally:
        # Emit exactly one terminal manifest on every path, including a failed
        # publish, so a disk/permission fault never silently swallows the
        # required command manifest contract.
        print(manifest.decode("utf-8"))
    return exit_code


def _reject_ambient_stage_environment(environment: dict[str, str]) -> None:
    present = tuple(
        sorted(marker for marker in _AMBIENT_STAGE_CONFIG_MARKERS if environment.get(marker))
    )
    if present:
        raise StageControlError("ambient_stage_configuration_forbidden", present)


def _reject_ci_paid_execution(environment: dict[str, str]) -> None:
    markers = tuple(sorted(marker for marker in _CI_ENV_MARKERS if environment.get(marker)))
    if markers:
        raise StageControlError("paid_execution_forbidden_in_ci", markers)


def _seal_primary_plan(
    args: Namespace,
    *,
    entry_bundle: StageEntryBundle,
    predecessor_exit: StageExitBundle,
    authorization: StageAuthorization,
) -> bytes:
    task_document, _task_bytes = _required_canonical_json(
        getattr(args, "task_plan", None), "primary_task_plan_missing"
    )
    limits_document, _limits_bytes = _required_canonical_json(
        getattr(args, "limits", None), "primary_limits_missing"
    )
    plan = seal_primary_stage_plan(
        entry_bundle=entry_bundle,
        pilot_exit=predecessor_exit,
        authorization=authorization,
        now=datetime.now(UTC),
        task_families=_task_families(task_document),
        limits=_primary_limits(limits_document),
    )
    return plan.bytes()


def _seal_secondary_plan(args: Namespace, *, primary_exit: StageExitBundle) -> bytes:
    task_document, _task_bytes = _required_canonical_json(
        getattr(args, "task_plan", None), "secondary_task_plan_missing"
    )
    limits_document, _limits_bytes = _required_canonical_json(
        getattr(args, "limits", None), "secondary_limits_missing"
    )
    model_lock, _model_lock_bytes = _required_canonical_json(
        getattr(args, "model_lock", None), "secondary_model_lock_missing"
    )
    primary_plan = load_primary_stage_plan(
        _read_stage_input(getattr(args, "primary_plan", None) or "", "primary_plan_unreadable")
    )
    conclusion = load_primary_stage_conclusion(
        _read_stage_input(
            getattr(args, "primary_conclusion", None) or "", "primary_conclusion_unreadable"
        )
    )
    entry_values = _role_stage_inputs(getattr(args, "secondary_entry", None), load_entry_bundle)
    authorization_values = _role_stage_inputs(
        getattr(args, "secondary_authorization", None), load_authorization
    )
    entries = {
        role: value for role, value in entry_values.items() if isinstance(value, StageEntryBundle)
    }
    authorizations = {
        role: value
        for role, value in authorization_values.items()
        if isinstance(value, StageAuthorization)
    }
    if len(entries) != len(entry_values) or len(authorizations) != len(authorization_values):
        raise StageControlError("secondary_role_input_invalid")
    if set(entries) != set(authorizations):
        raise StageControlError("secondary_role_authorization_missing")
    plan = seal_secondary_stage_plan(
        primary_plan=primary_plan,
        primary_exit=primary_exit,
        primary_conclusion=conclusion,
        model_lock=model_lock,
        role_entry_bundles=entries,
        role_authorizations=authorizations,
        now=datetime.now(UTC),
        task_families=_task_families(task_document),
        limits=_secondary_limits(limits_document),
    )
    return plan.bytes()


def _required_canonical_json(path: str | None, code: str) -> tuple[dict[str, object], bytes]:
    if not path:
        raise StageControlError(code)
    data = _read_stage_input(path, code)
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StageControlError(code) from error
    if not isinstance(document, dict) or canonical_bytes(document) != data:
        raise StageControlError(code)
    return document, data


def _model_stage_input_hashes(args: Namespace, *, secondary: bool) -> dict[str, str]:
    paths = {
        "limits": getattr(args, "limits", None),
        "task_plan": getattr(args, "task_plan", None),
    }
    if secondary:
        paths |= {
            "model_lock": getattr(args, "model_lock", None),
            "primary_conclusion": getattr(args, "primary_conclusion", None),
            "primary_plan": getattr(args, "primary_plan", None),
        }
    return {
        name: sha256(_read_stage_input(path, f"{name}_unreadable")).hexdigest()
        for name, path in paths.items()
        if path
    }


def _task_families(document: dict[str, object]) -> dict[str, tuple[str, ...]]:
    value = document.get("task_families")
    if not isinstance(value, dict):
        raise StageControlError("model_stage_task_plan_invalid")
    if any(
        not isinstance(family, str)
        or not family
        or not isinstance(task_ids, list)
        or any(not isinstance(task_id, str) or not task_id for task_id in task_ids)
        for family, task_ids in value.items()
    ):
        raise StageControlError("model_stage_task_plan_invalid")
    return {family: tuple(task_ids) for family, task_ids in value.items()}


def _primary_limits(document: dict[str, object]) -> PrimaryModelStageLimits:
    try:
        return PrimaryModelStageLimits(
            ai_credit_cap=float(document["ai_credit_cap"]),
            usd_cap=float(document["usd_cap"]),
            task_class_active_seconds=dict(document["task_class_active_seconds"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StageControlError("primary_limits_invalid") from error


def _secondary_limits(document: dict[str, object]) -> SecondaryModelStageLimits:
    try:
        return SecondaryModelStageLimits(
            ai_credit_cap=float(document["ai_credit_cap"]),
            usd_cap=float(document["usd_cap"]),
            task_class_active_seconds=dict(document["task_class_active_seconds"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise StageControlError("secondary_limits_invalid") from error


def _role_stage_inputs(
    values: list[str] | None, loader: Callable[[bytes], StageEntryBundle | StageAuthorization]
) -> dict[str, StageEntryBundle | StageAuthorization]:
    if not values:
        raise StageControlError("secondary_role_authorization_missing")
    result: dict[str, object] = {}
    for value in values:
        try:
            role, path = value.split(":", 1)
        except ValueError as error:
            raise StageControlError("secondary_role_input_invalid") from error
        if role not in {"M1", "M2"} or role in result or not path:
            raise StageControlError("secondary_role_input_invalid")
        result[role] = loader(_read_stage_input(path, "secondary_role_input_unreadable"))
    return result


def _require_stage_inputs(args: Namespace) -> tuple[str, str, str]:
    entry = getattr(args, "entry_bundle", None)
    predecessor = getattr(args, "predecessor_exit", None)
    authorization = getattr(args, "authorization", None)
    missing = tuple(
        name
        for name, value in (
            ("--authorization", authorization),
            ("--entry-bundle", entry),
            ("--predecessor-exit", predecessor),
        )
        if not value
    )
    if missing:
        raise StageControlError("stage_inputs_incomplete", missing)
    return entry, predecessor, authorization


def _read_stage_input(path: str, code: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as error:
        raise StageControlError(code, (path,)) from error


def _write_immutable_stage_manifest(path: Path, data: bytes) -> None:
    """Append one stage command manifest exactly once, refusing silent mutation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            raise StageControlError("stage_command_manifest_conflict", (str(path),))
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    except OSError as error:
        if path.is_file() and path.read_bytes() == data:
            return
        raise StageControlError("stage_command_manifest_publish_failed", (str(path),)) from error
    finally:
        if temporary.exists():
            temporary.unlink()
    if not path.is_file() or path.read_bytes() != data:
        raise StageControlError("stage_command_manifest_publish_failed", (str(path),))


def gate_pilot(args: Namespace) -> int:
    """Seal a whole-pilot exit decision from blinded, frozen evidence only."""

    plan_bytes = _read_stage_input(args.pilot_plan, "pilot_plan_unreadable")
    evidence_bytes = _read_stage_input(args.exit_evidence, "pilot_exit_evidence_unreadable")
    plan = load_pilot_plan(plan_bytes)
    evidence = load_pilot_exit_evidence(evidence_bytes)
    decision, _path, _outcome = PilotExitStore(Path(args.output_root)).gate(plan, evidence)
    print(decision.bytes().decode("utf-8"))
    return 0 if decision.status == "accepted" else 2


def bootstrap(
    args: Namespace,
    *,
    evaluation_root: Path | None = None,
    telemetry_verifier: Callable[[Path, Path], object] = verify_local_telemetry_bootstrap,
    runtime_bootstrap: Callable[[LockRepository, Path], object] = bootstrap_runtime,
    backup_root_validator: Callable[[Path], None] | None = None,
) -> int:
    """Perform the one permitted runtime download after checking the evidence root."""

    backup_root = Path(args.backup_root).expanduser().resolve()
    root = evaluation_root or Path(__file__).parents[3]
    if backup_root_validator is None:
        (root / "artifacts").mkdir(parents=True, exist_ok=True)
        preflight_backup_root(root / "artifacts", backup_root)
    else:
        backup_root_validator(backup_root)
    repository = LockRepository(root / "artifacts")
    archive_path = (
        Path(args.collector_archive).expanduser().resolve()
        if getattr(args, "collector_archive", None)
        else root / "collector" / DEFAULT_COLLECTOR_ARCHIVE_NAME
    )
    verification = telemetry_verifier(root, archive_path)
    runtime_lock = runtime_bootstrap(repository, root / "uv.lock")
    if not isinstance(runtime_lock, dict):
        runtime_lock = repository.read("runtime-lock.json")
    if not isinstance(runtime_lock, dict):
        raise ConformancePauseError(
            "bootstrap_runtime_lock_missing", "runtime bootstrap produced no lock"
        )
    environment_sha256 = getattr(args, "environment_sha256", None)
    protocol_sha256 = getattr(args, "protocol_sha256", None)
    if not isinstance(environment_sha256, str):
        environment_sha256 = sha256(
            canonical_bytes(
                {
                    "implementation": sys.implementation.name,
                    "python": tuple(sys.version_info[:3]),
                    "platform": sys.platform,
                }
            )
        ).hexdigest()
    if not isinstance(protocol_sha256, str):
        protocol_sha256 = sha256(
            canonical_bytes(
                {
                    "bootstrap_protocol": "2.0.0",
                    "runtime_lock": runtime_lock["lock_sha256"],
                }
            )
        ).hexdigest()
    telemetry_sha256 = getattr(getattr(verification, "evidence", None), "sha256", None)
    if not isinstance(telemetry_sha256, str):
        raise ConformancePauseError(
            "bootstrap_telemetry_evidence_missing", "telemetry verification retained no evidence"
        )
    receipt = build_bootstrap_receipt(
        mode=getattr(args, "mode", "provider_qualification"),
        runtime_lock=runtime_lock,
        input_hashes={
            "collector_archive": sha256(archive_path.read_bytes()).hexdigest(),
            "runtime_lock": str(runtime_lock["lock_sha256"]),
            "uv_lock": sha256((root / "uv.lock").read_bytes()).hexdigest(),
        },
        output_hashes={
            "backup_second_volume_preflight": sha256(
                canonical_bytes(
                    {
                        "backup_root": str(backup_root),
                        "artifact_root": str(root / "artifacts"),
                    }
                )
            ).hexdigest(),
            "telemetry_bootstrap": telemetry_sha256,
        },
        environment_sha256=environment_sha256,
        protocol_sha256=protocol_sha256,
        runtime_download_disabled=os.environ.get("COPILOT_SKIP_CLI_DOWNLOAD") == "1",
    )
    receipt_path = write_bootstrap_receipt(root / "artifacts", receipt)
    print(
        "Copilot runtime and telemetry substrate verified; "
        f"telemetry evidence {telemetry_sha256} retained; bootstrap receipt {receipt_path}"
    )
    return 0


def conformance(
    args: Namespace,
    *,
    proof_registry: ProofRegistry | None = None,
    provider_probe: Callable[[str, ConformanceContext], ProbeResult] | None = None,
) -> int:
    """Execute the required proof registry under explicit unpaid or paid authority."""

    from memrelay_eval.orchestration.planning import plan_offline

    mode = getattr(args, "mode", "unpaid_ci")
    root = Path(args.output_root)
    catalog_path = Path(args.catalog)
    stage_locks = _canonical_stage_locks(Path(args.stage_locks))
    bootstrap_path = getattr(args, "bootstrap_receipt", None)
    if not bootstrap_path:
        raise ConformancePauseError(
            "bootstrap_receipt_missing", "conformance requires an immutable bootstrap receipt"
        )
    try:
        bootstrap_bytes = Path(bootstrap_path).read_bytes()
    except OSError as error:
        raise ConformancePauseError(
            "bootstrap_receipt_unreadable", "bootstrap receipt cannot be read"
        ) from error
    bootstrap_document = load_bootstrap_receipt(bootstrap_bytes)
    if (
        bootstrap_document["mode"] != mode
        or bootstrap_document["runtime_lock_sha256"] != stage_locks["runtime_lock_sha256"]
        or bootstrap_document["protocol_sha256"] != stage_locks["protocol_sha256"]
        or bootstrap_document["environment_sha256"] != stage_locks["environment_sha256"]
    ):
        raise ConformancePauseError(
            "bootstrap_receipt_authority_conflict",
            "bootstrap receipt does not bind this conformance environment",
        )
    if mode == "provider_qualification":
        _require_provider_qualification_authorization(args)
        registry = proof_registry or provider_proof_registry(
            provider_probe or _official_provider_probe
        )
    elif mode == "unpaid_ci":
        registry = proof_registry or unpaid_proof_registry()
    else:
        raise ConformancePauseError("conformance_mode_invalid", "invalid conformance mode")
    catalog_hash = sha256(catalog_path.read_bytes()).hexdigest()
    planning_root = root / f"unpaid-catalog-{catalog_hash}"
    synthetic_catalog = planning_root / catalog_path.name
    if not planning_root.exists():
        shutil.copytree(catalog_path.parent, planning_root)
    elif (
        not synthetic_catalog.is_file()
        or synthetic_catalog.read_bytes() != catalog_path.read_bytes()
    ):
        raise ConformancePauseError(
            "unpaid_catalog_source_conflict",
            "the retained synthetic catalog does not match the requested catalog",
        )
    result = plan_offline(
        catalog_path=synthetic_catalog,
        output_dir=planning_root / "generated",
        manifest_path=planning_root / "plan-manifest.json",
        lock_path=planning_root / "catalog-lock.json",
    )
    if result.terminal_status != "succeeded":
        raise ConformancePauseError("unpaid_vertical_slice_failed", result.error_code or "unknown")
    vertical_slice_hash = sha256(
        canonical_bytes(
            {
                "catalog_input_hashes": dict(sorted(result.input_hashes.items())),
                "catalog_output_hashes": dict(sorted(result.output_hashes.items())),
                "manifest_ref": result.manifest_ref,
                "protocol_id": result.protocol_id,
            }
        )
    ).hexdigest()
    evaluation_root = Path(__file__).parents[3]
    context = ConformanceContext(
        mode=mode,
        evaluation_root=evaluation_root,
        run_root=planning_root,
        stage_locks=stage_locks,
        bootstrap_receipt=bootstrap_document,
    )
    receipts = registry.execute(context)
    report = build_conformance_report(
        mode=mode,
        stage_locks=stage_locks,
        proof_receipts=receipts,
        input_hashes={
            "bootstrap_receipt_sha256": sha256(bootstrap_bytes).hexdigest(),
            "catalog_to_report_sha256": vertical_slice_hash,
            "catalog_yaml_sha256": catalog_hash,
            "stage_locks_sha256": sha256(canonical_bytes(stage_locks)).hexdigest(),
        },
        bootstrap_receipt_sha256=sha256(bootstrap_bytes).hexdigest(),
    )
    path = write_conformance_report(root, report)
    print(
        canonical_bytes(
            {
                "artifact_type": "conformance_report_reference",
                "evidence_label": CONFORMANCE_LABEL,
                "path": str(path),
                "report_id": report["report_id"],
                "report_sha256": sha256(report_bytes(report)).hexdigest(),
                "status": report["status"],
            }
        ).decode("utf-8")
    )
    return 0 if report["status"] == "passed" else 2


def _require_provider_qualification_authorization(args: Namespace) -> None:
    """Require an independently sealed, paid Story 6.1 authority before any SDK call."""

    _reject_ci_paid_execution(dict(os.environ))
    entry_path = getattr(args, "entry_bundle", None)
    authorization_path = getattr(args, "authorization", None)
    if not entry_path or not authorization_path:
        raise StageControlError("provider_qualification_authorization_missing")
    entry = load_entry_bundle(_read_stage_input(entry_path, "stage_entry_bundle_unreadable"))
    authorization = load_authorization(
        _read_stage_input(authorization_path, "stage_authorization_unreadable")
    )
    if not authorization.paid_execution:
        raise StageControlError("provider_qualification_paid_authorization_required")
    if (
        authorization.stage_id != entry.stage_id
        or authorization.stage_kind is not entry.stage_kind
        or authorization.protocol_id != entry.protocol_id
        or authorization.entry_bundle_sha256 != entry.digest
        or authorization.envelope_sha256 != entry.envelope_sha256
        or not authorization.is_current(datetime.now(UTC))
    ):
        raise StageControlError("provider_qualification_authorization_invalid")


def _official_provider_probe(proof_id: str, context: ConformanceContext) -> ProbeResult:
    """Cross the official SDK boundary only after explicit paid authorization."""

    client = CopilotSdkClient()
    if proof_id == "AUTH-COPILOT-SUBSCRIPTION":
        subject = asyncio.run(client.authenticated_subscription_subject())
        return observed_probe_result(
            input_documents={"provider_probe": {"proof_id": proof_id, "mode": context.mode}},
            output_documents={
                "subscription_subject_sha256": sha256(subject.encode("utf-8")).hexdigest()
            },
            terminal="official_subscription_authenticated",
        )
    catalog = asyncio.run(client.archive_models())
    if proof_id == "MODEL-CATALOG-SNAPSHOT":
        return observed_probe_result(
            input_documents={"provider_probe": {"proof_id": proof_id, "mode": context.mode}},
            output_documents={"native_catalog": catalog.to_document()},
            terminal="official_catalog_archived",
        )
    qualified, evidence = asyncio.run(
        qualify_native_catalog(
            catalog,
            QualificationCaps(
                credit_cap=float(getattr(context, "credit_cap", 1.0)),
                token_cap=1_000,
                active_seconds_cap=30.0,
                wall_seconds_cap=60.0,
            ),
        )
    )
    return observed_probe_result(
        input_documents={"provider_probe": {"proof_id": proof_id, "mode": context.mode}},
        output_documents={
            "qualification_evidence": evidence.to_document(),
            "qualified_native_ids": tuple(item.native_id for item in qualified),
        },
        terminal="official_catalog_qualified",
    )


def _canonical_stage_locks(path: Path) -> dict[str, str]:
    try:
        data = path.read_bytes()
        document = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConformancePauseError(
            "conformance_stage_locks_unreadable", "conformance stage locks cannot be read"
        ) from error
    if not isinstance(document, dict) or canonical_bytes(document) != data:
        raise ConformancePauseError(
            "conformance_stage_locks_not_canonical", "conformance stage locks must be canonical"
        )
    if any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in document.items()
    ):
        raise ConformancePauseError(
            "conformance_stage_locks_invalid", "conformance stage locks must map strings"
        )
    return dict(document)


def lock_models(
    args: Namespace,
    *,
    repository: LockRepository | None = None,
    archive_models: Callable[[], Awaitable[Any]] | None = None,
    qualify: Callable[
        [Any, QualificationCaps], Awaitable[tuple[tuple[Any, ...], Any]]
    ] = qualify_native_catalog,
) -> int:
    """Run exactly eight arm-blind nonstudy sessions per eligible native model."""

    evaluation_root = Path(__file__).parents[3]
    repository = repository or LockRepository(evaluation_root / "artifacts")
    runtime_lock = repository.read("runtime-lock.json")
    if runtime_lock is None:
        raise ConformancePauseError(
            "runtime_lock_missing",
            "bootstrap must produce a runtime lock before model qualification",
        )
    existing = reuse_or_reject_model_lock(
        repository,
        runtime_lock,
        credit_limit=args.credit_cap,
        token_limit=args.token_cap,
        active_seconds_limit=args.active_seconds_cap,
        wall_seconds_limit=args.wall_seconds_cap,
    )
    if existing is not None:
        print("Existing native model lock verified; no qualification sessions were run")
        return 0
    if archive_models is None:
        archive_models = CopilotSdkClient().archive_models
    archive = asyncio.run(archive_models())
    eligible_count = len(eligible_models(archive.catalog))
    if eligible_count == 0:
        raise ConformancePauseError(
            "no_eligible_models", "native catalog has no qualified model candidates"
        )
    caps = QualificationCaps(
        session_limit=eligible_count * 8,
        credit_limit=args.credit_cap,
        token_limit=args.token_cap,
        active_seconds_limit=args.active_seconds_cap,
        wall_seconds_limit=args.wall_seconds_cap,
    )
    qualifications, consumption = asyncio.run(qualify(archive.catalog, caps))
    write_model_lock(repository, runtime_lock, archive, caps, qualifications, consumption)
    print("Native model catalog and qualification lock written")
    return 0


def validate_authored_catalog(args: Namespace) -> int:
    """Validate source YAML without compiling tasks, emitting a lock, or using a provider."""

    try:
        result = validate_catalog(Path(args.catalog), prior_lock=_optional_path(args.prior_lock))
    except CatalogValidationError as error:
        for diagnostic in error.diagnostics:
            print(diagnostic)
        return 1
    print(f"{result.source_path}: valid ({result.change_kind} change)")
    return 0


def compile_authored_catalog(args: Namespace) -> int:
    """Compile a validated catalog without provider, fixture-content, or eligibility work."""

    result = compile_catalog_command(
        Path(args.catalog),
        output_dir=Path(args.output_dir),
        lock_path=Path(args.lock),
        manifest_path=Path(args.manifest),
        prior_lock=_optional_path(args.prior_lock),
        runtime_lock=_optional_path(args.runtime_lock),
    )
    if isinstance(result.error, CatalogValidationError):
        for diagnostic in result.error.diagnostics:
            print(diagnostic)
    elif result.error is not None:
        print(result.error)
    if result.terminal_status == "succeeded":
        print("catalog compiled: canonical unpaid-conformance artifacts published")
    return result.exit_code


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value is not None else None


def _split_credential_reference(value: str) -> tuple[str, str]:
    variable, separator, process = value.partition(":")
    if not separator or not variable or not process:
        raise InvalidConfigurationError()
    return variable, process


def plan_offline_command(args: Namespace) -> int:
    """Run the deterministic offline catalog-to-planned-run dry run."""
    from memrelay_eval.orchestration.planning import (
        plan_offline,
        plan_offline_to_command_manifest,
    )

    catalog_path = Path(args.catalog)
    output_dir = Path(args.output_dir)
    manifest_path = Path(args.manifest)
    lock_path = Path(args.lock) if args.lock else None
    prior_lock_path = (
        _optional_path(args.prior_lock) if hasattr(args, "prior_lock") and args.prior_lock else None
    )
    runtime_lock_path = (
        _optional_path(args.runtime_lock)
        if hasattr(args, "runtime_lock") and args.runtime_lock
        else None
    )

    try:
        result = plan_offline(
            catalog_path=catalog_path,
            output_dir=output_dir,
            manifest_path=manifest_path,
            lock_path=lock_path,
            prior_lock=prior_lock_path,
            runtime_lock=runtime_lock_path,
        )
    except KeyboardInterrupt:
        from memrelay_eval.orchestration.planning import (
            PlanningResult,
        )
        from memrelay_eval.orchestration.planning import (
            plan_offline_to_command_manifest as to_manifest,
        )

        result_interrupted = PlanningResult(
            terminal_status="interrupted",
            exit_code=130,
            error_code="keyboard_interrupt",
        )
        print(to_manifest(result_interrupted).decode("utf-8"))
        return 130

    command_manifest = plan_offline_to_command_manifest(result)
    print(command_manifest.decode("utf-8"))
    return result.exit_code


def observation_conformance(args: Namespace) -> int:
    """Execute one configured product composition and qualify its retained native evidence."""

    if (
        not isinstance(args.sentinel_count, int)
        or isinstance(args.sentinel_count, bool)
        or args.sentinel_count <= 0
        or not isinstance(args.window_seconds, int)
        or isinstance(args.window_seconds, bool)
        or args.window_seconds <= 0
    ):
        raise ObservationQualificationError("observation_execution_parameters_invalid")
    input_path = Path(args.input)
    document, input_bytes = _canonical_observation_input(input_path)
    contract_value = document.get("contract")
    if not isinstance(contract_value, dict):
        raise ObservationQualificationError("observation_qualification_input_invalid")
    try:
        requested_contract = observation_contract_from_document(contract_value)
    except ValueError as error:
        raise ObservationQualificationError("observation_qualification_input_invalid") from error

    output_root = Path(args.output_root)
    workspace = output_root / "observation-runs" / requested_contract.path.value / uuid.uuid4().hex
    identity, product_config = resolve_product_observation_identity(
        path=requested_contract.path,
        product_config_path=Path(args.product_config),
        runtime_lock_path=Path(args.runtime_lock),
        workspace=workspace,
    )
    protocol_sha256 = canonical_digest(
        {
            "protocol_version": identity.protocol_version,
            "conformance_sha256": identity.conformance_sha256,
        }
    )
    # The shared Story 6.1 command-manifest wrapper reads these values after
    # this handler returns or raises, binding that terminal record to this path.
    args.runtime_lock_sha256 = identity.runtime_lock_sha256
    args.protocol_sha256 = protocol_sha256
    window_started_at = datetime.now(UTC)
    contract = ObservationContract(
        path=requested_contract.path,
        identity=identity,
        expected_sentinels=generate_sentinels(args.sentinel_count),
        window_started_at=window_started_at,
        deadline_at=window_started_at + timedelta(seconds=args.window_seconds),
    )
    require_new_protocol(requested_contract, contract)

    run = execute_product_observation_composition(
        contract=contract,
        config=product_config,
        workspace=workspace,
        fault_injections=tuple(args.fault_injection),
    )
    native_receipt_sha256 = product_observation_receipt_sha256(run)
    _persist_observation_native_receipt(
        output_root,
        path=contract.path.value,
        conformance_sha256=identity.conformance_sha256,
        receipt=run.receipt,
    )
    evidence = verified_product_observation_evidence(
        contract,
        run,
        native_receipt_persisted=True,
    )
    decision = qualify_observation(contract, evidence, decided_at=datetime.now(UTC))
    input_hashes = {
        "observation_contract_request": sha256(input_bytes).hexdigest(),
        "native_observation_receipt": native_receipt_sha256,
        "configuration": identity.configuration_sha256,
        "reconciliation_policy": identity.reconciliation_policy_sha256,
        "semantic_map": identity.semantic_map_sha256,
        "sentinel_contract": identity.sentinel_contract_sha256,
        "source_implementation": identity.source_implementation_sha256,
    }
    output_hashes = {
        "native_observation_receipt": native_receipt_sha256,
        "observation_qualification": decision.decision_sha256,
    }
    qualification_manifest = observation_qualification_manifest(
        path=contract.path.value,
        conformance_sha256=identity.conformance_sha256,
        protocol_version=identity.protocol_version,
        protocol_sha256=protocol_sha256,
        terminal_status="succeeded" if decision.qualified else "failed",
        error_code=None if decision.qualified else decision.assessment.reason_code,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        runtime_lock_sha256=identity.runtime_lock_sha256,
    )
    _persist_observation_decision(
        output_root,
        path=contract.path.value,
        conformance_sha256=identity.conformance_sha256,
        decision_bytes=canonical_bytes(decision.to_document()),
        qualification_manifest=qualification_manifest,
    )
    print(canonical_bytes(decision.to_document()).decode("utf-8"))
    if not decision.qualified:
        raise ObservationQualificationError(decision.assessment.reason_code)
    return 0


def _canonical_observation_input(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        data = path.read_bytes()
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ObservationQualificationError("observation_qualification_input_invalid") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"contract"}
        or canonical_bytes(document) != data
    ):
        raise ObservationQualificationError("observation_qualification_input_invalid")
    return document, data


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate_observation_input_key")
        document[key] = value
    return document


def _persist_observation_decision(
    output_root: Path,
    *,
    path: str,
    conformance_sha256: str,
    decision_bytes: bytes,
    qualification_manifest: bytes,
) -> None:
    directory = output_root / "observation-qualification" / path / conformance_sha256
    decision_sha256 = sha256(decision_bytes).hexdigest()
    manifest_sha256 = sha256(qualification_manifest).hexdigest()
    _write_immutable_observation_file(
        directory / f"decision-{decision_sha256}.json", decision_bytes
    )
    _write_immutable_observation_file(
        directory / f"manifest-{manifest_sha256}.json", qualification_manifest
    )


def _persist_observation_native_receipt(
    output_root: Path,
    *,
    path: str,
    conformance_sha256: str,
    receipt: bytes,
) -> None:
    directory = output_root / "observation-qualification" / path / conformance_sha256
    receipt_sha256 = sha256(receipt).hexdigest()
    _write_immutable_observation_file(directory / f"native-receipt-{receipt_sha256}.json", receipt)


def _write_immutable_observation_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_file() and path.read_bytes() == data:
            return
        raise ObservationQualificationError("observation_qualification_output_conflict")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        os.link(temporary, path)
    except FileExistsError:
        if not path.is_file() or path.read_bytes() != data:
            raise ObservationQualificationError(
                "observation_qualification_output_conflict"
            ) from None
    except OSError as error:
        raise ObservationQualificationError(
            "observation_qualification_output_publish_failed"
        ) from error
    finally:
        if temporary.exists():
            temporary.unlink()
    if not path.is_file() or path.read_bytes() != data:
        raise ObservationQualificationError("observation_qualification_output_publish_failed")


def reconcile_stage(args: Namespace) -> int:
    """Compose the reconciliation application service without adapter imports here."""

    from memrelay_eval.application.reconciliation_services import reconcile_stage_command

    return reconcile_stage_command(args)


def backup_terminal(args: Namespace) -> int:
    """Create one verified local second-volume generation for a terminal run."""

    from memrelay_eval.domain.ids import AttemptId, RunId
    from memrelay_eval.evidence.backup import TerminalEvidenceBackup
    from memrelay_eval.ledger import SqliteLedger

    ledger = SqliteLedger.open_control(Path(args.ledger))
    try:
        receipt = TerminalEvidenceBackup(
            artifacts_root=Path(args.artifacts_root),
            ledger=ledger,
            ledger_path=Path(args.ledger),
            backup_root=Path(args.backup_root),
        ).backup_terminal_run(run_id=RunId(args.run_id), attempt_id=AttemptId(args.attempt_id))
    finally:
        ledger.close()
    print(receipt.bytes().decode("utf-8"))
    return 0


def analyze_stage(args: Namespace) -> int:
    """Execute one frozen, SQL-free analysis request against a named Parquet version."""
    plan_bytes = Path(args.plan).read_bytes()
    plan = _canonical_analysis_plan(plan_bytes)
    if plan["stage"] != args.stage or plan["dataset_version"] != args.dataset_version:
        raise AnalysisError("analysis_plan_authority_conflict")
    query = AnalysisQuery(
        table=plan["table"],
        columns=tuple(plan["columns"]),
        equals=tuple((item["column"], item["value"]) for item in plan["equals"]),
    )
    spec = DerivationSpec(
        name=plan["derivation_name"],
        derivation_kind=plan["derivation_kind"],
        gate_ids=tuple(plan["gate_ids"]),
        parent_derivations=tuple(plan["parent_derivations"]),
        query_sha256=sha256(
            canonical_bytes(
                {
                    "columns": query.columns,
                    "equals": query.equals,
                    "table": query.table,
                }
            )
        ).hexdigest(),
    )
    with ReadOnlyDuckDbAnalysis.open(args.parquet_root, args.dataset_version) as analysis:
        publisher = DerivationPublisher(args.output_root, analysis.dataset)
        try:
            table = analysis.read(query)
            result = publisher.publish_table(table, spec)
        except AnalysisError as error:
            publisher.record_rejection(spec, error)
            raise
        command = {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "command": "analyze",
            "stage": args.stage,
            "terminal_status": "succeeded",
            "exit_code": 0,
            "dataset_version": analysis.dataset.dataset_version,
            "dataset_manifest_sha256": analysis.dataset.manifest_sha256,
            "analysis_plan_sha256": sha256(plan_bytes).hexdigest(),
            "derivation_sha256": result.derivation_sha256,
            "output_sha256": sha256(result.output_path.read_bytes()).hexdigest(),
            "protocol_sha256": analysis.dataset.manifest["protocol_sha256"],
            "runtime_lock_sha256": analysis.dataset.manifest["runtime_lock_sha256"],
        }
        command["command_sha256"] = canonical_digest(command)
        command_path = (
            Path(args.output_root) / "commands" / f"analyze-{command['command_sha256']}.json"
        )
        _write_immutable_command(command_path, canonical_bytes(command))
    print(canonical_bytes(command).decode("utf-8"))
    return 0


def reproduce_offline(args: Namespace) -> int:
    """Run a hash-sealed rebuild and compare it with retained original outputs."""
    bundle = ReproductionBundle.parse(Path(args.bundle).read_bytes())
    comparison = execute_sealed_replay(
        bundle,
        cas_root=Path(args.cas_root),
        backup_root=Path(args.backup_root) if args.backup_root else None,
    )
    path = publish_comparison(comparison, Path(args.output_root))
    print(canonical_bytes({**comparison.document(), "path": path.as_posix()}).decode("utf-8"))
    return 0 if comparison.matches else 1


def seal_reproduction_bundle_command(args: Namespace) -> int:
    """Seal retained authorities into the one standard offline replay bundle."""
    queries = _canonical_reproduction_json(Path(args.queries), "reproduction_queries_not_canonical")
    grader_result = _canonical_reproduction_json(
        Path(args.grader_result), "reproduction_grader_result_not_canonical"
    )
    normalized_evidence = _canonical_reproduction_json(
        Path(args.normalized_evidence), "reproduction_evidence_not_canonical"
    )
    if not isinstance(queries, list) or not all(isinstance(item, dict) for item in queries):
        raise AnalysisError("reproduction_queries_invalid")
    if not isinstance(grader_result, dict) or not isinstance(normalized_evidence, dict):
        raise AnalysisError("reproduction_source_invalid")
    bundle = seal_reproduction_bundle(
        dataset_root=Path(args.parquet_root),
        dataset_version=args.dataset_version,
        queries=tuple(queries),
        grader_result=grader_result,
        normalized_evidence=normalized_evidence,
        protocol_sha256=args.protocol_sha256,
        runtime_lock=Path(args.runtime_lock),
        output_root=Path(args.output_root),
        backup_receipt=Path(args.backup_receipt) if args.backup_receipt else None,
    )
    print(bundle.bytes().decode("utf-8"))
    return 0


def allocate_stochastic_rerun_command(args: Namespace) -> int:
    """Reserve distinct lineage for a governed stochastic rerun or replication."""
    identity = allocate_stochastic_rerun(
        original_protocol_id=args.original_protocol_id,
        original_run_id=args.original_run_id,
        original_attempt_id=args.original_attempt_id,
        conclusion_class=args.conclusion_class,
        output_root=Path(args.output_root),
        original_evidence_root=Path(args.original_evidence_root),
    )
    print(
        canonical_bytes(
            {**identity.document(), "output_directory": identity.output_directory.as_posix()}
        ).decode("utf-8")
    )
    return 0


def report_stage(args: Namespace) -> int:
    """Render a local report from one canonical frozen authority document."""
    input_bytes = Path(args.stage_evidence).read_bytes()
    report_input = _canonical_report_input(input_bytes)
    if report_input.stage != args.stage:
        raise AnalysisError("report_stage_authority_conflict")
    from memrelay_eval.analysis.queries import FrozenDataset

    report_input = build_stage_report_input(
        FrozenDataset.open(args.parquet_root, args.dataset_version), report_input
    )
    report = render_report(report_input)
    directory = publish_report(report, Path(args.output_root))
    command = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "command": "report",
        "stage": args.stage,
        "terminal_status": report.terminal_status,
        "exit_code": 0,
        "report_id": report_input.report_id,
        "report_sha256": report.report_sha256,
        "report_input_sha256": report_input.input_sha256,
        "protocol_sha256": report_input.scope.protocol_sha256,
        "output_directory": str(directory),
    }
    command["command_sha256"] = canonical_digest(command)
    print(canonical_bytes(command).decode("utf-8"))
    return 0


def _canonical_analysis_plan(data: bytes) -> dict[str, Any]:
    """Parse the intentionally small plan format before opening any analysis data."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AnalysisError("analysis_plan_not_canonical")
            result[key] = value
        return result

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError("analysis_plan_not_canonical") from error
    if not isinstance(document, dict) or canonical_bytes(document) != data:
        raise AnalysisError("analysis_plan_not_canonical")
    required = {
        "schema_version",
        "stage",
        "dataset_version",
        "table",
        "columns",
        "equals",
        "derivation_name",
        "derivation_kind",
        "gate_ids",
        "parent_derivations",
    }
    if set(document) != required or document["schema_version"] != ANALYSIS_SCHEMA_VERSION:
        raise AnalysisError("analysis_plan_schema_invalid")
    if (
        not isinstance(document["stage"], str)
        or not document["stage"]
        or not isinstance(document["dataset_version"], str)
        or not isinstance(document["table"], str)
        or not isinstance(document["derivation_name"], str)
        or not isinstance(document["derivation_kind"], str)
        or not all(isinstance(value, str) for value in document["columns"])
        or not all(isinstance(value, str) for value in document["gate_ids"])
        or not all(isinstance(value, str) for value in document["parent_derivations"])
        or not isinstance(document["equals"], list)
    ):
        raise AnalysisError("analysis_plan_schema_invalid")
    for value in document["equals"]:
        if (
            not isinstance(value, dict)
            or set(value) != {"column", "value"}
            or not isinstance(value["column"], str)
            or not isinstance(value["value"], str)
        ):
            raise AnalysisError("analysis_plan_schema_invalid")
    return document


def _canonical_reproduction_json(path: Path, code: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AnalysisError(code)
            result[key] = value
        return result

    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError(code) from error
    if canonical_bytes(value) != payload:
        raise AnalysisError(code)
    return value


def _claim_decision(document: object) -> ClaimGateDecision:
    if not isinstance(document, dict):
        raise AnalysisError("report_claim_decision_invalid")
    required = {
        "schema_version",
        "artifact_type",
        "endpoint_id",
        "claim_type",
        "claim_id",
        "status",
        "gate_trace",
        "source_sha256",
        "derivation_sha256",
        "protocol_sha256",
        "family_sha256",
        "sealed_claim_protocol_sha256",
        "threshold_sha256",
        "power_sha256",
        "power_evaluation_sha256",
        "information_sha256",
        "panel_gate_sha256",
        "categorical_policy_sha256",
        "categorical_gate_decision_sha256",
    }
    if (
        set(document) != required
        or document["schema_version"] != "1.0.0"
        or document["artifact_type"] != "claim_gate_decision"
    ):
        raise AnalysisError("report_claim_decision_invalid")
    try:
        return ClaimGateDecision(
            endpoint_id=document["endpoint_id"],
            claim_type=document["claim_type"],
            claim_id=document["claim_id"],
            status=document["status"],
            gate_trace=tuple(document["gate_trace"]),
            source_sha256=document["source_sha256"],
            derivation_sha256=document["derivation_sha256"],
            protocol_sha256=document["protocol_sha256"],
            family_sha256=document["family_sha256"],
            sealed_claim_protocol_sha256=document["sealed_claim_protocol_sha256"],
            threshold_sha256=document["threshold_sha256"],
            power_sha256=document["power_sha256"],
            power_evaluation_sha256=document["power_evaluation_sha256"],
            information_sha256=document["information_sha256"],
            panel_gate_sha256=document["panel_gate_sha256"],
            categorical_policy_sha256=document["categorical_policy_sha256"],
            categorical_gate_decision_sha256=document["categorical_gate_decision_sha256"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AnalysisError("report_claim_decision_invalid") from error


def _canonical_report_input(data: bytes) -> ReportInput:
    """Replay a sealed report input and reject any altered release conclusion."""
    from memrelay_eval.analysis.gates import CategoricalGateDecision, CategoricalGatePolicy
    from memrelay_eval.analysis.intervals import SimultaneousInterval
    from memrelay_eval.analysis.multiplicity import FrozenClaimFamily
    from memrelay_eval.analysis.reports import ReportItem, SourceAuthority, StageScope

    document = _canonical_json_document(data, "report_input_not_canonical")
    if document.get("artifact_type") != "frozen_report_input":
        raise AnalysisError("report_input_schema_invalid")

    def claim_scope(scope_document: object) -> ClaimScope:
        if not isinstance(scope_document, dict):
            raise AnalysisError("report_scope_schema_invalid")
        normalized = dict(scope_document)
        normalized["source_sha256"] = tuple(normalized["source_sha256"])
        normalized["evidence_ids"] = tuple(normalized["evidence_ids"])
        return ClaimScope(**normalized)

    try:
        scope_document = dict(document["scope"])
        scope_document["source_sha256"] = tuple(scope_document["source_sha256"])
        scope_document["evidence_ids"] = tuple(scope_document["evidence_ids"])
        scope = StageScope(**scope_document)
        family_document = dict(document["family"])
        family_document.pop("schema_version")
        family_document.pop("artifact_type")
        family = FrozenClaimFamily(**family_document)
        decisions = tuple(_claim_decision(item) for item in document["claim_decisions"])
        claim_scopes = tuple(claim_scope(item) for item in document["claim_scopes"])
        intervals = tuple(SimultaneousInterval(**item) for item in document["non_target_intervals"])
        policy_document = dict(document["categorical_policy"])
        policy_document.pop("schema_version")
        policy = CategoricalGatePolicy(**policy_document)
        categorical = tuple(
            CategoricalGateDecision(
                scope_id=item["scope_id"],
                status=item["status"],
                blocking_event_ids=tuple(item["blocking_event_ids"]),
                affected_claim_ids=tuple(item["affected_claim_ids"]),
                policy_sha256=item["policy_sha256"],
                evidence_sha256=tuple(item["evidence_sha256"]),
                bounded_language_required=item["bounded_language_required"],
            )
            for item in document["categorical_decisions"]
        )
        source_document = dict(document["source_authority"])
        source_document.pop("schema_version")
        source_document.pop("artifact_type")
        source_authority = SourceAuthority(
            source_kind=source_document["source_kind"],
            dataset_manifest_sha256=source_document["dataset_manifest_sha256"],
            protocol_sha256=source_document["protocol_sha256"],
            source_sha256=tuple(source_document["source_sha256"]),
            authority_sha256=source_document["authority_sha256"],
        )
        sections = {
            name: tuple(
                ReportItem(
                    item_id=item["item_id"],
                    scope=claim_scope(item["scope"]),
                    value=item["value"],
                )
                for item in values
            )
            for name, values in document["sections"].items()
        }
        result = ReportInput(
            report_id=document["report_id"],
            stage=document["stage"],
            scope=scope,
            dataset_manifest_sha256=document["dataset_manifest_sha256"],
            table_sha256=tuple(document["table_sha256"]),
            figure_sha256=tuple(document["figure_sha256"]),
            estimator_sha256=document["estimator_sha256"],
            interval_sha256=tuple(document["interval_sha256"]),
            power_sha256=document["power_sha256"],
            safety_sha256=document["safety_sha256"],
            panel_sha256=document["panel_sha256"],
            cost_revision_sha256=document["cost_revision_sha256"],
            runtime_lock_sha256=document["runtime_lock_sha256"],
            template_sha256=document["template_sha256"],
            gate_ids=tuple(document["gate_ids"]),
            family=family,
            claim_decisions=decisions,
            claim_scopes=claim_scopes,
            non_target_intervals=intervals,
            categorical_policy=policy,
            categorical_decisions=categorical,
            source_authority=source_authority,
            reproduction_status=document["reproduction_status"],
            sections=sections,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AnalysisError("report_input_schema_invalid") from error
    if result.to_document() != document:
        raise AnalysisError("report_input_authority_conflict")
    return result


def _canonical_json_document(data: bytes, code: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AnalysisError(code)
            result[key] = value
        return result

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisError(code) from error
    if not isinstance(document, dict) or canonical_bytes(document) != data:
        raise AnalysisError(code)
    return document


def _write_immutable_command(path: Path, data: bytes) -> None:
    """Create a command manifest exactly once without modifying prior evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            raise AnalysisError("analysis_command_manifest_conflict")
        return
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    except OSError as error:
        if path.is_file() and path.read_bytes() == data:
            return
        raise AnalysisError("analysis_command_manifest_publish_failed") from error
    finally:
        if temporary.exists():
            temporary.unlink()
