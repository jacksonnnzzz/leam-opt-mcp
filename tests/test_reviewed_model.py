import ast
import hashlib
import json
from pathlib import Path

import pytest

from antenna_mcp.execution import HfssBuildService
from antenna_mcp.reviewed_model import EngineeringAssumptionService, ReviewedModelCompiler
from antenna_mcp.workspace import WorkspaceStore


def _approved_source():
    values = {
        "DPR": (6.58, "mm", "radius", "visual"),
        "SW": (13.43, "mm", "width", "visual"),
        "SLT": (1.0, "mm", "width", "visual"),
        "SLV": (7.9, "mm", "length", "visual"),
        "SLH": (7.9, "mm", "length", "visual"),
        "ML": (25.08, "mm", "offset", "visual"),
        "RPL": (6.67, "mm", "gap", "visual"),
        "MW": (1.2, "mm", "width", "visual"),
        "MG": (0.3, "mm", "gap", "visual"),
        "SL": (31.86, "mm", "length", "visual"),
        "RPW": (5.815, "mm", "width", "visual"),
        "SubT": (0.8, "mm", "thickness", "text"),
        "eps_r": (4.4, "", "material_property", "text"),
        "tan_delta": (0.02, "", "material_property", "text"),
        "CuT": (None, "mm", "thickness", "unresolved"),
    }
    components = [
        ("substrate", "box", "FR-4", "dielectric", "volume"),
        ("radiator", "cylinder", "copper (pure)", "conductor", "top_coplanar"),
        ("feedline", "box", "copper (pure)", "conductor", "top_coplanar"),
        ("left_ground", "box", "copper (pure)", "conductor", "top_coplanar"),
        ("right_ground", "box", "copper (pure)", "conductor", "top_coplanar"),
        ("horizontal_slot", "box", "vacuum", "void", "subtraction"),
        ("vertical_slot", "box", "vacuum", "void", "subtraction"),
    ]
    return {
        "input_summary": "LEAM Case 3",
        "antenna_type": "quasi-cross-slotted printed monopole",
        "coordinate_system": {
            "plane": "XY-plane",
            "origin": [0, 0, 0],
            "axes": [
                "x-axis: horizontal direction (width)",
                "y-axis: vertical direction (length)",
                "z-axis: upward through substrate thickness",
            ],
        },
        "components": [
            {
                "name": name,
                "role": name,
                "primitive": primitive,
                "material": material,
                "geometric_evidence": "reviewed",
                "confidence": 0.99,
                "evidence_binding": {
                    "primitive_class": primitive,
                    "material_class": material_class,
                    "layer_class": layer_class,
                },
            }
            for name, primitive, material, material_class, layer_class in components
        ],
        "parameters": [
            {
                "symbol": symbol,
                "value": value,
                "unit": unit,
                "geometric_meaning": quantity,
                "evidence_source": mode,
                "confidence": 0.99 if value is not None else 0.4,
                "semantic_binding": {
                    "mode": mode,
                    "claim_id": {
                        "eps_r": "case3-eps-r",
                        "tan_delta": "case3-tan-delta",
                    }.get(symbol, f"case3-{symbol}"),
                    "quantity": quantity,
                },
            }
            for symbol, (value, unit, quantity, mode) in values.items()
        ],
        "operations": [
            {"operation": "unite", "target": "radiator", "operands": ["feedline"], "order": 1},
            {
                "operation": "unite",
                "target": "horizontal_slot",
                "operands": ["vertical_slot"],
                "order": 2,
            },
            {
                "operation": "subtract",
                "target": "radiator",
                "operands": ["horizontal_slot"],
                "order": 3,
            },
        ],
        "derived_relations": [
            {
                "claim_id": "case3-relation-SL",
                "expression": "SL = ML + DPR + 0.2",
                "symbols": ["SL", "ML", "DPR"],
            },
            {
                "claim_id": "case3-relation-RPW",
                "expression": "RPW = (SW - MW - 2*MG) / 2",
                "symbols": ["RPW", "SW", "MW", "MG"],
            },
            {
                "claim_id": "case3-relation-ground-length",
                "expression": "ground_length = ML - RPL",
                "symbols": ["ML", "RPL"],
            },
        ],
        "uncertainties": [],
    }


def _job(tmp_path):
    store = WorkspaceStore(tmp_path)
    state = store.create_job("modeling", {"description": "Reconstruct reviewed LEAM Case 3 antenna."})
    source_text = json.dumps(_approved_source(), ensure_ascii=False, indent=2) + "\n"
    candidate = store.write_artifact(
        state.job_id,
        "source_analysis_candidate.json",
        source_text,
    )
    report = store.write_artifact(
        state.job_id,
        "source_refinement_report.json",
        json.dumps({"quality_gate_passed": True}) + "\n",
    )
    source = store.write_artifact(
        state.job_id,
        "source_analysis_approved.json",
        source_text,
    )
    entries = []
    for name, path in (("candidate", candidate), ("report", report)):
        data = path.read_bytes()
        entries.append(
            {
                "name": name,
                "path": str(path),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    packet = store.write_artifact(
        state.job_id,
        "source_review_packet.json",
        json.dumps(
            {"artifacts": entries, "approval_hash": hashlib.sha256(canonical.encode()).hexdigest()}
        )
        + "\n",
    )
    state.status = "completed"
    state.artifacts = {
        "source_analysis_approved": str(source),
        "source_analysis_candidate": str(candidate),
        "source_refinement_report": str(report),
        "source_review_packet": str(packet),
    }
    store.save_state(state)
    return store, state, source


def _record_cut(store, job_id):
    service = EngineeringAssumptionService(store)
    proposal = service.prepare(
        job_id,
        "CuT",
        0.035,
        "mm",
        "35 um copper selected as a reproducible approximately 1 oz PCB baseline.",
    )
    return service.approve(job_id, proposal["approval_hash"])


def _compile(store, job_id, profile="auto"):
    receipt_path = Path(store.load_state(job_id).artifacts["engineering_assumptions_receipt"])
    assumption_hash = json.loads(receipt_path.read_text("utf-8"))["assumption_approval_hash"]
    return ReviewedModelCompiler(store).compile(job_id, profile, assumption_hash)


def test_assumption_is_separate_and_cannot_override_source_evidence(tmp_path):
    store, state, source = _job(tmp_path)
    original = source.read_bytes()
    result = _record_cut(store, state.job_id)

    assert source.read_bytes() == original
    payload = json.loads(Path(result["approved"]).read_text("utf-8"))
    assert payload["decisions"][0]["paper_evidence"] is False
    assert payload["decisions"][0]["value"] == 0.035
    assert _approved_source()["parameters"][-1]["value"] is None

    with pytest.raises(ValueError, match="null/unresolved"):
        EngineeringAssumptionService(store).prepare(
            state.job_id,
            "DPR",
            8.0,
            "mm",
            "Attempt to overwrite a visually reviewed source dimension.",
        )


def test_assumption_requires_exact_candidate_hash_round_trip(tmp_path):
    store, state, _ = _job(tmp_path)
    service = EngineeringAssumptionService(store)
    proposal = service.prepare(
        state.job_id,
        "CuT",
        0.035,
        "mm",
        "A reproducible one ounce copper baseline is required.",
    )
    assert proposal["status"] == "awaiting_review"
    with pytest.raises(PermissionError, match="approval hash"):
        service.approve(state.job_id, "0" * 64)


def test_candidate_cannot_compile_before_hash_approval(tmp_path):
    store, state, _ = _job(tmp_path)
    EngineeringAssumptionService(store).prepare(
        state.job_id,
        "CuT",
        0.035,
        "mm",
        "A reproducible one ounce copper baseline is required.",
    )
    with pytest.raises(ValueError, match="completed modeling job"):
        ReviewedModelCompiler(store).compile(state.job_id, "auto", "0" * 64)


def test_assumption_approval_revalidates_candidate_semantics(tmp_path):
    store, state, _ = _job(tmp_path)
    service = EngineeringAssumptionService(store)
    proposal = service.prepare(
        state.job_id,
        "CuT",
        0.035,
        "mm",
        "A reproducible one ounce copper baseline is required.",
    )
    candidate_path = Path(proposal["candidate"])
    candidate = json.loads(candidate_path.read_text("utf-8"))
    candidate["decisions"][0]["value"] = -0.035
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    current = store.load_state(state.job_id)
    source_path = Path(current.artifacts["source_analysis_approved"])
    from antenna_mcp.reviewed_model import _assumption_review_packet

    source_review_hash = json.loads(
        Path(current.artifacts["source_review_packet"]).read_text("utf-8")
    )["approval_hash"]
    changed_hash = _assumption_review_packet(
        candidate_path,
        source_path,
        source_review_hash,
    )["approval_hash"]
    with pytest.raises(ValueError, match="must be positive"):
        service.approve(state.job_id, changed_hash)


def test_compile_requires_the_user_returned_assumption_hash(tmp_path):
    store, state, _ = _job(tmp_path)
    approval = _record_cut(store, state.job_id)

    with pytest.raises(PermissionError, match="user-returned"):
        ReviewedModelCompiler(store).compile(state.job_id)
    with pytest.raises(PermissionError, match="supplied engineering assumption"):
        ReviewedModelCompiler(store).compile(state.job_id, "auto", "0" * 64)

    result = ReviewedModelCompiler(store).compile(
        state.job_id,
        "auto",
        approval["approval_hash"],
    )
    assert result["status"] == "awaiting_artifact_review"


def test_reviewed_case3_compiler_generates_validated_hash_frozen_artifacts(tmp_path):
    store, state, _ = _job(tmp_path)
    _record_cut(store, state.job_id)
    result = _compile(store, state.job_id, "leam_case3")

    assert result["status"] == "awaiting_artifact_review"
    assert result["validation"]["passed"] is True
    parameters = json.loads(Path(result["artifacts"]["parameters"]).read_text("utf-8"))["parameters"]
    cut = next(item for item in parameters if item["name"] == "CuT")
    assert cut["value"] == 0.035
    assert cut["provenance"]["kind"] == "engineering_assumption"
    sl = next(item for item in parameters if item["name"] == "SL")
    assert sl["expression"] == "ML+DPR+0.2mm"
    assert "unite([\"radiator\", \"feedline\"])" in Path(
        result["artifacts"]["boolean"]
    ).read_text("utf-8")
    stages = {item["stage"] for item in result["review"]["artifacts"]}
    assert "engineering_assumptions_approved" in stages
    assert "model_3d" in stages
    assert "boolean" in stages


def test_compiler_rejects_assumptions_after_source_changes(tmp_path):
    store, state, source = _job(tmp_path)
    _record_cut(store, state.job_id)
    source.write_text(source.read_text("utf-8") + " ", encoding="utf-8")

    with pytest.raises(PermissionError, match="no longer match"):
        _compile(store, state.job_id)


def test_source_approval_chain_is_rechecked_before_recording_assumption(tmp_path):
    store, state, source = _job(tmp_path)
    source.write_text(source.read_text("utf-8") + " ", encoding="utf-8")

    with pytest.raises(PermissionError, match="approved no longer matches"):
        _record_cut(store, state.job_id)


def test_compiler_rejects_assumption_tampered_after_confirmation(tmp_path):
    store, state, _ = _job(tmp_path)
    recorded = _record_cut(store, state.job_id)
    assumption_path = Path(recorded["approved"])
    payload = json.loads(assumption_path.read_text("utf-8"))
    payload["decisions"][0]["value"] = 0.7
    assumption_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PermissionError, match="hash-approved candidate"):
        _compile(store, state.job_id)


def test_source_review_packet_cannot_drop_required_report(tmp_path):
    store, state, _ = _job(tmp_path)
    packet_path = Path(state.artifacts["source_review_packet"])
    packet = json.loads(packet_path.read_text("utf-8"))
    packet["artifacts"] = [item for item in packet["artifacts"] if item["name"] != "report"]
    canonical = json.dumps(packet["artifacts"], sort_keys=True, separators=(",", ":"))
    packet["approval_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    with pytest.raises(PermissionError, match="artifact set"):
        _record_cut(store, state.job_id)


def test_compiler_requires_exact_source_relation_contract(tmp_path):
    store, state, source = _job(tmp_path)
    payload = json.loads(source.read_text("utf-8"))
    payload["derived_relations"][0]["expression"] = "SL = ML + DPR"
    changed = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    source.write_text(changed, encoding="utf-8")
    Path(state.artifacts["source_analysis_candidate"]).write_text(changed, encoding="utf-8")
    packet_path = Path(state.artifacts["source_review_packet"])
    packet = json.loads(packet_path.read_text("utf-8"))
    for entry in packet["artifacts"]:
        if entry["name"] == "candidate":
            data = Path(entry["path"]).read_bytes()
            entry["size"] = len(data)
            entry["sha256"] = hashlib.sha256(data).hexdigest()
    canonical = json.dumps(packet["artifacts"], sort_keys=True, separators=(",", ":"))
    packet["approval_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    _record_cut(store, state.job_id)

    with pytest.raises(ValueError, match="derived relation mismatch"):
        _compile(store, state.job_id)


def test_deterministic_compile_drops_stale_executable_stages(tmp_path):
    store, state, _ = _job(tmp_path)
    _record_cut(store, state.job_id)
    stale_2d = store.write_artifact(state.job_id, "model_2d.py", "danger = 'stale'\n")
    stale_setup = store.write_artifact(
        state.job_id, "simulation_setup.py", "danger = 'stale simulation'\n"
    )
    current = store.load_state(state.job_id)
    current.artifacts["model_2d"] = str(stale_2d)
    current.artifacts["simulation_setup"] = str(stale_setup)
    store.save_state(current)

    result = _compile(store, state.job_id)
    final = store.load_state(state.job_id)
    review_stages = {item["stage"] for item in result["review"]["artifacts"]}

    assert "model_2d" not in final.artifacts
    assert "simulation_setup" not in final.artifacts
    assert "model_2d" not in review_stages
    assert "simulation_setup" not in review_stages


def test_build_contract_rejects_parameter_that_differs_from_assumption(tmp_path):
    store, state, _ = _job(tmp_path)
    _record_cut(store, state.job_id)
    result = _compile(store, state.job_id)
    parameters_path = Path(result["artifacts"]["parameters"])
    payload = json.loads(parameters_path.read_text("utf-8"))
    next(item for item in payload["parameters"] if item["name"] == "CuT")["value"] = 0.036
    parameters_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PermissionError, match="differs"):
        HfssBuildService._validate_reviewed_contract(store.load_state(state.job_id))


@pytest.mark.parametrize("mutation", ["value", "unit", "provenance", "claim_id"])
def test_build_contract_rejects_source_parameter_mapping_drift(tmp_path, mutation):
    store, state, _ = _job(tmp_path)
    _record_cut(store, state.job_id)
    result = _compile(store, state.job_id)
    parameters_path = Path(result["artifacts"]["parameters"])
    payload = json.loads(parameters_path.read_text("utf-8"))
    parameter = next(item for item in payload["parameters"] if item["name"] == "DPR")
    if mutation == "value":
        parameter["value"] = 6.59
    elif mutation == "unit":
        parameter["unit"] = "cm"
    elif mutation == "provenance":
        parameter["provenance"]["kind"] = "derived_relation"
    else:
        parameter["provenance"]["claim_id"] = "case3-DPR-unreviewed"
    parameters_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PermissionError, match="DPR"):
        HfssBuildService._validate_reviewed_contract(store.load_state(state.job_id))


def test_build_contract_recomputes_derived_parameter_relations(tmp_path):
    store, state, _ = _job(tmp_path)
    _record_cut(store, state.job_id)
    result = _compile(store, state.job_id)
    parameters_path = Path(result["artifacts"]["parameters"])
    payload = json.loads(parameters_path.read_text("utf-8"))
    next(item for item in payload["parameters"] if item["name"] == "ground_length")[
        "value"
    ] = 18.0
    parameters_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PermissionError, match="ground_length_relation"):
        HfssBuildService._validate_reviewed_contract(store.load_state(state.job_id))


def test_case3_geometry_checks_are_recomputed_from_live_parameters(tmp_path):
    store, state, _ = _job(tmp_path)
    _record_cut(store, state.job_id)
    result = _compile(store, state.job_id)
    payload = json.loads(Path(result["artifacts"]["parameters"]).read_text("utf-8"))
    parameters = {item["name"]: item for item in payload["parameters"]}
    parameters["DPR"]["value"] = 7.0
    parameters["SL"]["value"] = parameters["ML"]["value"] + 7.0 + 0.2

    with pytest.raises(PermissionError, match="radiator_x_inside_board"):
        HfssBuildService._recompute_case3_parameter_geometry(parameters)


def test_build_contract_reconstructs_source_review_with_visual_evidence(tmp_path):
    store, state, _ = _job(tmp_path)
    audit_path = store.write_artifact(state.job_id, "source_visual_audit.json", "{}\n")
    visual_paths = [
        store.write_binary_artifact(
            state.job_id,
            f"source_visual_input_{index}_page_7.png",
            f"visual-{index}".encode(),
        )
        for index in range(1, 4)
    ]
    current = store.load_state(state.job_id)
    current.artifacts["source_visual_audit"] = str(audit_path)
    for index, path in enumerate(visual_paths, start=1):
        current.artifacts[f"source_visual_input_{index}"] = str(path)
    packet_path = Path(current.artifacts["source_review_packet"])
    packet = json.loads(packet_path.read_text("utf-8"))
    for name, path in [
        ("visual_audit", audit_path),
        *((f"visual_input_{index}", path) for index, path in enumerate(visual_paths, start=1)),
    ]:
        data = path.read_bytes()
        packet["artifacts"].append(
            {
                "name": name,
                "path": str(path),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    canonical = json.dumps(packet["artifacts"], sort_keys=True, separators=(",", ":"))
    packet["approval_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    store.save_state(current)

    _record_cut(store, state.job_id)
    _compile(store, state.job_id)
    HfssBuildService._validate_reviewed_contract(store.load_state(state.job_id))


def test_build_contract_rejects_legacy_self_attested_assumption_receipt(tmp_path):
    store, state, _ = _job(tmp_path)
    _record_cut(store, state.job_id)
    _compile(store, state.job_id)
    current = store.load_state(state.job_id)
    receipt_path = Path(current.artifacts["engineering_assumptions_receipt"])
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt.pop("approval_method")
    receipt["confirmation"] = "interactive_user_confirmation"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(PermissionError, match="content-hash approval"):
        HfssBuildService._validate_reviewed_contract(store.load_state(state.job_id))


class _FakeProperty:
    def __init__(self, value=1.0):
        self.value = value


class _FakeMaterial:
    def __init__(self):
        self._permittivity = _FakeProperty()
        self._loss = _FakeProperty(0.0)

    @property
    def permittivity(self):
        return self._permittivity

    @permittivity.setter
    def permittivity(self, value):
        self._permittivity.value = value

    @property
    def dielectric_loss_tangent(self):
        return self._loss

    @dielectric_loss_tangent.setter
    def dielectric_loss_tangent(self, value):
        self._loss.value = value


class _FakeMaterials:
    def __init__(self):
        self.items = {}

    def add_material(self, name):
        return self.items.setdefault(name, _FakeMaterial())

    def exists_material(self, name):
        return self.items.get(name, False)


class _FakeModeler:
    def __init__(self, variables):
        self.objects = {}
        self.variables = variables
        self.model_units = "mm"

    @property
    def object_names(self):
        return list(self.objects)

    def __getitem__(self, name):
        value = self.objects[name]
        return type(
            "FakeObject",
            (),
            {
                "material_name": value["material"],
                "bounding_box": list(value["bounding_box"]),
            },
        )()

    @property
    def model_consistency_report(self):
        return {"Missing Objects": [], "Non-Existent Objects": []}

    def create_box(self, origin, size, name, material):
        resolved_origin = [self._resolve(value) for value in origin]
        resolved_size = [self._resolve(value) for value in size]
        opposite = [left + right for left, right in zip(resolved_origin, resolved_size)]
        bounding_box = [
            *[min(left, right) for left, right in zip(resolved_origin, opposite)],
            *[max(left, right) for left, right in zip(resolved_origin, opposite)],
        ]
        self.objects[name] = {
            "primitive": "box",
            "material": material,
            "origin": origin,
            "size": size,
            "bounding_box": bounding_box,
        }
        return name

    def create_cylinder(self, orientation, origin, radius, height, name, material):
        assert orientation == "Z"
        center = [self._resolve(value) for value in origin]
        resolved_radius = self._resolve(radius)
        resolved_height = self._resolve(height)
        self.objects[name] = {
            "primitive": "cylinder",
            "material": material,
            "origin": origin,
            "radius": radius,
            "height": height,
            "bounding_box": [
                center[0] - resolved_radius,
                center[1] - resolved_radius,
                min(center[2], center[2] + resolved_height),
                center[0] + resolved_radius,
                center[1] + resolved_radius,
                max(center[2], center[2] + resolved_height),
            ],
        }
        return name

    def unite(self, names):
        target_box = self.objects[names[0]]["bounding_box"]
        for name in names[1:]:
            tool_box = self.objects[name]["bounding_box"]
            target_box = [
                *[min(target_box[index], tool_box[index]) for index in range(3)],
                *[max(target_box[index], tool_box[index]) for index in range(3, 6)],
            ]
            self.objects.pop(name)
        self.objects[names[0]]["bounding_box"] = target_box
        return names[0]

    def subtract(self, blank, tool, keep_originals=False):
        if not keep_originals:
            self.objects.pop(tool)
        return True

    def fit_all(self):
        return True

    def _resolve(self, value, seen=frozenset()):
        if isinstance(value, (int, float)):
            return float(value)
        expression = str(value).replace("mm", "")

        def evaluate(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return float(node.value)
            if isinstance(node, ast.Name):
                if node.id in seen or node.id not in self.variables:
                    raise ValueError(f"unresolved fake AEDT variable: {node.id}")
                return self._resolve(self.variables[node.id], seen | {node.id})
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                operand = evaluate(node.operand)
                return operand if isinstance(node.op, ast.UAdd) else -operand
            if isinstance(node, ast.BinOp):
                left = evaluate(node.left)
                right = evaluate(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return left / right
            raise ValueError(f"unsupported fake AEDT expression: {expression}")

        return evaluate(ast.parse(expression, mode="eval").body)


class _FakeReviewedHfss:
    def __init__(self):
        self.variables = {}
        self.materials = _FakeMaterials()
        self.modeler = _FakeModeler(self.variables)
        self.odesign = object()

    def __setitem__(self, name, value):
        self.variables[name] = value

    def save_project(self, path):
        Path(path).write_text("reviewed aedt", encoding="utf-8")
        return True

    def release_desktop(self, **kwargs):
        return None


def test_reviewed_case3_artifacts_execute_as_one_hash_frozen_build(tmp_path, monkeypatch):
    store, state, _ = _job(tmp_path)
    _record_cut(store, state.job_id)
    compiled = _compile(store, state.job_id)
    fake = _FakeReviewedHfss()
    monkeypatch.setenv("ANTENNA_MCP_ALLOW_SIMULATION", "1")

    built = HfssBuildService(store, lambda **kwargs: fake).build(
        state.job_id,
        project_name="leam_case3.aedt",
        approval_hash=compiled["review"]["approval_hash"],
    )

    assert built.status == "completed"
    assert set(fake.modeler.object_names) == {"substrate", "radiator", "left_ground", "right_ground"}
    assert fake.variables["CuT"] == "0.035mm"
    assert fake.variables["SL"] == "ML+DPR+0.2mm"
    assert Path(built.artifacts["hfss_project"]).is_file()
    report = json.loads(Path(built.artifacts["hfss_build_report"]).read_text("utf-8"))
    assert report["passed"] is True
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["initial_object_bounding_boxes"]["passed"] is True
    assert checks["final_object_bounding_boxes"]["passed"] is True
