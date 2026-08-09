from __future__ import annotations

import json
import hashlib
import math
import os
import re
import threading
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

from .modeling import validate_generated_python
from .models import JobState
from .workspace import WorkspaceStore
from .discovery import preferred_aedt_version
from .aedt_runtime import (
    aedt_license_preflight,
    describe_aedt_exception,
    ensure_strict_existing_attachment,
    prepare_pyaedt_environment,
    temporary_multi_desktop,
)
from .review import ArtifactReviewService


_AEDT_BUILD_LOCK = threading.Lock()
_JOB_BUILD_LOCKS_GUARD = threading.Lock()
_JOB_BUILD_LOCKS: dict[str, threading.Lock] = {}


@contextmanager
def _exclusive_hfss_build(job_id: str) -> Iterator[None]:
    """Fail fast when this process already owns the job or AEDT build slot."""
    with _JOB_BUILD_LOCKS_GUARD:
        job_lock = _JOB_BUILD_LOCKS.setdefault(job_id, threading.Lock())
    if not job_lock.acquire(blocking=False):
        raise RuntimeError(f"an HFSS build is already running for job {job_id}")
    try:
        if not _AEDT_BUILD_LOCK.acquire(blocking=False):
            raise RuntimeError("another HFSS build is already running in this MCP process")
        try:
            yield
        finally:
            _AEDT_BUILD_LOCK.release()
    finally:
        job_lock.release()


class HfssBuildService:
    def __init__(self, store: WorkspaceStore, hfss_factory: Callable[..., Any] | None = None) -> None:
        self.store = store
        self.hfss_factory = hfss_factory

    def build(
        self,
        job_id: str,
        project_name: str = "antenna.aedt",
        approval_hash: str | None = None,
        session_mode: str = "new",
        grpc_port: int | None = None,
    ) -> JobState:
        with _exclusive_hfss_build(job_id):
            return self._build(
                job_id,
                project_name=project_name,
                approval_hash=approval_hash,
                session_mode=session_mode,
                grpc_port=grpc_port,
            )

    def _build(
        self,
        job_id: str,
        project_name: str = "antenna.aedt",
        approval_hash: str | None = None,
        session_mode: str = "new",
        grpc_port: int | None = None,
    ) -> JobState:
        if os.getenv("ANTENNA_MCP_ALLOW_SIMULATION") != "1":
            raise PermissionError("Set ANTENNA_MCP_ALLOW_SIMULATION=1 to allow local HFSS execution")
        if Path(project_name).name != project_name or not project_name.lower().endswith(".aedt"):
            raise ValueError("project_name must be a plain .aedt filename")
        if session_mode not in {"new", "existing"}:
            raise ValueError("session_mode must be 'new' or 'existing'")
        if session_mode == "new" and grpc_port is not None:
            raise ValueError("grpc_port is only valid for session_mode='existing'")
        if session_mode == "existing" and grpc_port is None:
            raise ValueError("grpc_port is required for strict existing-session attachment")
        if grpc_port is not None and not 1 <= grpc_port <= 65535:
            raise ValueError("grpc_port must be between 1 and 65535")
        if approval_hash is None:
            raise PermissionError("prepare and approve the generated artifact review packet before building")
        packet = ArtifactReviewService(self.store).verify(job_id, approval_hash)
        state = self.store.load_state(job_id)
        if state.kind != "modeling" or state.status not in {"completed", "failed"}:
            raise ValueError("a completed modeling job is required")
        for required in ("parameters", "model_3d", "boolean"):
            if required not in state.artifacts:
                raise ValueError(f"missing required artifact: {required}")
        snapshot = self._snapshot_reviewed_artifacts(packet, state)
        self._validate_reviewed_contract(state, snapshot)
        fragments: dict[str, str] = {}
        for stage in ("model_3d", "model_2d", "boolean", "simulation_setup"):
            if stage in state.artifacts:
                try:
                    source = snapshot[stage].decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"{stage}.py must be valid UTF-8") from exc
                validate_generated_python(source)
                fragments[stage] = source

        output = self.store.job_dir(job_id) / project_name
        if output.exists():
            raise FileExistsError(
                f"refusing to overwrite an existing HFSS project; choose a new project_name: {output}"
            )
        state.status = "running"
        state.current_stage = "hfss_build"
        state.error = None
        self.store.save_state(state)
        factory = self.hfss_factory or _default_hfss_factory
        hfss = None
        try:
            hfss = factory(
                project=str(output),
                design="HFSSDesign1",
                solution_type=self._solution_type(state, snapshot),
                session_mode=session_mode,
                grpc_port=grpc_port,
            )
            if getattr(hfss, "odesign", None) is None:
                raise RuntimeError("AEDT session has no active HFSS design")
            if list(hfss.modeler.object_names):
                raise RuntimeError("the dedicated HFSS design is not empty; refusing to merge generated geometry")
            self._apply_parameters(hfss, snapshot["parameters"])
            for stage in ("model_3d", "model_2d"):
                if stage in fragments:
                    _execute_fragment(fragments[stage], hfss)
            initial_report = self._verify_initial_geometry(hfss, snapshot)
            if not initial_report["passed"]:
                failures = [item["name"] for item in initial_report["checks"] if not item["passed"]]
                raise RuntimeError(f"HFSS initial geometry verification failed: {', '.join(failures)}")
            _execute_fragment(fragments["boolean"], hfss)
            final_report = self._verify_geometry(hfss, snapshot)
            if "simulation_setup" in fragments:
                _execute_fragment(fragments["simulation_setup"], hfss)
            build_report = {
                "passed": initial_report["passed"] and final_report["passed"],
                "initial_geometry": initial_report,
                "final_geometry": final_report,
                "checks": [*initial_report["checks"], *final_report["checks"]],
            }
            if not build_report["passed"]:
                failures = [item["name"] for item in build_report["checks"] if not item["passed"]]
                raise RuntimeError(f"HFSS geometry verification failed: {', '.join(failures)}")
            if not hfss.save_project(str(output)):
                raise RuntimeError("HFSS failed to save the reviewed project")
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError("HFSS reported success but the saved project is missing or empty")
            report_path = self.store.write_artifact(
                job_id,
                "hfss_build_report.json",
                json.dumps(build_report, ensure_ascii=False, indent=2) + "\n",
            )
            state.artifacts["hfss_build_report"] = str(report_path)
            state.artifacts["hfss_project"] = str(output)
            state.status = "completed"
            state.current_stage = "complete"
            state.error = None
        except Exception as exc:
            state.status = "failed"
            state.error = describe_aedt_exception(exc, [output.parent])
        finally:
            if hfss is not None:
                close = session_mode == "new"
                try:
                    hfss.release_desktop(close_projects=close, close_desktop=close)
                except Exception as exc:
                    if state.status == "completed":
                        state.status = "failed"
                        state.error = f"AEDT release failed after build: {type(exc).__name__}: {exc}"
            self.store.save_state(state)
        return state

    @staticmethod
    def _snapshot_reviewed_artifacts(packet: dict[str, Any], state: JobState) -> dict[str, bytes]:
        snapshot: dict[str, bytes] = {}
        for entry in packet.get("artifacts", []):
            stage = str(entry.get("stage") or "")
            if not stage or stage in snapshot:
                raise PermissionError("artifact review packet contains a missing or duplicate stage")
            path = Path(str(entry.get("path") or "")).expanduser().resolve()
            current_path = state.artifacts.get(stage)
            if not current_path or Path(current_path).expanduser().resolve() != path:
                raise PermissionError(f"artifact review packet path is stale for stage {stage}")
            data = path.read_bytes()
            if len(data) != entry.get("size") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                raise PermissionError(f"artifact {stage} changed while preparing its immutable build snapshot")
            snapshot[stage] = data
        return snapshot

    @staticmethod
    def _apply_parameters(hfss: Any, artifact: bytes) -> None:
        payload = json.loads(artifact.decode("utf-8"))
        parameters = payload.get("parameters")
        if not isinstance(parameters, list):
            raise ValueError("parameters.json must contain a parameters array")
        seen: set[str] = set()
        for parameter in parameters:
            name = parameter["name"]
            if name in seen:
                raise ValueError(f"duplicate HFSS parameter: {name}")
            seen.add(name)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"invalid HFSS parameter name: {name!r}")
            expression = parameter.get("expression")
            if expression is not None:
                if not isinstance(expression, str) or not expression.strip():
                    raise ValueError(f"parameter {name} has an invalid expression")
                hfss[name] = expression
                continue
            value = parameter.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"parameter {name} must have a finite numeric value")
            unit = parameter.get("unit", "")
            if not isinstance(unit, str) or not re.fullmatch(r"[A-Za-z0-9_./^-]*", unit):
                raise ValueError(f"parameter {name} has an invalid unit")
            hfss[name] = f"{value}{unit}"

    @staticmethod
    def _validate_reviewed_contract(state: JobState, snapshot: dict[str, bytes] | None = None) -> None:
        if snapshot is None:
            snapshot = {
                stage: Path(path).read_bytes()
                for stage, path in state.artifacts.items()
                if stage != "review_packet" and Path(path).is_file()
            }

        def payload(stage: str, *, required: bool = False) -> dict[str, Any] | None:
            raw = snapshot.get(stage)
            if raw is None:
                if required:
                    raise ValueError(f"missing required reviewed artifact: {stage}")
                return None
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"{stage}.json must contain an object")
            return value

        parameter_payload = payload("parameters", required=True)
        raw_parameters = parameter_payload.get("parameters")
        if not isinstance(raw_parameters, list) or not raw_parameters:
            raise ValueError("parameters.json must contain a non-empty parameters array")
        parameters: dict[str, dict[str, Any]] = {}
        for item in raw_parameters:
            if not isinstance(item, dict):
                raise ValueError("every parameters.json entry must be an object")
            name = str(item.get("name") or "")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in parameters:
                raise ValueError(f"invalid or duplicate parameter name: {name!r}")
            parameters[name] = item

        source = payload("source_analysis_approved")
        unresolved: dict[str, dict[str, Any]] = {}
        if source:
            for item in source.get("parameters", []):
                binding = item.get("semantic_binding") or {}
                if item.get("value") is None and binding.get("mode") == "unresolved":
                    unresolved[str(item.get("symbol") or "")] = item

        assumptions = payload("engineering_assumptions_approved")
        receipt = payload("engineering_assumptions_receipt")
        decision_map: dict[str, dict[str, Any]] = {}
        if unresolved and (not assumptions or not receipt):
            raise PermissionError(
                f"unresolved source parameters require approved engineering assumptions: {sorted(unresolved)}"
            )
        if assumptions or receipt:
            if not assumptions or not receipt or not source:
                raise PermissionError("engineering assumptions, receipt, and approved source must be present together")
            assumption_bytes = snapshot["engineering_assumptions_approved"]
            if receipt.get("sha256") != hashlib.sha256(assumption_bytes).hexdigest():
                raise PermissionError("engineering assumptions no longer match their confirmation receipt")
            if receipt.get("approval_method") != "content_hash_round_trip":
                raise PermissionError("engineering assumption receipt lacks content-hash approval")
            source_digest = hashlib.sha256(snapshot["source_analysis_approved"]).hexdigest()
            if (assumptions.get("base_source") or {}).get("sha256") != source_digest:
                raise PermissionError("approved engineering assumptions no longer match the source artifact")
            if receipt.get("base_source_sha256") != source_digest:
                raise PermissionError("engineering assumption receipt no longer matches the source artifact")
            HfssBuildService._validate_engineering_assumption_approval_chain(
                state,
                snapshot,
                assumptions,
                receipt,
                source_digest,
            )
            decisions = assumptions.get("decisions")
            if not isinstance(decisions, list):
                raise ValueError("engineering assumptions must contain a decisions array")
            decision_map = {str(item.get("symbol") or ""): item for item in decisions}
            if set(decision_map) != set(unresolved) or len(decision_map) != len(decisions):
                raise PermissionError("engineering decisions must match unresolved source parameters exactly")
            for symbol, decision in decision_map.items():
                value = decision.get("value")
                expected_unit = str(unresolved[symbol].get("unit") or "")
                if (
                    decision.get("classification") != "engineering_assumption"
                    or decision.get("paper_evidence") is not False
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or decision.get("unit") != expected_unit
                ):
                    raise PermissionError(f"invalid approved engineering decision for {symbol}")
                parameter = parameters.get(symbol)
                if not parameter:
                    raise ValueError(f"parameters.json omits approved engineering assumption {symbol}")
                if parameter.get("value") != value or parameter.get("unit") != expected_unit:
                    raise PermissionError(f"parameter {symbol} differs from the approved engineering assumption")
                provenance = parameter.get("provenance") or {}
                if provenance.get("kind") != "engineering_assumption" or provenance.get(
                    "assumption_id"
                ) != decision.get("assumption_id"):
                    raise PermissionError(f"parameter {symbol} is missing engineering-assumption provenance")
                binding = unresolved[symbol].get("semantic_binding") or {}
                source_claim = decision.get("source_claim") or {}
                if (
                    source_claim.get("claim_id") != binding.get("claim_id")
                    or source_claim.get("original_value") is not None
                    or source_claim.get("original_evidence_mode") != "unresolved"
                ):
                    raise PermissionError(f"engineering decision {symbol} no longer maps to its source claim")

        manifest = payload("geometry_manifest")
        validation = payload("geometry_validation")
        if validation and not manifest:
            raise ValueError("geometry_validation requires a geometry_manifest")
        if manifest:
            for stage in ("geometry_validation", "materials", "solids", "dimensions"):
                if stage not in snapshot:
                    raise ValueError(f"deterministic geometry build is missing {stage}")
            if manifest.get("profile") != "leam_case3":
                raise ValueError("unsupported deterministic geometry manifest profile")
            expected_initial = [
                "substrate",
                "radiator",
                "feedline",
                "left_ground",
                "right_ground",
                "horizontal_slot",
                "vertical_slot",
            ]
            expected_final = ["substrate", "radiator", "left_ground", "right_ground"]
            expected_operations = [
                {"order": 1, "operation": "unite", "target": "radiator", "operands": ["feedline"]},
                {
                    "order": 2,
                    "operation": "unite",
                    "target": "horizontal_slot",
                    "operands": ["vertical_slot"],
                },
                {
                    "order": 3,
                    "operation": "subtract",
                    "target": "radiator",
                    "operands": ["horizontal_slot"],
                },
            ]
            if manifest.get("initial_objects") != expected_initial:
                raise ValueError("LEAM Case 3 manifest has an invalid initial object contract")
            if manifest.get("final_objects") != expected_final:
                raise ValueError("LEAM Case 3 manifest has an invalid final object contract")
            if manifest.get("operations") != expected_operations:
                raise ValueError("LEAM Case 3 manifest has an invalid boolean contract")

            expected_checks = {
                "seven_source_components",
                "SL_relation",
                "RPW_relation",
                "positive_ground_length",
                "positive_copper_thickness",
                "radiator_x_inside_board",
                "radiator_y_inside_board",
                "horizontal_slot_inside_radiator",
                "vertical_slot_inside_radiator",
                "CuT_has_engineering_provenance",
            }
            check_items = validation.get("checks") if validation else None
            if (
                validation.get("passed") is not True
                or not isinstance(check_items, list)
                or {item.get("name") for item in check_items} != expected_checks
                or any(item.get("passed") is not True for item in check_items)
                or validation.get("expected_initial_object_count") != 7
                or validation.get("expected_final_object_count") != 4
                or validation.get("expected_boolean_operation_count") != 3
            ):
                raise PermissionError("the deterministic geometry validation contract did not pass")

            expected_parameter_names = {
                "DPR",
                "SW",
                "SLT",
                "SLV",
                "SLH",
                "ML",
                "RPL",
                "MW",
                "MG",
                "SubT",
                "eps_r",
                "tan_delta",
                "CuT",
                "SL",
                "RPW",
                "ground_length",
            }
            if set(parameters) != expected_parameter_names:
                raise ValueError("LEAM Case 3 parameters do not match the deterministic contract")
            if not source:
                raise ValueError("LEAM Case 3 deterministic build requires source_analysis_approved")
            HfssBuildService._validate_case3_source_parameter_mapping(
                source,
                parameters,
                decision_map,
            )
            expected_expressions = {
                "SL": "ML+DPR+0.2mm",
                "RPW": "(SW-MW-2*MG)/2",
                "ground_length": "ML-RPL",
            }
            if any(parameters[name].get("expression") != expression for name, expression in expected_expressions.items()):
                raise ValueError("LEAM Case 3 derived parameter expressions were changed")
            HfssBuildService._recompute_case3_parameter_geometry(parameters)

            materials = payload("materials", required=True).get("materials")
            if not isinstance(materials, list):
                raise ValueError("materials.json must contain a materials array")
            material_map = {item.get("name"): item for item in materials}
            fr4 = material_map.get("LEAM_FR4") or {}
            if set(material_map) != {"LEAM_FR4", "copper", "vacuum"} or not _numeric_close(
                fr4.get("relative_permittivity"), parameters["eps_r"].get("value")
            ) or not _numeric_close(
                fr4.get("dielectric_loss_tangent"), parameters["tan_delta"].get("value")
            ):
                raise ValueError("LEAM Case 3 material contract is incomplete or inconsistent")

            solids = payload("solids", required=True).get("solids")
            expected_solids = {
                "substrate": ("box", "LEAM_FR4"),
                "radiator": ("cylinder", "copper"),
                "feedline": ("box", "copper"),
                "left_ground": ("box", "copper"),
                "right_ground": ("box", "copper"),
                "horizontal_slot": ("box", "vacuum"),
                "vertical_slot": ("box", "vacuum"),
            }
            solid_map = {
                str(item.get("name") or ""): (item.get("primitive"), item.get("material"))
                for item in solids or []
            }
            if solid_map != expected_solids:
                raise ValueError("LEAM Case 3 solids contract is incomplete or inconsistent")
            dimensions = payload("dimensions", required=True).get("dimensions")
            if {str(item.get("name") or "") for item in dimensions or []} != set(expected_solids):
                raise ValueError("LEAM Case 3 dimensions contract is incomplete or inconsistent")

    @staticmethod
    def _validate_case3_source_parameter_mapping(
        source: dict[str, Any],
        parameters: dict[str, dict[str, Any]],
        decisions: dict[str, dict[str, Any]],
    ) -> None:
        raw_source_parameters = source.get("parameters")
        if not isinstance(raw_source_parameters, list):
            raise ValueError("approved source must contain a parameters array")

        source_parameters: dict[str, dict[str, Any]] = {}
        for item in raw_source_parameters:
            if not isinstance(item, dict):
                raise ValueError("every approved source parameter must be an object")
            symbol = str(item.get("symbol") or "")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol) or symbol in source_parameters:
                raise ValueError(f"approved source contains an invalid or duplicate parameter: {symbol!r}")
            source_parameters[symbol] = item

        expected_source_claims = {
            "DPR": "case3-DPR",
            "SW": "case3-SW",
            "SLT": "case3-SLT",
            "SLV": "case3-SLV",
            "SLH": "case3-SLH",
            "ML": "case3-ML",
            "RPL": "case3-RPL",
            "MW": "case3-MW",
            "MG": "case3-MG",
            "SL": "case3-SL",
            "RPW": "case3-RPW",
            "SubT": "case3-SubT",
            "eps_r": "case3-eps-r",
            "tan_delta": "case3-tan-delta",
            "CuT": "case3-CuT",
        }
        if set(source_parameters) != set(expected_source_claims):
            raise PermissionError("approved source parameters do not match the LEAM Case 3 evidence contract")

        relation_claims = {
            str(item.get("claim_id") or "")
            for item in source.get("derived_relations", [])
            if isinstance(item, dict)
        }
        expected_relation_claims = {
            "case3-relation-SL",
            "case3-relation-RPW",
            "case3-relation-ground-length",
        }
        if relation_claims != expected_relation_claims:
            raise PermissionError("approved source derived relations do not match the LEAM Case 3 contract")

        derived_parameters = {
            "SL": "case3-relation-SL",
            "RPW": "case3-relation-RPW",
        }
        for symbol, expected_claim_id in expected_source_claims.items():
            source_parameter = source_parameters[symbol]
            parameter = parameters.get(symbol)
            if parameter is None:
                raise ValueError(f"parameters.json omits approved source parameter {symbol}")
            binding = source_parameter.get("semantic_binding")
            if not isinstance(binding, dict):
                raise PermissionError(f"approved source parameter {symbol} has no semantic binding")
            mode = binding.get("mode")
            claim_id = binding.get("claim_id")
            if claim_id != expected_claim_id or mode not in {"visual", "text", "unresolved"}:
                raise PermissionError(f"approved source parameter {symbol} has an invalid evidence claim")

            source_unit = source_parameter.get("unit")
            if not isinstance(source_unit, str) or parameter.get("unit") != source_unit:
                raise PermissionError(f"parameter {symbol} unit differs from the approved source")
            provenance = parameter.get("provenance")
            if not isinstance(provenance, dict):
                raise PermissionError(f"parameter {symbol} is missing provenance")

            source_value = source_parameter.get("value")
            if symbol == "CuT":
                if source_value is not None or mode != "unresolved":
                    raise PermissionError("CuT must remain null/unresolved in the approved source")
                decision = decisions.get(symbol)
                if not decision:
                    raise PermissionError("CuT has no approved engineering decision")
                if (
                    provenance.get("kind") != "engineering_assumption"
                    or provenance.get("assumption_id") != decision.get("assumption_id")
                    or provenance.get("paper_evidence") is not False
                ):
                    raise PermissionError("parameter CuT is not mapped to its approved engineering assumption")
                continue

            if (
                isinstance(source_value, bool)
                or not isinstance(source_value, (int, float))
                or not math.isfinite(source_value)
                or not _numeric_close(parameter.get("value"), source_value)
            ):
                raise PermissionError(f"parameter {symbol} value differs from the approved source")

            relation_claim = derived_parameters.get(symbol)
            if relation_claim:
                if provenance.get("kind") != "derived_relation" or provenance.get("claim_id") != relation_claim:
                    raise PermissionError(f"parameter {symbol} is not mapped to its approved derived relation")
            elif (
                provenance.get("kind") != "source_evidence"
                or provenance.get("claim_id") != claim_id
                or provenance.get("evidence_mode") != mode
            ):
                raise PermissionError(f"parameter {symbol} provenance differs from the approved source claim")

        ground = parameters.get("ground_length") or {}
        ground_provenance = ground.get("provenance") or {}
        if (
            ground.get("unit") != "mm"
            or ground_provenance.get("kind") != "derived_relation"
            or ground_provenance.get("claim_id") != "case3-relation-ground-length"
        ):
            raise PermissionError("ground_length is not mapped to its approved derived relation")

    @staticmethod
    def _validate_engineering_assumption_approval_chain(
        state: JobState,
        snapshot: dict[str, bytes],
        assumptions: dict[str, Any],
        receipt: dict[str, Any],
        source_digest: str,
    ) -> None:
        required = {
            "source_analysis_candidate",
            "source_refinement_report",
            "source_review_packet",
            "engineering_assumptions_candidate",
            "engineering_assumption_review_packet",
        }
        missing = required - snapshot.keys()
        if missing:
            raise PermissionError(
                "engineering assumption approval chain is incomplete: " + ", ".join(sorted(missing))
            )

        approved_assumption = snapshot["engineering_assumptions_approved"]
        candidate_assumption = snapshot["engineering_assumptions_candidate"]
        if approved_assumption != candidate_assumption:
            raise PermissionError(
                "approved engineering assumptions no longer match the hash-approved candidate"
            )
        if assumptions.get("job_id") != state.job_id or receipt.get("job_id") != state.job_id:
            raise PermissionError("engineering assumption approval chain names a different job")
        if receipt.get("artifact") != "engineering_assumptions_approved.json":
            raise PermissionError("engineering assumption receipt names a different approved artifact")

        source_packet = json.loads(snapshot["source_review_packet"].decode("utf-8"))
        source_entries = source_packet.get("artifacts")
        source_entry_stages = {
            "candidate": "source_analysis_candidate",
            "report": "source_refinement_report",
        }
        for state_stage, packet_name in (
            ("source_visual_audit", "visual_audit"),
            ("source_visual_verdict", "visual_verdict"),
        ):
            if state_stage in state.artifacts:
                source_entry_stages[packet_name] = state_stage
        visual_input_stages = sorted(
            stage for stage in state.artifacts if stage.startswith("source_visual_input_")
        )
        for index, stage in enumerate(visual_input_stages, start=1):
            source_entry_stages[f"visual_input_{index}"] = stage
        HfssBuildService._validate_review_entries(
            source_entries,
            source_entry_stages,
            snapshot,
            state,
        )
        source_canonical = json.dumps(
            source_entries,
            sort_keys=True,
            separators=(",", ":"),
        )
        source_review_hash = hashlib.sha256(source_canonical.encode()).hexdigest()
        if source_packet.get("approval_hash") != source_review_hash:
            raise PermissionError("source review packet approval hash is stale")
        if snapshot["source_analysis_approved"] != snapshot["source_analysis_candidate"]:
            raise PermissionError("approved source no longer matches its reviewed candidate")

        assumption_packet = json.loads(
            snapshot["engineering_assumption_review_packet"].decode("utf-8")
        )
        assumption_entries = assumption_packet.get("artifacts")
        assumption_entry_stages = {
            "candidate": "engineering_assumptions_candidate",
            "approved_source": "source_analysis_approved",
        }
        HfssBuildService._validate_review_entries(
            assumption_entries,
            assumption_entry_stages,
            snapshot,
            state,
        )
        if assumption_packet.get("source_review_approval_hash") != source_review_hash:
            raise PermissionError("engineering assumption review packet no longer matches the source review")
        assumption_canonical = json.dumps(
            {
                "artifacts": assumption_entries,
                "source_review_approval_hash": source_review_hash,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        assumption_review_hash = hashlib.sha256(assumption_canonical.encode("utf-8")).hexdigest()
        if assumption_packet.get("approval_hash") != assumption_review_hash:
            raise PermissionError("engineering assumption review packet approval hash is stale")

        base_source = assumptions.get("base_source") or {}
        if (
            base_source.get("artifact") != "source_analysis_approved.json"
            or base_source.get("sha256") != source_digest
            or base_source.get("source_review_approval_hash") != source_review_hash
            or receipt.get("base_source_sha256") != source_digest
            or receipt.get("source_review_approval_hash") != source_review_hash
            or receipt.get("assumption_approval_hash") != assumption_review_hash
        ):
            raise PermissionError("engineering assumption approval chain no longer matches its source or review")
        decision_symbols = [item.get("symbol") for item in assumptions.get("decisions", [])]
        if receipt.get("decision_symbols") != decision_symbols:
            raise PermissionError("engineering assumption receipt decision list is stale")

    @staticmethod
    def _validate_review_entries(
        entries: Any,
        expected_stages: dict[str, str],
        snapshot: dict[str, bytes],
        state: JobState,
    ) -> None:
        if not isinstance(entries, list) or len(entries) != len(expected_stages):
            raise PermissionError("review packet artifact set is incomplete or contaminated")
        names = [str(item.get("name") or "") for item in entries if isinstance(item, dict)]
        if names != list(expected_stages) or len(set(names)) != len(names):
            raise PermissionError("review packet artifact set is incomplete or contaminated")
        for entry in entries:
            name = str(entry["name"])
            stage = expected_stages[name]
            data = snapshot.get(stage)
            if data is None:
                raise PermissionError(f"reviewed artifact is missing from the immutable snapshot: {stage}")
            state_path = state.artifacts.get(stage)
            if (
                not state_path
                or Path(str(entry.get("path") or "")).expanduser().resolve()
                != Path(state_path).expanduser().resolve()
                or entry.get("size") != len(data)
                or entry.get("sha256") != hashlib.sha256(data).hexdigest()
            ):
                raise PermissionError(f"review packet entry no longer matches artifact {stage}")

    @staticmethod
    def _recompute_case3_parameter_geometry(parameters: dict[str, dict[str, Any]]) -> None:
        expected_units = {
            "DPR": "mm",
            "SW": "mm",
            "SLT": "mm",
            "SLV": "mm",
            "SLH": "mm",
            "ML": "mm",
            "RPL": "mm",
            "MW": "mm",
            "MG": "mm",
            "SL": "mm",
            "RPW": "mm",
            "SubT": "mm",
            "eps_r": "",
            "tan_delta": "",
            "CuT": "mm",
            "ground_length": "mm",
        }
        values: dict[str, float] = {}
        for name, expected_unit in expected_units.items():
            parameter = parameters.get(name)
            if not parameter:
                raise ValueError(f"LEAM Case 3 is missing parameter {name}")
            value = parameter.get("value")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or parameter.get("unit") != expected_unit
            ):
                raise ValueError(f"LEAM Case 3 parameter {name} has an invalid value or unit")
            values[name] = float(value)

        p = values
        horizontal_corner_radius = math.hypot(max(p["SLH"], p["SLT"]) / 2, min(p["SLH"], p["SLT"]) / 2)
        vertical_corner_radius = math.hypot(max(p["SLV"], p["SLT"]) / 2, min(p["SLV"], p["SLT"]) / 2)
        checks = {
            "SL_relation": _numeric_close(p["SL"], p["ML"] + p["DPR"] + 0.2),
            "RPW_relation": _numeric_close(p["RPW"], (p["SW"] - p["MW"] - 2 * p["MG"]) / 2),
            "ground_length_relation": _numeric_close(p["ground_length"], p["ML"] - p["RPL"]),
            "positive_planar_dimensions": all(
                p[name] > 0
                for name in ("DPR", "SW", "SLT", "SLV", "SLH", "ML", "RPL", "MW", "MG", "SL", "RPW")
            ),
            "positive_material_properties": p["eps_r"] > 0 and 0 <= p["tan_delta"] < 1,
            "positive_ground_length": p["ground_length"] > 0,
            "positive_copper_thickness": 0 < p["CuT"] < p["SubT"],
            "radiator_x_inside_board": p["SW"] / 2 - p["DPR"] >= 0
            and p["SW"] / 2 + p["DPR"] <= p["SW"],
            "radiator_y_inside_board": p["ML"] - p["DPR"] >= 0
            and p["ML"] + p["DPR"] <= p["SL"],
            "feed_and_grounds_inside_board": p["MW"] + 2 * p["MG"] <= p["SW"]
            and p["RPW"] <= p["SW"]
            and p["ground_length"] <= p["SL"],
            "horizontal_slot_inside_radiator": horizontal_corner_radius < p["DPR"],
            "vertical_slot_inside_radiator": vertical_corner_radius < p["DPR"],
        }
        failures = [name for name, passed in checks.items() if not passed]
        if failures:
            raise PermissionError(
                "recomputed LEAM Case 3 geometry contract failed: " + ", ".join(failures)
            )

    @staticmethod
    def _verify_initial_geometry(hfss: Any, snapshot: dict[str, bytes]) -> dict[str, Any]:
        raw = snapshot.get("geometry_manifest")
        if raw is None:
            return {"passed": True, "checks": []}
        manifest = json.loads(raw.decode("utf-8"))
        expected = set(manifest.get("initial_objects") or [])
        actual = set(hfss.modeler.object_names)
        checks = [
            {
                "name": "initial_object_names",
                "passed": bool(expected) and actual == expected,
                "detail": {"expected": sorted(expected), "actual": sorted(actual)},
            }
        ]
        expected_materials = {
            "substrate": "LEAM_FR4",
            "radiator": "copper",
            "feedline": "copper",
            "left_ground": "copper",
            "right_ground": "copper",
            "horizontal_slot": "vacuum",
            "vertical_slot": "vacuum",
        }
        actual_materials = {name: _object_material(hfss.modeler, name) for name in expected_materials}
        checks.append(
            {
                "name": "initial_object_materials",
                "passed": all(
                    str(actual_materials[name] or "").casefold() == expected_material.casefold()
                    for name, expected_material in expected_materials.items()
                ),
                "detail": {"expected": expected_materials, "actual": actual_materials},
            }
        )
        if manifest.get("profile") == "leam_case3":
            expected_boxes = HfssBuildService._case3_expected_bounding_boxes(snapshot, final=False)
            actual_boxes = {
                name: _object_bounding_box_mm(hfss.modeler, name) for name in expected_boxes
            }
            checks.append(
                {
                    "name": "initial_object_bounding_boxes",
                    "passed": all(
                        _bounding_box_close(actual_boxes[name], expected_box)
                        for name, expected_box in expected_boxes.items()
                    ),
                    "detail": {
                        "unit": "mm",
                        "absolute_tolerance": 1e-6,
                        "expected": expected_boxes,
                        "actual": actual_boxes,
                    },
                }
            )
        return {"passed": all(item["passed"] for item in checks), "checks": checks}

    @staticmethod
    def _verify_geometry(hfss: Any, snapshot: dict[str, bytes]) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def check(name: str, passed: bool, detail: Any) -> None:
            checks.append({"name": name, "passed": bool(passed), "detail": detail})

        manifest_raw = snapshot.get("geometry_manifest")
        if manifest_raw:
            manifest = json.loads(manifest_raw.decode("utf-8"))
            expected = set(manifest.get("final_objects") or [])
            actual = set(hfss.modeler.object_names)
            check(
                "final_object_names",
                bool(expected) and actual == expected,
                {"expected": sorted(expected), "actual": sorted(actual)},
            )
            expected_object_materials = {
                "substrate": "LEAM_FR4",
                "radiator": "copper",
                "left_ground": "copper",
                "right_ground": "copper",
            }
            actual_object_materials = {
                name: _object_material(hfss.modeler, name) for name in expected_object_materials
            }
            check(
                "final_object_materials",
                all(
                    str(actual_object_materials[name] or "").casefold() == expected_material.casefold()
                    for name, expected_material in expected_object_materials.items()
                ),
                {"expected": expected_object_materials, "actual": actual_object_materials},
            )
            if manifest.get("profile") == "leam_case3":
                expected_boxes = HfssBuildService._case3_expected_bounding_boxes(
                    snapshot,
                    final=True,
                )
                actual_boxes = {
                    name: _object_bounding_box_mm(hfss.modeler, name) for name in expected_boxes
                }
                check(
                    "final_object_bounding_boxes",
                    all(
                        _bounding_box_close(actual_boxes[name], expected_box)
                        for name, expected_box in expected_boxes.items()
                    ),
                    {
                        "unit": "mm",
                        "absolute_tolerance": 1e-6,
                        "expected": expected_boxes,
                        "actual": actual_boxes,
                    },
                )
        else:
            check("model_contains_geometry", bool(list(hfss.modeler.object_names)), list(hfss.modeler.object_names))

        consistency = getattr(hfss.modeler, "model_consistency_report", None)
        if consistency is not None:
            if callable(consistency):
                consistency = consistency()
            consistent = isinstance(consistency, dict) and all(not values for values in consistency.values())
            check("modeler_consistency", consistent, consistency)

        materials_raw = snapshot.get("materials")
        if materials_raw:
            if not hasattr(hfss, "materials"):
                if manifest_raw:
                    check("material_manager_available", False, "hfss.materials is unavailable")
                return {"passed": all(item["passed"] for item in checks), "checks": checks}
            expected_materials = json.loads(materials_raw.decode("utf-8")).get("materials", [])
            fr4_spec = next((item for item in expected_materials if item.get("name") == "LEAM_FR4"), None)
            if not fr4_spec:
                if manifest_raw:
                    check("LEAM_FR4_properties", False, "LEAM_FR4 is missing from materials.json")
            else:
                fr4 = hfss.materials.exists_material("LEAM_FR4")
                actual_eps = _material_value(fr4, "permittivity")
                actual_loss = _material_value(fr4, "dielectric_loss_tangent")
                check(
                    "LEAM_FR4_properties",
                    _numeric_close(actual_eps, fr4_spec.get("relative_permittivity"))
                    and _numeric_close(actual_loss, fr4_spec.get("dielectric_loss_tangent")),
                    {
                        "expected_eps_r": fr4_spec.get("relative_permittivity"),
                        "actual_eps_r": actual_eps,
                        "expected_tan_delta": fr4_spec.get("dielectric_loss_tangent"),
                        "actual_tan_delta": actual_loss,
                    },
                )
        return {"passed": all(item["passed"] for item in checks), "checks": checks}

    @staticmethod
    def _case3_expected_bounding_boxes(
        snapshot: dict[str, bytes],
        *,
        final: bool,
    ) -> dict[str, list[float]]:
        payload = json.loads(snapshot["parameters"].decode("utf-8"))
        values = {
            str(item.get("name") or ""): float(item["value"])
            for item in payload.get("parameters", [])
            if isinstance(item, dict)
        }
        required = {
            "DPR",
            "SW",
            "SLT",
            "SLV",
            "SLH",
            "ML",
            "SL",
            "MW",
            "RPW",
            "SubT",
            "CuT",
            "ground_length",
        }
        if required - values.keys():
            raise ValueError("LEAM Case 3 bounding-box verification is missing required parameters")
        p = values
        conductor_top = p["SubT"] + p["CuT"]
        radiator = [
            p["SW"] / 2 - p["DPR"],
            p["ML"] - p["DPR"],
            p["SubT"],
            p["SW"] / 2 + p["DPR"],
            p["ML"] + p["DPR"],
            conductor_top,
        ]
        feedline = [
            (p["SW"] - p["MW"]) / 2,
            0.0,
            p["SubT"],
            (p["SW"] + p["MW"]) / 2,
            p["ML"],
            conductor_top,
        ]
        initial = {
            "substrate": [0.0, 0.0, 0.0, p["SW"], p["SL"], p["SubT"]],
            "radiator": radiator,
            "feedline": feedline,
            "left_ground": [
                0.0,
                0.0,
                p["SubT"],
                p["RPW"],
                p["ground_length"],
                conductor_top,
            ],
            "right_ground": [
                p["SW"] - p["RPW"],
                0.0,
                p["SubT"],
                p["SW"],
                p["ground_length"],
                conductor_top,
            ],
            "horizontal_slot": [
                (p["SW"] - p["SLH"]) / 2,
                p["ML"] - p["SLT"] / 2,
                p["SubT"],
                (p["SW"] + p["SLH"]) / 2,
                p["ML"] + p["SLT"] / 2,
                conductor_top,
            ],
            "vertical_slot": [
                (p["SW"] - p["SLT"]) / 2,
                p["ML"] - p["SLV"] / 2,
                p["SubT"],
                (p["SW"] + p["SLT"]) / 2,
                p["ML"] + p["SLV"] / 2,
                conductor_top,
            ],
        }
        if not final:
            return initial
        united_radiator = [
            min(radiator[0], feedline[0]),
            min(radiator[1], feedline[1]),
            min(radiator[2], feedline[2]),
            max(radiator[3], feedline[3]),
            max(radiator[4], feedline[4]),
            max(radiator[5], feedline[5]),
        ]
        return {
            "substrate": initial["substrate"],
            "radiator": united_radiator,
            "left_ground": initial["left_ground"],
            "right_ground": initial["right_ground"],
        }

    @staticmethod
    def _solution_type(state: JobState, snapshot: dict[str, bytes] | None = None) -> str:
        raw = (snapshot or {}).get("simulation_spec")
        if raw is None and snapshot is None:
            path = state.artifacts.get("simulation_spec")
            raw = Path(path).read_bytes() if path else None
        if not raw:
            return "Modal"
        try:
            payload = json.loads(raw.decode("utf-8"))
            value = str(payload.get("solution_type") or "Modal")
        except (UnicodeDecodeError, ValueError, TypeError):
            return "Modal"
        aliases = {
            "driven modal": "Modal",
            "modal": "Modal",
            "driven terminal": "Terminal",
            "terminal": "Terminal",
        }
        return aliases.get(value.strip().lower(), value)


def _default_hfss_factory(**kwargs: Any) -> Any:
    session_mode = kwargs.pop("session_mode", "new")
    grpc_port = kwargs.pop("grpc_port", None)
    project = kwargs.get("project")
    search_dirs = [Path(project).expanduser().resolve().parent] if project else None
    if session_mode == "new":
        preflight = aedt_license_preflight(search_dirs)
        if preflight:
            raise RuntimeError(preflight)
    prepare_pyaedt_environment()
    try:
        from ansys.aedt.core import Hfss
    except ImportError as exc:
        raise RuntimeError("Install the hfss extra: pip install 'leam-opt-mcp[hfss]'") from exc
    version = preferred_aedt_version()
    if version:
        kwargs["version"] = version
    if session_mode == "existing":
        if grpc_port is None:
            raise ValueError("session_mode='existing' requires an explicit AEDT gRPC port")
        from ansys.aedt.core.generic.general_methods import is_grpc_session_active

        if not is_grpc_session_active(grpc_port, "127.0.0.1"):
            raise RuntimeError(
                f"no active AEDT gRPC session is available on port {grpc_port}; refusing to launch a fallback session"
            )
        # Force PyAEDT to honor the explicit port even if this Python process has
        # cached another Desktop object. Create a dedicated project/design inside
        # that Desktop instead of copying or modifying the user's active project.
        with temporary_multi_desktop():
            app = Hfss(
                non_graphical=False,
                new_desktop=False,
                close_on_exit=False,
                port=grpc_port,
                **kwargs,
            )
        ensure_strict_existing_attachment(app, grpc_port)
        return app
    return Hfss(non_graphical=True, new_desktop=True, **kwargs)


def _material_value(material: Any, name: str) -> Any:
    if not material:
        return None
    value = getattr(material, name, None)
    return getattr(value, "value", value)


def _object_material(modeler: Any, name: str) -> Any:
    try:
        obj = modeler[name]
    except (KeyError, TypeError, AttributeError):
        objects = getattr(modeler, "objects_by_name", {})
        obj = objects.get(name) if isinstance(objects, dict) else None
    return getattr(obj, "material_name", None)


def _object_bounding_box_mm(modeler: Any, name: str) -> list[float] | None:
    try:
        obj = modeler[name]
    except (KeyError, TypeError, AttributeError):
        objects = getattr(modeler, "objects_by_name", {})
        obj = objects.get(name) if isinstance(objects, dict) else None
    raw = getattr(obj, "bounding_box", None)
    if callable(raw):
        raw = raw()
    if not isinstance(raw, (list, tuple)) or len(raw) != 6:
        return None
    model_units = getattr(modeler, "model_units", "mm") or "mm"
    if callable(model_units):
        model_units = model_units()
    factor = {
        "m": 1000.0,
        "cm": 10.0,
        "mm": 1.0,
        "um": 1e-3,
        "nm": 1e-6,
        "in": 25.4,
        "mil": 0.0254,
        "ft": 304.8,
    }.get(str(model_units).strip().casefold())
    if factor is None:
        return None
    try:
        values = [float(value) * factor for value in raw]
    except (TypeError, ValueError):
        return None
    return values if all(math.isfinite(value) for value in values) else None


def _bounding_box_close(
    actual: list[float] | None,
    expected: list[float],
    tolerance: float = 1e-6,
) -> bool:
    return actual is not None and len(actual) == len(expected) and all(
        abs(left - right) <= tolerance for left, right in zip(actual, expected)
    )


def _numeric_close(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def _execute_fragment(source: str, hfss: Any) -> None:
    safe_builtins = {
        "abs": abs,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "round": round,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
    }
    exec(compile(source, "<generated-hfss-fragment>", "exec"), {"__builtins__": safe_builtins}, {"hfss": hfss})
