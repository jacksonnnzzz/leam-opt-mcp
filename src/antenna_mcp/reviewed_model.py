from __future__ import annotations

import hashlib
import hmac
import json
import math
from pathlib import Path
from typing import Any

from .modeling import validate_generated_python
from .review import ArtifactReviewService
from .workspace import WorkspaceStore


CASE3_COMPONENTS = (
    "substrate",
    "radiator",
    "feedline",
    "left_ground",
    "right_ground",
    "horizontal_slot",
    "vertical_slot",
)

CASE3_OPERATIONS = (
    ("unite", "radiator", ("feedline",)),
    ("unite", "horizontal_slot", ("vertical_slot",)),
    ("subtract", "radiator", ("horizontal_slot",)),
)

DOWNSTREAM_ARTIFACTS = (
    "parameters",
    "materials",
    "solids",
    "dimensions",
    "model_3d",
    "model_2d",
    "boolean",
    "simulation_spec",
    "simulation_setup",
    "optimization_spec",
    "geometry_manifest",
    "geometry_validation",
    "builder",
    "review_packet",
    "hfss_project",
    "hfss_build_report",
)


class EngineeringAssumptionService:
    """Record user-approved engineering decisions without changing source evidence."""

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def prepare(
        self,
        job_id: str,
        symbol: str,
        value: float,
        unit: str,
        rationale: str,
    ) -> dict[str, Any]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("assumption value must be a finite number")
        if len(rationale.strip()) < 10:
            raise ValueError("rationale must explain why the engineering assumption is needed")

        state = self.store.load_state(job_id)
        if state.kind != "modeling" or state.status != "completed":
            raise ValueError("a completed modeling job with approved source evidence is required")
        source_path, source_bytes, source_packet_hash = self._verify_source_approval(state)
        source = json.loads(source_bytes)
        parameter = self._unresolved_parameter(source, symbol)
        expected_unit = str(parameter.get("unit") or "")
        if expected_unit.casefold() != unit.strip().casefold():
            raise ValueError(
                f"unit mismatch for {symbol}: approved source uses {expected_unit!r}, got {unit!r}"
            )
        binding = parameter.get("semantic_binding") or {}
        if str(binding.get("quantity") or "").casefold() == "thickness" and value <= 0:
            raise ValueError("a thickness assumption must be greater than zero")

        decision = {
            "assumption_id": f"{binding.get('claim_id') or symbol}-baseline-v1",
            "kind": "missing_parameter_resolution",
            "symbol": parameter["symbol"],
            "quantity": binding.get("quantity"),
            "value": float(value),
            "unit": expected_unit,
            "classification": "engineering_assumption",
            "paper_evidence": False,
            "source_claim": {
                "claim_id": binding.get("claim_id"),
                "original_value": parameter.get("value"),
                "original_evidence_mode": binding.get("mode"),
            },
            "rationale": rationale.strip(),
            "optimizable": False,
        }
        if parameter["symbol"].casefold() == "cut":
            decision["physical_components"] = [
                "radiator",
                "feedline",
                "left_ground",
                "right_ground",
            ]
            decision["implementation_dependencies"] = [
                "horizontal_slot.height",
                "vertical_slot.height",
            ]

        prior_decisions: list[dict[str, Any]] = []
        existing_raw = state.artifacts.get("engineering_assumptions_approved")
        receipt_raw = state.artifacts.get("engineering_assumptions_receipt")
        if bool(existing_raw) != bool(receipt_raw):
            raise PermissionError("existing engineering assumption approval artifacts are incomplete")
        if existing_raw and receipt_raw:
            existing_path = Path(existing_raw).expanduser().resolve()
            existing = json.loads(existing_path.read_text("utf-8"))
            existing_receipt = json.loads(Path(receipt_raw).read_text("utf-8"))
            try:
                existing = ReviewedModelCompiler._load_assumptions(
                    state.artifacts,
                    source_path,
                    source_bytes,
                    source_packet_hash,
                    state.job_id,
                    str(existing_receipt.get("assumption_approval_hash") or ""),
                )
            except (FileNotFoundError, KeyError, PermissionError, ValueError):
                # A pre-two-stage receipt cannot be treated as an approval. It can only be
                # replaced safely when the new proposal covers every legacy decision.
                legacy_symbols = {
                    str(item.get("symbol") or "").casefold()
                    for item in existing.get("decisions", [])
                }
                if legacy_symbols - {parameter["symbol"].casefold()}:
                    raise PermissionError(
                        "legacy engineering assumptions contain other decisions; re-propose each one"
                    )
                existing = {"decisions": []}
            prior_decisions = [
                item
                for item in existing.get("decisions", [])
                if str(item.get("symbol") or "").casefold() != parameter["symbol"].casefold()
            ]
            versions = [
                _assumption_version(item.get("assumption_id"))
                for item in existing.get("decisions", [])
                if str(item.get("symbol") or "").casefold() == parameter["symbol"].casefold()
            ]
            decision["assumption_id"] = f"{binding.get('claim_id') or symbol}-baseline-v{max(versions, default=0) + 1}"

        decisions = sorted([*prior_decisions, decision], key=lambda item: str(item.get("symbol") or ""))
        payload = {
            "schema_version": "1.0",
            "job_id": job_id,
            "base_source": {
                "artifact": source_path.name,
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
                "source_review_approval_hash": source_packet_hash,
            },
            "decisions": decisions,
        }
        path = self.store.write_artifact(
            job_id,
            "engineering_assumptions_candidate.json",
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        packet = _assumption_review_packet(path, source_path, source_packet_hash)
        packet_path = self.store.write_artifact(
            job_id,
            "engineering_assumption_review_packet.json",
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
        )
        for key in DOWNSTREAM_ARTIFACTS:
            state.artifacts.pop(key, None)
        state.artifacts["engineering_assumptions_candidate"] = str(path)
        state.artifacts["engineering_assumption_review_packet"] = str(packet_path)
        state.artifacts.pop("engineering_assumptions_approved", None)
        state.artifacts.pop("engineering_assumptions_receipt", None)
        state.status = "awaiting_review"
        state.current_stage = "engineering_assumption_review"
        state.error = None
        self.store.save_state(state)
        return {
            "job_id": job_id,
            "status": "awaiting_review",
            "candidate": str(path),
            "review_packet": str(packet_path),
            "approval_hash": packet["approval_hash"],
            "source_sha256": payload["base_source"]["sha256"],
            "decision": decision,
        }

    def approve(self, job_id: str, approval_hash: str) -> dict[str, Any]:
        state = self.store.load_state(job_id)
        if (
            state.kind != "modeling"
            or state.status != "awaiting_review"
            or state.current_stage != "engineering_assumption_review"
        ):
            raise ValueError("an awaiting-review engineering assumption candidate is required")
        source_path, source_bytes, source_packet_hash = self._verify_source_approval(state)
        candidate_path = Path(state.artifacts["engineering_assumptions_candidate"]).resolve()
        packet = _assumption_review_packet(candidate_path, source_path, source_packet_hash)
        if not hmac.compare_digest(packet["approval_hash"], approval_hash):
            raise PermissionError("engineering assumption approval hash does not match the current candidate")
        candidate_bytes = candidate_path.read_bytes()
        candidate = json.loads(candidate_bytes)
        if (candidate.get("base_source") or {}).get("sha256") != hashlib.sha256(source_bytes).hexdigest():
            raise PermissionError("engineering assumption candidate no longer matches the approved source")
        if (candidate.get("base_source") or {}).get("source_review_approval_hash") != source_packet_hash:
            raise PermissionError("engineering assumption candidate no longer matches the source review")
        _validate_assumption_payload(
            candidate,
            json.loads(source_bytes),
            job_id,
            source_path,
            source_packet_hash,
        )
        approved_path = self.store.write_artifact(
            job_id,
            "engineering_assumptions_approved.json",
            candidate_bytes.decode("utf-8"),
        )
        receipt = {
            "schema_version": "1.0",
            "job_id": job_id,
            "artifact": approved_path.name,
            "sha256": hashlib.sha256(candidate_bytes).hexdigest(),
            "base_source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "source_review_approval_hash": source_packet_hash,
            "assumption_approval_hash": approval_hash,
            "approval_method": "content_hash_round_trip",
            "decision_symbols": [item["symbol"] for item in candidate.get("decisions", [])],
        }
        receipt_path = self.store.write_artifact(
            job_id,
            "engineering_assumptions_receipt.json",
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        )
        state.artifacts["engineering_assumptions_approved"] = str(approved_path)
        state.artifacts["engineering_assumptions_receipt"] = str(receipt_path)
        state.status = "completed"
        state.current_stage = "engineering_assumptions_approved"
        state.error = None
        self.store.save_state(state)
        return {
            "job_id": job_id,
            "status": "completed",
            "approved": str(approved_path),
            "receipt": str(receipt_path),
            "approval_hash": approval_hash,
        }

    @staticmethod
    def _approved_source_path(artifacts: dict[str, str]) -> Path:
        raw = artifacts.get("source_analysis_approved")
        if not raw:
            raise ValueError("source_analysis_approved.json is required")
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    @staticmethod
    def _unresolved_parameter(source: dict[str, Any], symbol: str) -> dict[str, Any]:
        matches = [
            item
            for item in source.get("parameters", [])
            if str(item.get("symbol") or "").casefold() == symbol.strip().casefold()
        ]
        if len(matches) != 1:
            raise ValueError(f"approved source must contain exactly one parameter named {symbol!r}")
        parameter = matches[0]
        binding = parameter.get("semantic_binding") or {}
        if parameter.get("value") is not None or binding.get("mode") != "unresolved":
            raise ValueError(
                f"engineering assumptions may only resolve null/unresolved source parameters; {symbol} is evidence-backed"
            )
        return parameter

    def _verify_source_approval(self, state: Any) -> tuple[Path, bytes, str]:
        source_path = self._approved_source_path(state.artifacts)
        candidate_raw = state.artifacts.get("source_analysis_candidate")
        packet_raw = state.artifacts.get("source_review_packet")
        report_raw = state.artifacts.get("source_refinement_report")
        if not candidate_raw or not packet_raw or not report_raw:
            raise ValueError("the completed source approval chain is incomplete")
        job_dir = self.store.job_dir(state.job_id)
        candidate_path = Path(candidate_raw).expanduser().resolve()
        packet_path = Path(packet_raw).expanduser().resolve()
        report_path = Path(report_raw).expanduser().resolve()
        for path in (source_path, candidate_path, packet_path, report_path):
            if path.parent != job_dir or not path.is_file():
                raise PermissionError(f"source approval artifact is outside the job directory or missing: {path}")
        source_bytes = source_path.read_bytes()
        if source_bytes != candidate_path.read_bytes():
            raise PermissionError("source_analysis_approved no longer matches the hash-approved candidate")
        report = json.loads(report_path.read_text("utf-8"))
        if report.get("quality_gate_passed") is not True:
            raise PermissionError("the source refinement quality gate is not currently passed")

        packet = json.loads(packet_path.read_text("utf-8"))
        expected_paths: list[tuple[str, Path]] = [
            ("candidate", candidate_path),
            ("report", report_path),
        ]
        for artifact_name, packet_name in (
            ("source_visual_audit", "visual_audit"),
            ("source_visual_verdict", "visual_verdict"),
        ):
            raw = state.artifacts.get(artifact_name)
            if raw:
                expected_paths.append((packet_name, Path(raw).expanduser().resolve()))
        visual_names = sorted(
            name for name in state.artifacts if name.startswith("source_visual_input_")
        )
        for index, artifact_name in enumerate(visual_names, start=1):
            expected_paths.append(
                (f"visual_input_{index}", Path(state.artifacts[artifact_name]).expanduser().resolve())
            )
        recomputed = []
        for name, path in expected_paths:
            if path.parent != job_dir or not path.is_file():
                raise PermissionError(f"source review packet contains an invalid artifact path: {path}")
            data = path.read_bytes()
            recomputed.append(
                {
                    "name": name,
                    "path": str(path),
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        if packet.get("artifacts") != recomputed:
            raise PermissionError("source review packet artifact set differs from the completed source review")
        canonical = json.dumps(recomputed, sort_keys=True, separators=(",", ":"))
        approval_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(str(packet.get("approval_hash") or ""), approval_hash):
            raise PermissionError("source review packet no longer matches its approved artifacts")
        candidate_entries = [item for item in recomputed if item["name"] == "candidate"]
        if len(candidate_entries) != 1 or candidate_entries[0]["sha256"] != hashlib.sha256(
            source_bytes
        ).hexdigest():
            raise PermissionError("source review packet does not bind the approved source candidate")
        return source_path, source_bytes, approval_hash


class ReviewedModelCompiler:
    """Compile approved evidence into deterministic, reviewable HFSS artifacts."""

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def compile(
        self,
        job_id: str,
        profile: str = "auto",
        assumption_approval_hash: str | None = None,
    ) -> dict[str, Any]:
        state = self.store.load_state(job_id)
        if state.kind != "modeling" or state.status != "completed":
            raise ValueError("a completed modeling job is required")
        if not assumption_approval_hash:
            raise PermissionError(
                "the user-returned engineering assumption approval hash is required for compilation"
            )
        source_path, source_bytes, source_review_hash = EngineeringAssumptionService(
            self.store
        )._verify_source_approval(state)
        source = json.loads(source_bytes)
        assumptions = self._load_assumptions(
            state.artifacts,
            source_path,
            source_bytes,
            source_review_hash,
            job_id,
            assumption_approval_hash,
        )
        resolved = self._resolved_parameters(source, assumptions)
        selected = self._select_profile(source, profile)
        if selected != "leam_case3":  # pragma: no cover - guarded by _select_profile
            raise ValueError(f"unsupported reviewed compiler profile: {selected}")
        self._validate_case3_source_contract(source)

        artifacts, validation = self._compile_leam_case3(source, resolved, assumptions)
        if not validation["passed"]:
            failures = [item["name"] for item in validation["checks"] if not item["passed"]]
            raise ValueError(f"reviewed geometry validation failed: {', '.join(failures)}")

        for key in DOWNSTREAM_ARTIFACTS:
            state.artifacts.pop(key, None)
        for name, payload in artifacts.items():
            if isinstance(payload, str):
                validate_generated_python(payload)
                path = self.store.write_artifact(job_id, f"{name}.py", payload.rstrip() + "\n")
            else:
                path = self.store.write_artifact(
                    job_id,
                    f"{name}.json",
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                )
            state.artifacts[name] = str(path)
        validation_path = self.store.write_artifact(
            job_id,
            "geometry_validation.json",
            json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
        )
        state.artifacts["geometry_validation"] = str(validation_path)
        builder_path = self._assemble_builder(job_id, artifacts)
        state.artifacts["builder"] = str(builder_path)
        state.current_stage = "artifact_review"
        state.status = "completed"
        state.error = None
        self.store.save_state(state)
        review = ArtifactReviewService(self.store).prepare(job_id)
        return {
            "job_id": job_id,
            "status": "awaiting_artifact_review",
            "profile": selected,
            "validation": validation,
            "review": review,
            "artifacts": {
                key: self.store.load_state(job_id).artifacts[key]
                for key in (
                    "parameters",
                    "materials",
                    "solids",
                    "dimensions",
                    "model_3d",
                    "boolean",
                    "geometry_manifest",
                    "geometry_validation",
                    "builder",
                    "review_packet",
                )
            },
        }

    @staticmethod
    def _load_assumptions(
        artifacts: dict[str, str],
        source_path: Path,
        source_bytes: bytes,
        source_review_hash: str,
        job_id: str,
        expected_assumption_approval_hash: str,
    ) -> dict[str, Any]:
        raw = artifacts.get("engineering_assumptions_approved")
        if not raw:
            raise ValueError("approved engineering assumptions are required for unresolved geometry")
        path = Path(raw).expanduser().resolve()
        candidate_raw = artifacts.get("engineering_assumptions_candidate")
        review_packet_raw = artifacts.get("engineering_assumption_review_packet")
        receipt_raw = artifacts.get("engineering_assumptions_receipt")
        if not candidate_raw or not review_packet_raw or not receipt_raw:
            raise PermissionError("the engineering assumption approval chain is incomplete")
        candidate_path = Path(candidate_raw).expanduser().resolve()
        review_packet_path = Path(review_packet_raw).expanduser().resolve()
        receipt_path = Path(receipt_raw).expanduser().resolve()
        job_dir = source_path.resolve().parent
        for artifact_path in (path, candidate_path, review_packet_path, receipt_path):
            if artifact_path.parent != job_dir or not artifact_path.is_file():
                raise PermissionError(
                    f"engineering assumption artifact is outside the job directory or missing: {artifact_path}"
                )
        assumption_bytes = path.read_bytes()
        candidate_bytes = candidate_path.read_bytes()
        if assumption_bytes != candidate_bytes:
            raise PermissionError(
                "approved engineering assumptions no longer match the hash-approved candidate"
            )
        payload = json.loads(assumption_bytes)
        expected = hashlib.sha256(source_bytes).hexdigest()
        actual = str((payload.get("base_source") or {}).get("sha256") or "")
        if actual != expected:
            raise PermissionError("engineering assumptions no longer match the approved source artifact")
        if (payload.get("base_source") or {}).get("source_review_approval_hash") != source_review_hash:
            raise PermissionError("engineering assumptions no longer match the approved source review")
        _validate_assumption_payload(
            payload,
            json.loads(source_bytes),
            job_id,
            source_path,
            source_review_hash,
        )
        receipt = json.loads(receipt_path.read_text("utf-8"))
        if receipt.get("sha256") != hashlib.sha256(assumption_bytes).hexdigest():
            raise PermissionError("engineering assumptions no longer match their confirmation receipt")
        if receipt.get("artifact") != path.name:
            raise PermissionError("engineering assumption receipt names a different approved artifact")
        if receipt.get("approval_method") != "content_hash_round_trip":
            raise PermissionError("engineering assumption receipt lacks content-hash approval")
        if receipt.get("base_source_sha256") != expected:
            raise PermissionError("engineering assumption receipt no longer matches the approved source")
        if receipt.get("source_review_approval_hash") != source_review_hash:
            raise PermissionError("engineering assumption receipt no longer matches the source review")
        recomputed_packet = _assumption_review_packet(
            candidate_path,
            source_path,
            source_review_hash,
        )
        stored_packet = json.loads(review_packet_path.read_text("utf-8"))
        if stored_packet != recomputed_packet:
            raise PermissionError("engineering assumption review packet no longer matches its artifacts")
        if not hmac.compare_digest(
            str(receipt.get("assumption_approval_hash") or ""),
            recomputed_packet["approval_hash"],
        ):
            raise PermissionError("engineering assumption receipt no longer matches the reviewed candidate")
        if not hmac.compare_digest(
            expected_assumption_approval_hash,
            recomputed_packet["approval_hash"],
        ):
            raise PermissionError(
                "the supplied engineering assumption approval hash does not match the reviewed candidate"
            )
        decision_symbols = [item.get("symbol") for item in payload.get("decisions", [])]
        if receipt.get("decision_symbols") != decision_symbols:
            raise PermissionError("engineering assumption receipt decision list is stale")
        return payload

    @staticmethod
    def _resolved_parameters(
        source: dict[str, Any], assumptions: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        resolved: dict[str, dict[str, Any]] = {}
        decisions = {
            str(item.get("symbol") or "").casefold(): item
            for item in assumptions.get("decisions", [])
        }
        for parameter in source.get("parameters", []):
            symbol = str(parameter.get("symbol") or "")
            value = parameter.get("value")
            provenance: dict[str, Any] = {
                "kind": "source_evidence",
                "evidence_mode": (parameter.get("semantic_binding") or {}).get("mode"),
                "claim_id": (parameter.get("semantic_binding") or {}).get("claim_id"),
            }
            if value is None:
                decision = decisions.get(symbol.casefold())
                if not decision:
                    raise ValueError(f"unresolved parameter {symbol} has no approved engineering assumption")
                if decision.get("classification") != "engineering_assumption":
                    raise ValueError(f"invalid assumption classification for {symbol}")
                value = decision.get("value")
                provenance = {
                    "kind": "engineering_assumption",
                    "assumption_id": decision.get("assumption_id"),
                    "paper_evidence": False,
                }
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"parameter {symbol} is not a finite numeric value")
            resolved[symbol] = {
                "name": symbol,
                "value": float(value),
                "unit": str(parameter.get("unit") or ""),
                "description": parameter.get("geometric_meaning"),
                "optimizable": False,
                "provenance": provenance,
            }
        return resolved

    @staticmethod
    def _select_profile(source: dict[str, Any], requested: str) -> str:
        if requested not in {"auto", "leam_case3"}:
            raise ValueError("profile must be 'auto' or 'leam_case3'")
        component_names = {str(item.get("name") or "") for item in source.get("components", [])}
        antenna_type = str(source.get("antenna_type") or "").casefold()
        is_case3 = component_names == set(CASE3_COMPONENTS) and "quasi-cross" in antenna_type
        if requested == "leam_case3" and not is_case3:
            raise ValueError("approved source does not match the LEAM Case 3 profile")
        if requested == "leam_case3" and is_case3:
            return "leam_case3"
        if requested == "auto" and is_case3:
            return "leam_case3"
        raise ValueError("no deterministic compiler profile matches the approved source")

    @staticmethod
    def _validate_case3_source_contract(source: dict[str, Any]) -> None:
        expected_components = {
            "substrate": ("box", "dielectric", "volume"),
            "radiator": ("cylinder", "conductor", "top_coplanar"),
            "feedline": ("box", "conductor", "top_coplanar"),
            "left_ground": ("box", "conductor", "top_coplanar"),
            "right_ground": ("box", "conductor", "top_coplanar"),
            "horizontal_slot": ("box", "void", "subtraction"),
            "vertical_slot": ("box", "void", "subtraction"),
        }
        components = {str(item.get("name") or ""): item for item in source.get("components", [])}
        for name, expected in expected_components.items():
            binding = (components.get(name) or {}).get("evidence_binding") or {}
            actual = (
                binding.get("primitive_class"),
                binding.get("material_class"),
                binding.get("layer_class"),
            )
            if actual != expected:
                raise ValueError(f"LEAM Case 3 component contract mismatch for {name}: {actual} != {expected}")

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
            "CuT": "mm",
            "eps_r": "",
            "tan_delta": "",
        }
        parameters = {str(item.get("symbol") or ""): item for item in source.get("parameters", [])}
        if set(parameters) != set(expected_units) or len(parameters) != len(source.get("parameters", [])):
            raise ValueError("LEAM Case 3 parameter set is incomplete, duplicated, or contaminated")
        for symbol, expected_unit in expected_units.items():
            if str((parameters.get(symbol) or {}).get("unit") or "") != expected_unit:
                raise ValueError(f"LEAM Case 3 parameter {symbol} must use {expected_unit!r}")
        expected_quantities = {
            "DPR": "radius",
            "SW": "width",
            "SLT": "width",
            "SLV": "length",
            "SLH": "length",
            "ML": "offset",
            "RPL": "gap",
            "MW": "width",
            "MG": "gap",
            "SL": "length",
            "RPW": "width",
            "SubT": "thickness",
            "CuT": "thickness",
            "eps_r": "material_property",
            "tan_delta": "material_property",
        }
        expected_modes = {
            **{name: "visual" for name in expected_units if name not in {"SubT", "eps_r", "tan_delta", "CuT"}},
            "SubT": "text",
            "eps_r": "text",
            "tan_delta": "text",
            "CuT": "unresolved",
        }
        for symbol, quantity in expected_quantities.items():
            binding = parameters[symbol].get("semantic_binding") or {}
            if binding.get("quantity") != quantity or binding.get("mode") != expected_modes[symbol]:
                raise ValueError(f"LEAM Case 3 semantic binding mismatch for {symbol}")

        coordinate_system = source.get("coordinate_system") or {}
        if (
            not str(coordinate_system.get("plane") or "").casefold().startswith("xy")
            or coordinate_system.get("origin") != [0, 0, 0]
            or len(coordinate_system.get("axes") or []) != 3
        ):
            raise ValueError("LEAM Case 3 coordinate-system contract must be an XY model at the origin")

        expected_relations = {
            "case3-relation-SL": ("SL = ML + DPR + 0.2", ["SL", "ML", "DPR"]),
            "case3-relation-RPW": (
                "RPW = (SW - MW - 2*MG) / 2",
                ["RPW", "SW", "MW", "MG"],
            ),
            "case3-relation-ground-length": ("ground_length = ML - RPL", ["ML", "RPL"]),
        }
        relations = {
            str(item.get("claim_id") or ""): item for item in source.get("derived_relations", [])
        }
        if set(relations) != set(expected_relations) or len(relations) != len(
            source.get("derived_relations", [])
        ):
            raise ValueError("LEAM Case 3 derived-relation contract is incomplete or contaminated")
        for claim_id, (expression, symbols) in expected_relations.items():
            relation = relations[claim_id]
            if relation.get("expression") != expression or relation.get("symbols") != symbols:
                raise ValueError(f"LEAM Case 3 derived relation mismatch for {claim_id}")

    def _compile_leam_case3(
        self,
        source: dict[str, Any],
        resolved: dict[str, dict[str, Any]],
        assumptions: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        required = {
            "DPR",
            "SW",
            "SLT",
            "SLV",
            "SLH",
            "ML",
            "RPL",
            "MW",
            "MG",
            "SL",
            "RPW",
            "SubT",
            "eps_r",
            "tan_delta",
            "CuT",
        }
        missing = required - resolved.keys()
        if missing:
            raise ValueError(f"LEAM Case 3 is missing parameters: {sorted(missing)}")
        operations = self._normalized_operations(source.get("operations", []))
        if operations != CASE3_OPERATIONS:
            raise ValueError("approved source boolean topology does not match LEAM Case 3")

        p = {name: item["value"] for name, item in resolved.items()}
        parameter_order = (
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
        )
        parameters = [resolved[name] for name in parameter_order]
        parameters.extend(
            [
                {
                    "name": "SL",
                    "value": p["SL"],
                    "unit": "mm",
                    "expression": "ML+DPR+0.2mm",
                    "description": "overall substrate length",
                    "optimizable": False,
                    "provenance": {"kind": "derived_relation", "claim_id": "case3-relation-SL"},
                },
                {
                    "name": "RPW",
                    "value": p["RPW"],
                    "unit": "mm",
                    "expression": "(SW-MW-2*MG)/2",
                    "description": "each partial-ground width",
                    "optimizable": False,
                    "provenance": {"kind": "derived_relation", "claim_id": "case3-relation-RPW"},
                },
                {
                    "name": "ground_length",
                    "value": round(p["ML"] - p["RPL"], 12),
                    "unit": "mm",
                    "expression": "ML-RPL",
                    "description": "partial-ground length",
                    "optimizable": False,
                    "provenance": {
                        "kind": "derived_relation",
                        "claim_id": "case3-relation-ground-length",
                    },
                },
            ]
        )

        materials = {
            "materials": [
                {
                    "name": "LEAM_FR4",
                    "source_label": "FR-4",
                    "relative_permittivity": p["eps_r"],
                    "dielectric_loss_tangent": p["tan_delta"],
                    "provenance": "source_evidence",
                },
                {
                    "name": "copper",
                    "source_label": "copper (pure)",
                    "database_material": True,
                    "provenance": "source_material_class",
                },
                {
                    "name": "vacuum",
                    "role": "temporary slot subtraction tools",
                    "database_material": True,
                },
            ]
        }
        solids = {
            "solids": [
                {"name": "substrate", "primitive": "box", "material": "LEAM_FR4"},
                {"name": "radiator", "primitive": "cylinder", "material": "copper"},
                {"name": "feedline", "primitive": "box", "material": "copper"},
                {"name": "left_ground", "primitive": "box", "material": "copper"},
                {"name": "right_ground", "primitive": "box", "material": "copper"},
                {"name": "horizontal_slot", "primitive": "box", "material": "vacuum"},
                {"name": "vertical_slot", "primitive": "box", "material": "vacuum"},
            ]
        }
        dimensions = {
            "coordinate_system": source["coordinate_system"],
            "dimensions": [
                {"name": "substrate", "origin": ["0mm", "0mm", "0mm"], "size": ["SW", "SL", "SubT"]},
                {"name": "radiator", "origin": ["SW/2", "ML", "SubT"], "radius": "DPR", "height": "CuT", "axis": "Z"},
                {"name": "feedline", "origin": ["(SW-MW)/2", "0mm", "SubT"], "size": ["MW", "ML", "CuT"]},
                {"name": "left_ground", "origin": ["0mm", "0mm", "SubT"], "size": ["RPW", "ground_length", "CuT"]},
                {"name": "right_ground", "origin": ["SW-RPW", "0mm", "SubT"], "size": ["RPW", "ground_length", "CuT"]},
                {"name": "horizontal_slot", "origin": ["(SW-SLH)/2", "ML-SLT/2", "SubT"], "size": ["SLH", "SLT", "CuT"]},
                {"name": "vertical_slot", "origin": ["(SW-SLT)/2", "ML-SLV/2", "SubT"], "size": ["SLT", "SLV", "CuT"]},
            ],
        }
        model_3d = f"""leam_fr4 = hfss.materials.add_material(\"LEAM_FR4\")
leam_fr4.permittivity = {p["eps_r"]!r}
leam_fr4.dielectric_loss_tangent = {p["tan_delta"]!r}

hfss.modeler.create_box([\"0mm\", \"0mm\", \"0mm\"], [\"SW\", \"SL\", \"SubT\"], name=\"substrate\", material=\"LEAM_FR4\")
hfss.modeler.create_cylinder(orientation=\"Z\", origin=[\"SW/2\", \"ML\", \"SubT\"], radius=\"DPR\", height=\"CuT\", name=\"radiator\", material=\"copper\")
hfss.modeler.create_box([\"(SW-MW)/2\", \"0mm\", \"SubT\"], [\"MW\", \"ML\", \"CuT\"], name=\"feedline\", material=\"copper\")
hfss.modeler.create_box([\"0mm\", \"0mm\", \"SubT\"], [\"RPW\", \"ground_length\", \"CuT\"], name=\"left_ground\", material=\"copper\")
hfss.modeler.create_box([\"SW-RPW\", \"0mm\", \"SubT\"], [\"RPW\", \"ground_length\", \"CuT\"], name=\"right_ground\", material=\"copper\")
hfss.modeler.create_box([\"(SW-SLH)/2\", \"ML-SLT/2\", \"SubT\"], [\"SLH\", \"SLT\", \"CuT\"], name=\"horizontal_slot\", material=\"vacuum\")
hfss.modeler.create_box([\"(SW-SLT)/2\", \"ML-SLV/2\", \"SubT\"], [\"SLT\", \"SLV\", \"CuT\"], name=\"vertical_slot\", material=\"vacuum\")
"""
        boolean = """hfss.modeler.unite([\"radiator\", \"feedline\"])
hfss.modeler.unite([\"horizontal_slot\", \"vertical_slot\"])
hfss.modeler.subtract(\"radiator\", \"horizontal_slot\", keep_originals=False)
hfss.modeler.fit_all()
"""
        manifest = {
            "profile": "leam_case3",
            "initial_objects": list(CASE3_COMPONENTS),
            "final_objects": ["substrate", "radiator", "left_ground", "right_ground"],
            "operations": [
                {"order": index, "operation": op, "target": target, "operands": list(operands)}
                for index, (op, target, operands) in enumerate(CASE3_OPERATIONS, start=1)
            ],
            "source_artifact": "source_analysis_approved.json",
            "assumption_artifact": "engineering_assumptions_approved.json",
        }
        validation = self._validate_case3_geometry(p, source, assumptions)
        return (
            {
                "parameters": {"parameters": parameters},
                "materials": materials,
                "solids": solids,
                "dimensions": dimensions,
                "model_3d": model_3d,
                "boolean": boolean,
                "geometry_manifest": manifest,
            },
            validation,
        )

    @staticmethod
    def _normalized_operations(raw: list[dict[str, Any]]) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        normalized = []
        for item in sorted(raw, key=lambda value: int(value.get("order", 0))):
            target = item.get("target", item.get("blank"))
            operands = item.get("operands", item.get("tools", []))
            normalized.append(
                (
                    str(item.get("operation") or ""),
                    str(target or ""),
                    tuple(str(value) for value in operands),
                )
            )
        return tuple(normalized)

    @staticmethod
    def _validate_case3_geometry(
        p: dict[str, float], source: dict[str, Any], assumptions: dict[str, Any]
    ) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def check(name: str, passed: bool, detail: str) -> None:
            checks.append({"name": name, "passed": bool(passed), "detail": detail})

        component_names = {str(item.get("name") or "") for item in source.get("components", [])}
        check("seven_source_components", component_names == set(CASE3_COMPONENTS), str(sorted(component_names)))
        check("SL_relation", abs(p["SL"] - (p["ML"] + p["DPR"] + 0.2)) <= 1e-9, "SL = ML + DPR + 0.2")
        check("RPW_relation", abs(p["RPW"] - (p["SW"] - p["MW"] - 2 * p["MG"]) / 2) <= 1e-9, "RPW = (SW-MW-2*MG)/2")
        ground_length = p["ML"] - p["RPL"]
        check("positive_ground_length", ground_length > 0, f"ground_length={ground_length}")
        check("positive_copper_thickness", 0 < p["CuT"] < p["SubT"], f"CuT={p['CuT']}, SubT={p['SubT']}")
        check("radiator_x_inside_board", p["SW"] / 2 - p["DPR"] >= 0 and p["SW"] / 2 + p["DPR"] <= p["SW"], "radiator radial x bounds")
        check("radiator_y_inside_board", p["ML"] - p["DPR"] >= 0 and p["ML"] + p["DPR"] <= p["SL"], "radiator radial y bounds")
        slot_corner_radius = math.hypot(max(p["SLH"], p["SLT"]) / 2, min(p["SLH"], p["SLT"]) / 2)
        vertical_corner_radius = math.hypot(max(p["SLV"], p["SLT"]) / 2, min(p["SLV"], p["SLT"]) / 2)
        check("horizontal_slot_inside_radiator", slot_corner_radius < p["DPR"], f"corner_radius={slot_corner_radius}")
        check("vertical_slot_inside_radiator", vertical_corner_radius < p["DPR"], f"corner_radius={vertical_corner_radius}")
        cut_decisions = [item for item in assumptions.get("decisions", []) if str(item.get("symbol") or "").casefold() == "cut"]
        check("CuT_has_engineering_provenance", len(cut_decisions) == 1 and cut_decisions[0].get("paper_evidence") is False, "CuT stays separate from paper evidence")
        return {
            "profile": "leam_case3",
            "passed": all(item["passed"] for item in checks),
            "checks": checks,
            "expected_initial_object_count": 7,
            "expected_final_object_count": 4,
            "expected_boolean_operation_count": 3,
        }

    def _assemble_builder(self, job_id: str, artifacts: dict[str, Any]) -> Path:
        source = (
            "# Generated deterministically from reviewed evidence. Review before execution.\n"
            "# --- model_3d ---\n"
            f"{artifacts['model_3d'].rstrip()}\n\n"
            "# --- boolean ---\n"
            f"{artifacts['boolean'].rstrip()}\n"
        )
        validate_generated_python(source)
        return self.store.write_artifact(job_id, "build_model.py", source)


def _assumption_version(value: Any) -> int:
    try:
        return max(0, int(str(value).rsplit("-v", 1)[1]))
    except (IndexError, TypeError, ValueError):
        return 0


def _assumption_review_packet(
    candidate: Path,
    approved_source: Path,
    source_review_approval_hash: str,
) -> dict[str, Any]:
    entries = []
    for name, path in (
        ("candidate", candidate.resolve()),
        ("approved_source", approved_source.resolve()),
    ):
        data = path.read_bytes()
        entries.append(
            {
                "name": name,
                "path": str(path),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    canonical = json.dumps(
        {
            "artifacts": entries,
            "source_review_approval_hash": source_review_approval_hash,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "artifacts": entries,
        "source_review_approval_hash": source_review_approval_hash,
        "approval_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "instruction": (
            "Review the engineering assumption candidate, then return this exact approval_hash. "
            "Any candidate or approved-source change invalidates it."
        ),
    }


def _validate_assumption_payload(
    payload: dict[str, Any],
    source: dict[str, Any],
    job_id: str,
    source_path: Path,
    source_review_hash: str,
) -> None:
    if payload.get("schema_version") != "1.0" or payload.get("job_id") != job_id:
        raise ValueError("engineering assumption candidate has an invalid schema or job binding")
    base = payload.get("base_source") or {}
    if (
        base.get("artifact") != source_path.name
        or base.get("source_review_approval_hash") != source_review_hash
    ):
        raise PermissionError("engineering assumption candidate has a stale source-review binding")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("engineering assumption candidate must contain at least one decision")
    unresolved = {}
    for parameter in source.get("parameters", []):
        binding = parameter.get("semantic_binding") or {}
        if parameter.get("value") is None and binding.get("mode") == "unresolved":
            unresolved[str(parameter.get("symbol") or "").casefold()] = parameter
    seen: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("every engineering assumption decision must be an object")
        symbol = str(decision.get("symbol") or "")
        folded = symbol.casefold()
        if folded in seen or folded not in unresolved:
            raise PermissionError(
                f"engineering assumptions must uniquely resolve source-unresolved symbols: {symbol!r}"
            )
        seen.add(folded)
        parameter = unresolved[folded]
        binding = parameter.get("semantic_binding") or {}
        value = decision.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"engineering assumption {symbol} must be a finite number")
        if binding.get("quantity") == "thickness" and value <= 0:
            raise ValueError(f"engineering assumption {symbol} must be positive")
        expected_id_prefix = f"{binding.get('claim_id') or symbol}-baseline-v"
        source_claim = decision.get("source_claim") or {}
        if (
            decision.get("classification") != "engineering_assumption"
            or decision.get("paper_evidence") is not False
            or decision.get("unit") != str(parameter.get("unit") or "")
            or decision.get("quantity") != binding.get("quantity")
            or decision.get("optimizable") is not False
            or not str(decision.get("assumption_id") or "").startswith(expected_id_prefix)
            or _assumption_version(decision.get("assumption_id")) < 1
            or source_claim.get("claim_id") != binding.get("claim_id")
            or source_claim.get("original_value") is not None
            or source_claim.get("original_evidence_mode") != "unresolved"
            or len(str(decision.get("rationale") or "").strip()) < 10
        ):
            raise PermissionError(f"engineering assumption metadata is invalid for {symbol}")
