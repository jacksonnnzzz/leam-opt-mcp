from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .workspace import WorkspaceStore
from .evidence_consistency import check_records, check_source


EvidenceStatus = Literal[
    "explicit",
    "derived",
    "assumed",
    "conflicting",
    "missing",
    "not_applicable",
]


@dataclass(frozen=True)
class Criterion:
    id: str
    dimension: str
    weight: float
    label: str


CRITERIA: tuple[Criterion, ...] = (
    Criterion("overall_dimensions", "geometry", 5, "Overall dimensions"),
    Criterion("component_dimensions", "geometry", 8, "Component dimensions"),
    Criterion("component_placement", "geometry", 7, "Component coordinates and placement"),
    Criterion("layer_stackup", "geometry", 5, "Layer stack-up"),
    Criterion("topology_operations", "geometry", 5, "Topology and Boolean operations"),
    Criterion("substrate_permittivity", "materials", 4, "Substrate permittivity"),
    Criterion("substrate_loss_tangent", "materials", 3, "Substrate loss tangent"),
    Criterion("substrate_thickness", "materials", 3, "Substrate thickness"),
    Criterion("conductor_material", "materials", 2, "Conductor material/model"),
    Criterion(
        "conductor_thickness_or_sheet_model",
        "materials",
        3,
        "Conductor thickness or explicit sheet model",
    ),
    Criterion("feed_geometry", "excitation", 5, "Feed geometry"),
    Criterion("port_type", "excitation", 4, "Port or excitation type"),
    Criterion("port_location_plane", "excitation", 3, "Port location and plane"),
    Criterion(
        "port_reference_or_integration_line",
        "excitation",
        3,
        "Port reference conductor or integration line",
    ),
    Criterion("open_boundary_geometry", "solver", 4, "Open-region geometry/clearance"),
    Criterion("boundary_assignments", "solver", 3, "Boundary assignments"),
    Criterion("mesh_controls", "solver", 3, "Mesh controls"),
    Criterion("convergence_criteria", "solver", 3, "Convergence criteria"),
    Criterion("frequency_sweep", "solver", 2, "Frequency sweep"),
    Criterion("target_resonances", "validation", 4, "Target resonances"),
    Criterion("target_bands", "validation", 4, "Target pass/notch bands"),
    Criterion("numeric_sparameter_data", "validation", 3, "Numeric S-parameter data"),
    Criterion("radiation_targets", "validation", 2, "Radiation targets"),
    Criterion(
        "measurement_or_reference_curve",
        "validation",
        2,
        "Measured or reference curve",
    ),
    Criterion("figure_table_traceability", "provenance", 4, "Figure/table traceability"),
    Criterion("equation_traceability", "provenance", 2, "Equation traceability"),
    Criterion("ambiguity_disclosure", "provenance", 2, "Explicit ambiguity disclosure"),
    Criterion("cross_source_consistency", "provenance", 2, "Cross-source consistency"),
)

DIMENSION_LABELS = {
    "geometry": "Geometry completeness",
    "materials": "Material completeness",
    "excitation": "Feed and excitation",
    "solver": "Boundary and solver setup",
    "validation": "Validation evidence",
    "provenance": "Evidence traceability",
}

STATUS_FACTORS: dict[str, float] = {
    "explicit": 1.0,
    "derived": 0.75,
    "assumed": 0.25,
    "conflicting": 0.0,
    "missing": 0.0,
}

_VALID_STATUSES = set(STATUS_FACTORS) | {"not_applicable"}
_CRITERIA_BY_ID = {criterion.id: criterion for criterion in CRITERIA}


class ReproducibilityAuditService:
    """Deterministically score whether source evidence can define one HFSS model.

    The LLM may extract evidence status, but it never supplies the score, grade, or
    workflow decision. Those values are computed from the fixed criteria above.
    """

    def __init__(self, store: WorkspaceStore | None = None) -> None:
        self.store = store

    def audit_payload(self, source_analysis: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(source_analysis, dict):
            raise ValueError("source_analysis must be a JSON object")
        check_source(source_analysis)
        evidence = source_analysis.get("reproducibility_evidence")
        if evidence is None:
            records = _infer_records(source_analysis)
            extraction_mode = "deterministic_fallback"
        else:
            records = validate_reproducibility_evidence(evidence)
            extraction_mode = "structured_source_evidence"

        assessed: list[dict[str, Any]] = []
        dimension_totals = {
            name: {"score": 0.0, "maximum": 0.0, "coverage_percent": 0.0}
            for name in DIMENSION_LABELS
        }
        blocking_gaps: list[dict[str, Any]] = []
        assumptions: list[dict[str, Any]] = []

        for criterion in CRITERIA:
            record = records[criterion.id]
            status = record["status"]
            if status == "not_applicable":
                awarded = None
            else:
                factor = STATUS_FACTORS[status]
                awarded = round(criterion.weight * factor, 4)
                dimension_totals[criterion.dimension]["score"] += awarded
                dimension_totals[criterion.dimension]["maximum"] += criterion.weight
            assessed_record = {
                "id": criterion.id,
                "label": criterion.label,
                "dimension": criterion.dimension,
                "weight": criterion.weight,
                "status": status,
                "awarded": awarded,
                "evidence_source": record.get("evidence_source"),
                "detail": record.get("detail", ""),
            }
            assessed.append(assessed_record)
            if status in {"missing", "conflicting"}:
                blocking_gaps.append(
                    {
                        "criterion": criterion.id,
                        "dimension": criterion.dimension,
                        "status": status,
                        "detail": record.get("detail", ""),
                    }
                )
            elif status == "assumed":
                assumptions.append(
                    {
                        "criterion": criterion.id,
                        "detail": record.get("detail", ""),
                    }
                )

        raw_score = sum(
            float(record["awarded"])
            for record in assessed
            if record["awarded"] is not None
        )
        active_maximum = sum(
            float(record["weight"])
            for record in assessed
            if record["awarded"] is not None
        )
        score = round(100.0 * raw_score / active_maximum, 2) if active_maximum else 0.0
        for name, totals in dimension_totals.items():
            maximum = float(totals["maximum"])
            totals["score"] = round(float(totals["score"]), 2)
            totals["maximum"] = round(maximum, 2)
            totals["coverage_percent"] = (
                round(100.0 * float(totals["score"]) / maximum, 2) if maximum else None
            )
            totals["label"] = DIMENSION_LABELS[name]

        grade, decision = _classify(score)
        status_counts = {
            status: sum(1 for record in assessed if record["status"] == status)
            for status in sorted(_VALID_STATUSES)
        }
        return {
            "schema_version": "1.0",
            "consistency_checks_version": "1.0",
            "source_citations_verified": False,
            "method": "fixed_weight_evidence_coverage",
            "extraction_mode": extraction_mode,
            "score": score,
            "grade": grade,
            "decision": decision,
            "thresholds": {
                "A_direct_reconstruction": 85,
                "B_conditional_reconstruction": 65,
                "C_candidate_only": 0,
            },
            "dimension_scores": dimension_totals,
            "status_counts": status_counts,
            "criteria": assessed,
            "blocking_gaps": blocking_gaps,
            "engineering_assumptions": assumptions,
            "workflow_policy": {
                "candidate_codegen_allowed": True,
                "source_review_required": True,
                "assumption_approval_required": grade in {"B", "C"},
                "automatic_hfss_execution_recommended": grade == "A",
                "automatic_optimization_recommended": grade == "A",
                "electromagnetic_reproduction_claim_allowed": False,
                "claim_note": (
                    "A source score measures specification completeness only. "
                    "An electromagnetic reproduction claim still requires a converged "
                    "same-version simulation that passes the frozen paper targets."
                ),
            },
        }

    def audit_file(self, source_path: str | Path) -> dict[str, Any]:
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(str(source_path))
        payload = json.loads(path.read_text("utf-8"))
        return self.audit_payload(payload)

    def render_markdown(self, report: dict[str, Any]) -> str:
        lines = [
            "# Source reproducibility assessment",
            "",
            f"- Score: **{report['score']}/100**",
            f"- Grade: **{report['grade']}**",
            f"- Decision: `{report['decision']}`",
            f"- Evidence mode: `{report['extraction_mode']}`",
            "",
            "## Dimension scores",
            "",
            "| Dimension | Score | Coverage |",
            "|---|---:|---:|",
        ]
        for name, values in report["dimension_scores"].items():
            coverage = values["coverage_percent"]
            coverage_text = "N/A" if coverage is None else f"{coverage:.2f}%"
            lines.append(
                f"| {values['label']} | {values['score']:.2f}/{values['maximum']:.2f} | {coverage_text} |"
            )
        lines.extend(["", "## Blocking gaps", ""])
        gaps = report["blocking_gaps"]
        if gaps:
            for gap in gaps:
                detail = gap["detail"] or "No source-backed value was found."
                lines.append(
                    f"- `{gap['criterion']}` ({gap['status']}): {detail}"
                )
        else:
            lines.append("- None at the source-completeness stage.")
        lines.extend(["", "## Workflow policy", ""])
        policy = report["workflow_policy"]
        lines.extend(
            [
                f"- Candidate code generation allowed: `{str(policy['candidate_codegen_allowed']).lower()}`",
                f"- Source review required: `{str(policy['source_review_required']).lower()}`",
                f"- Engineering-assumption approval required: `{str(policy['assumption_approval_required']).lower()}`",
                f"- Automatic HFSS execution recommended: `{str(policy['automatic_hfss_execution_recommended']).lower()}`",
                f"- Automatic optimization recommended: `{str(policy['automatic_optimization_recommended']).lower()}`",
                "- Electromagnetic reproduction claim allowed at this stage: `false`",
                "",
                policy["claim_note"],
                "",
            ]
        )
        return "\n".join(lines)

    def audit_job(self, job_id: str) -> dict[str, Any]:
        if self.store is None:
            raise RuntimeError("audit_job requires a workspace store")
        state = self.store.load_state(job_id)
        raw = state.artifacts.get("source_analysis_approved") or state.artifacts.get(
            "source_analysis"
        )
        if not raw:
            raise ValueError("the job has no source_analysis artifact")
        source_path = Path(raw).expanduser().resolve()
        job_dir = self.store.job_dir(job_id).resolve()
        if source_path.parent != job_dir or not source_path.is_file():
            raise PermissionError("source_analysis is missing or outside the modeling job")
        report = self.audit_file(source_path)
        report["job_id"] = job_id
        report["source_artifact"] = str(source_path)
        output = self.store.write_artifact(
            job_id,
            "reproducibility_assessment.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        markdown = self.store.write_artifact(
            job_id,
            "reproducibility_report.md",
            self.render_markdown(report),
        )
        state.artifacts["reproducibility_assessment"] = str(output)
        state.artifacts["reproducibility_report"] = str(markdown)
        self.store.save_state(state)
        return {
            **report,
            "assessment": str(output),
            "report": str(markdown),
        }


def _classify(score: float) -> tuple[str, str]:
    if score >= 85:
        return "A", "direct_reconstruction"
    if score >= 65:
        return "B", "conditional_reconstruction"
    return "C", "candidate_only"


def validate_reproducibility_evidence(payload: object) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("source_analysis.reproducibility_evidence must be an object")
    records = payload.get("criteria")
    if not isinstance(records, list):
        raise ValueError("source_analysis.reproducibility_evidence.criteria must be an array")
    result: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        path = f"source_analysis.reproducibility_evidence.criteria[{index}]"
        if not isinstance(record, dict):
            raise ValueError(f"{path} must be an object")
        criterion_id = record.get("id")
        if criterion_id not in _CRITERIA_BY_ID:
            raise ValueError(f"{path}.id is unknown: {criterion_id!r}")
        if criterion_id in result:
            raise ValueError(f"{path}.id is duplicated: {criterion_id!r}")
        status = record.get("status")
        if status not in _VALID_STATUSES:
            raise ValueError(f"{path}.status is invalid: {status!r}")
        evidence_source = record.get("evidence_source")
        detail = record.get("detail", "")
        if evidence_source is not None and not isinstance(evidence_source, str):
            raise ValueError(f"{path}.evidence_source must be null or a string")
        if not isinstance(detail, str):
            raise ValueError(f"{path}.detail must be a string")
        if status not in {"missing"} and not (
            isinstance(evidence_source, str) and evidence_source.strip()
        ):
            raise ValueError(f"{path}.evidence_source is required for status {status!r}")
        result[criterion_id] = {
            "status": status,
            "evidence_source": evidence_source,
            "detail": detail,
        }
    check_records(records)
    for criterion in CRITERIA:
        result.setdefault(
            criterion.id,
            {
                "status": "missing",
                "evidence_source": None,
                "detail": "No structured evidence record was supplied.",
            },
        )
    return result


def _infer_records(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Conservative compatibility path for source artifacts created before schema 1.0."""

    records = {
        criterion.id: {
            "status": "missing",
            "evidence_source": None,
            "detail": "Not found by the deterministic compatibility audit.",
        }
        for criterion in CRITERIA
    }
    components = [item for item in source.get("components", []) if isinstance(item, dict)]
    parameters = [item for item in source.get("parameters", []) if isinstance(item, dict)]
    operations = [item for item in source.get("operations", []) if isinstance(item, dict)]
    relations = [item for item in source.get("derived_relations", []) if isinstance(item, dict)]
    uncertainties = [item for item in source.get("uncertainties", []) if isinstance(item, str)]
    searchable = json.dumps(source, ensure_ascii=False).casefold()

    def set_record(
        criterion_id: str,
        status: EvidenceStatus,
        evidence_source: str,
        detail: str,
    ) -> None:
        records[criterion_id] = {
            "status": status,
            "evidence_source": evidence_source,
            "detail": detail,
        }

    dimensional = [
        item
        for item in parameters
        if str(item.get("unit", "")).casefold() in {"mm", "cm", "m", "um", "µm", "in"}
    ]
    if dimensional:
        set_record(
            "overall_dimensions",
            "explicit",
            "source_analysis.parameters",
            f"Found {len(dimensional)} dimensional parameters.",
        )
    structured_geometry = [
        item
        for item in components
        if isinstance(item.get("geometric_evidence"), dict)
        and bool(item.get("geometric_evidence"))
    ]
    geometry_ratio = len(structured_geometry) / len(components) if components else 0.0
    if geometry_ratio >= 0.9:
        status: EvidenceStatus = "explicit"
    elif geometry_ratio >= 0.5:
        status = "derived"
    elif structured_geometry:
        status = "assumed"
    else:
        status = "missing"
    if status != "missing":
        set_record(
            "component_dimensions",
            status,
            "source_analysis.components[*].geometric_evidence",
            f"Structured geometry is present for {len(structured_geometry)}/{len(components)} components.",
        )
    coordinate_system = source.get("coordinate_system")
    coordinate_complete = isinstance(coordinate_system, dict) and all(
        coordinate_system.get(key) not in (None, "", []) for key in ("plane", "origin", "axes")
    )
    if coordinate_complete and structured_geometry:
        set_record(
            "component_placement",
            "explicit" if geometry_ratio >= 0.9 else "derived",
            "source_analysis.coordinate_system and components[*].geometric_evidence",
            "A coordinate system and coordinate-based component evidence are present.",
        )
    role_text = " ".join(str(item.get("role", "")) for item in components).casefold()
    stackup_roles = sum(
        token in role_text for token in ("ground", "dielectric", "substrate", "signal")
    )
    if stackup_roles >= 3:
        set_record(
            "layer_stackup",
            "explicit" if geometry_ratio >= 0.9 else "derived",
            "source_analysis.components",
            "Ground/dielectric/signal layer roles are represented.",
        )
    if operations:
        set_record(
            "topology_operations",
            "explicit",
            "source_analysis.operations",
            f"Found {len(operations)} ordered operations.",
        )

    _infer_keyword_parameter(
        records,
        parameters,
        "substrate_permittivity",
        ("permittivity", "dielectric constant", "epsilon", "epsilon_r", "relative permittivity"),
    )
    _infer_keyword_parameter(
        records,
        parameters,
        "substrate_loss_tangent",
        ("loss tangent", "loss_tangent", "tan delta", "tan(delta)"),
    )
    _infer_keyword_parameter(
        records,
        parameters,
        "substrate_thickness",
        ("substrate thickness", "dielectric layer thickness", "substrate_thickness"),
    )
    material_names = {
        str(item.get("material", "")).strip().casefold()
        for item in components
        if item.get("material")
    }
    if material_names & {"copper", "pec", "aluminum", "aluminium", "gold", "silver"}:
        set_record(
            "conductor_material",
            "explicit",
            "source_analysis.components[*].material",
            "An explicit conductor material or PEC sheet model is present.",
        )
    _infer_keyword_parameter(
        records,
        parameters,
        "conductor_thickness_or_sheet_model",
        ("copper thickness", "conductor thickness", "ground thickness", "signal thickness"),
    )
    if records["conductor_thickness_or_sheet_model"]["status"] == "missing" and "pec" in material_names:
        set_record(
            "conductor_thickness_or_sheet_model",
            "derived",
            "source_analysis.components[*].material",
            "PEC is explicitly represented; the compatibility audit treats it as a sheet-model declaration.",
        )

    feed_components = [
        item
        for item in components
        if any(token in str(item.get("role", "")).casefold() for token in ("feed", "probe", "coax", "port"))
    ]
    if feed_components:
        feed_geometry_count = sum(
            isinstance(item.get("geometric_evidence"), dict) and bool(item["geometric_evidence"])
            for item in feed_components
        )
        set_record(
            "feed_geometry",
            "explicit" if feed_geometry_count == len(feed_components) else "derived",
            "source_analysis.components",
            f"Found {len(feed_components)} feed/excitation components.",
        )
    operation_text = json.dumps(operations, ensure_ascii=False).casefold()
    if any(token in operation_text for token in ("wave_port", "lumped_port", "assign_port")):
        set_record(
            "port_type",
            "explicit",
            "source_analysis.operations",
            "An explicit port-assignment operation is present.",
        )
    if any(token in searchable for token in ("port_plane", "port plane", "source_face")):
        set_record(
            "port_location_plane",
            "explicit",
            "source_analysis geometric/derived evidence",
            "An explicit port plane or source face is present.",
        )
    if any(token in operation_text for token in ("reference", "integration_line")):
        set_record(
            "port_reference_or_integration_line",
            "explicit",
            "source_analysis.operations",
            "A reference conductor or integration line is present.",
        )

    open_components = [
        item for item in components if "open_region" in str(item.get("role", "")).casefold()
    ]
    if open_components:
        status = (
            "explicit"
            if all(isinstance(item.get("geometric_evidence"), dict) for item in open_components)
            else "derived"
        )
        set_record(
            "open_boundary_geometry",
            status,
            "source_analysis.components",
            "An open-region component is present.",
        )
    if any(item.get("boundary") for item in components) or "assign_radiation" in operation_text:
        set_record(
            "boundary_assignments",
            "explicit",
            "source_analysis.components/operations",
            "At least one boundary assignment is explicit.",
        )
    _infer_text_record(records, "mesh_controls", searchable, ("mesh", "maximum length"))
    _infer_text_record(records, "convergence_criteria", searchable, ("delta s", "convergence", "adaptive pass"))
    _infer_text_record(records, "frequency_sweep", searchable, ("sweep1", "frequency sweep", "interpolating sweep"))
    _infer_text_record(records, "target_resonances", searchable, ("target resonance", "resonance at", "resonant frequency"))
    _infer_text_record(records, "target_bands", searchable, ("target band", "-10 db band", "passband", "notch band"))
    _infer_text_record(records, "numeric_sparameter_data", searchable, ("numeric s11", "s11 csv", "s-parameter data"))
    _infer_text_record(records, "radiation_targets", searchable, ("gain", "directivity", "radiation efficiency"))
    _infer_text_record(records, "measurement_or_reference_curve", searchable, ("measured curve", "reference curve", "measured s11"))

    evidence_sources = [
        str(item.get("evidence_source", "")).strip()
        for item in parameters
        if str(item.get("evidence_source", "")).strip()
    ]
    if evidence_sources:
        set_record(
            "figure_table_traceability",
            "explicit" if len(evidence_sources) == len(parameters) else "derived",
            "source_analysis.parameters[*].evidence_source",
            f"Evidence sources are present for {len(evidence_sources)}/{len(parameters)} parameters.",
        )
    relation_evidence = [item for item in relations if item.get("evidence")]
    if relation_evidence:
        set_record(
            "equation_traceability",
            "explicit",
            "source_analysis.derived_relations[*].evidence",
            f"Found {len(relation_evidence)} traced derived relations.",
        )
    if uncertainties:
        set_record(
            "ambiguity_disclosure",
            "explicit",
            "source_analysis.uncertainties",
            f"Found {len(uncertainties)} disclosed uncertainties.",
        )
    unique_sources = {source.casefold() for source in evidence_sources}
    if len(unique_sources) >= 2:
        set_record(
            "cross_source_consistency",
            "derived",
            "source_analysis.parameters[*].evidence_source",
            f"The artifact cites {len(unique_sources)} distinct evidence-source strings.",
        )
    return records


def _infer_keyword_parameter(
    records: dict[str, dict[str, Any]],
    parameters: list[dict[str, Any]],
    criterion_id: str,
    keywords: tuple[str, ...],
) -> None:
    for item in parameters:
        text = " ".join(
            str(item.get(field, ""))
            for field in ("symbol", "geometric_meaning", "evidence_source")
        ).casefold()
        if any(keyword in text for keyword in keywords) and item.get("value") is not None:
            records[criterion_id] = {
                "status": "explicit",
                "evidence_source": str(item.get("evidence_source", "source_analysis.parameters")),
                "detail": f"Parameter {item.get('symbol')!r} supplies a numeric value.",
            }
            return


def _infer_text_record(
    records: dict[str, dict[str, Any]],
    criterion_id: str,
    searchable: str,
    keywords: tuple[str, ...],
) -> None:
    matched = next((keyword for keyword in keywords if keyword in searchable), None)
    if matched:
        records[criterion_id] = {
            "status": "derived",
            "evidence_source": "source_analysis text compatibility scan",
            "detail": f"Found the evidence term {matched!r}; migrate it to structured reproducibility_evidence.",
        }


def reproducibility_prompt_contract() -> str:
    """Return the exact criterion IDs for prompt and documentation reuse."""

    ids = ", ".join(criterion.id for criterion in CRITERIA)
    return (
        "reproducibility_evidence must be an object with a criteria array containing "
        "one record for every ID below. Each record has id, status, evidence_source, "
        "and detail. status is explicit, derived, assumed, conflicting, missing, or "
        "not_applicable. Use missing with a null evidence_source when the paper is silent; "
        "never mark a common engineering default as explicit paper evidence. Criterion IDs: "
        + ids
        + ". Do not calculate a score or grade; the application does that deterministically."
    )


def criterion_weight_total() -> float:
    return sum(criterion.weight for criterion in CRITERIA)
