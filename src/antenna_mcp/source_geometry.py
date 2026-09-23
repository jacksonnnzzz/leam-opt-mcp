"""Geometry-only output contract shared by decoding and local validation."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Component(StrictRecord):
    # Preserve producer-specific relationships; the existing source validator
    # checks these instead of stripping them while normalizing the response.
    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    primitive: str = Field(min_length=1)
    material: str | None
    geometric_evidence: str | dict[str, Any]
    confidence: float = Field(ge=0, le=1)


class Parameter(StrictRecord):
    symbol: str = Field(min_length=1)
    value: float | str | None
    unit: str = Field(min_length=1)
    geometric_meaning: str = Field(min_length=1)
    evidence_source: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class Coordinates(StrictRecord):
    plane: str | None
    origin: list[float | str] | None = Field(min_length=3, max_length=3)
    axes: list[str] | None = Field(min_length=3, max_length=3)


class Criterion(StrictRecord):
    id: str
    status: Literal["explicit", "derived", "assumed", "missing", "conflicting", "not_applicable"]
    evidence_source: str | None
    detail: str


class Evidence(StrictRecord):
    criteria: list[Criterion]


class GeometryOutput(StrictRecord):
    input_summary: str = Field(min_length=1)
    antenna_type: str | None
    coordinate_system: Coordinates
    components: list[Component]
    parameters: list[Parameter]
    operations: list[dict[str, Any]]
    derived_relations: list[dict[str, Any]]
    uncertainties: list[str | dict[str, Any]]
    reproducibility_evidence: Evidence


class GeometryEntities(StrictRecord):
    input_summary: str = Field(min_length=1)
    antenna_type: str | None
    coordinate_system: Coordinates
    components: list[Component]
    operations: list[dict[str, Any]]
    uncertainties: list[str | dict[str, Any]]


class GeometryParameters(StrictRecord):
    parameters: list[Parameter]
    derived_relations: list[dict[str, Any]]
    uncertainties: list[str | dict[str, Any]]


class GeometryEvidence(StrictRecord):
    reproducibility_evidence: Evidence
    uncertainties: list[str | dict[str, Any]]


GEOMETRY_SUBPARTS = {
    "geometry_entities": GeometryEntities,
    "geometry_parameters": GeometryParameters,
    "geometry_evidence": GeometryEvidence,
}

GEOMETRY_SUBPROMPTS = {
    "geometry_entities": (
        "Extract ONLY physical component identities, coordinate system, and ordered Boolean operations. "
        "One name per physical object/tool, not per figure view; preserve all evidenced objects. "
        "Keep geometric_evidence and explicit producer relationships. Material may be null. "
        "Do not return a parameter table or evidence criteria. Confidence is your extraction estimate. "
        "Unknown coordinates remain null. Disclose entity/topology uncertainties."
    ),
    "geometry_parameters": (
        "Extract ONLY the selected variant's geometry parameters and derived relations. "
        "Use source symbols, units and page/table citations. Existing component identities are fixed; "
        "do not return or rename components. No material properties or simulation results. "
        "Preserve supported values; unreadable values remain null and disclosed. "
        "Derived relations include claim_id, expression, symbols, evidence, confidence."
    ),
    "geometry_evidence": (
        "Judge ONLY the five assigned geometry evidence criteria against original attachments. "
        "Earlier extractions are provisional, not source authority. Return no components or parameters. "
        "Use each assigned criterion exactly once. Cite evidence; do not mark absent facts explicit. "
        "Disclose contradictions with earlier extractions; never silently rewrite them. "
        "An excerpt omission does not prove full-paper absence. No score."
    ),
}


def geometry_subschema(part: str, ids: list[str]) -> dict:
    schema = GEOMETRY_SUBPARTS[part].model_json_schema()
    if part == "geometry_evidence":
        _constrain_criteria(schema, ids)
    return schema


def _constrain_criteria(schema, ids):
    schema["$defs"]["Criterion"]["properties"]["id"] = {"type": "string", "enum": ids}
    schema["$defs"]["Evidence"]["properties"]["criteria"].update(minItems=len(ids), maxItems=len(ids))


def geometry_schema(ids: list[str]) -> dict:
    schema = GeometryOutput.model_json_schema()
    _constrain_criteria(schema, ids)
    return schema


GEOMETRY_PROMPT = """Extract ONLY geometry for the selected antenna variant.
Return one GeometryOutput JSON object. No solver settings, performance values, code or score.
Use one stable unique name per physical component or Boolean tool; a second view of the
same ground/patch is evidence for that entity, not another entity. Distinct actual objects
must remain distinct. Record all evidenced layers, feed geometry and subtraction tools.
Preserve source symbols, units, formulas and geometric evidence; unknown facts stay null
or are disclosed in uncertainties. Do not infer an origin, symmetry or metal thickness.
Material names may be null; material-property extraction belongs to the materials pass.
Preserve explicit producer relationships and ordered Boolean operations if supplied;
do not confuse implementation constants with independent source parameters.
Every component and parameter requires confidence: your numeric extraction estimate
from 0 to 1, not a paper fact. Coordinates origin/axes are length-three arrays or null.
Use only the assigned evidence IDs once each, with page/figure/table citations and detail.
Do not mark an omitted/unreadable fact explicit. Report scope limitations of excerpts;
'not found in the supplied excerpt' is not proof of absence from the full paper.
Return all required fields; do not copy template values or invent facts to fill them."""
