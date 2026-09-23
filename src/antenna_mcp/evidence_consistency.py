"""Conservative, inspectable contradictions; not a substitute for source review."""
from __future__ import annotations

import json
import re


class EvidenceConsistencyError(ValueError):
    def __init__(self, issues: list[dict]):
        self.issues = issues
        super().__init__(json.dumps({"contract": "evidence_consistency", "issues": issues}, ensure_ascii=False))


_ABSENT = re.compile(
    r"\b(?:not\s+(?:specified|provided|reported|given|found|available)|unspecified|unknown)\b"
    r"|未(?:提供|说明|给出|报告|指定)|缺少|未知", re.I
)
_SUBJECTS = {
    "open_boundary_geometry": r"open.boundary|air.region|clearance|空气域|开放边界",
    "boundary_assignments": r"boundary|boundaries|边界",
    "mesh_controls": r"mesh|网格",
    "convergence_criteria": r"converg|收敛",
    "port_location_plane": r"port|端口",
    "port_reference_or_integration_line": r"reference|integration.line|参考|积分线",
    "radiation_targets": r"radiation|gain|efficiency|辐射|增益|效率",
    "conductor_material": r"conductor.material|导体材料",
}


def check_records(records: list[dict]) -> None:
    issues = []
    for record in records:
        if record.get("status") not in {"explicit", "derived"}:
            continue
        detail = record.get("detail", "")
        # Only inspect the leading assertion: later sentences can legitimately
        # disclose a different variant or an implementation-specific limitation.
        first = re.split(r"(?<=[.!?。])\s+|[。；;]", detail, maxsplit=1)[0]
        match = _ABSENT.search(first)
        if not match:
            continue
        criterion = record["id"]
        subject = _SUBJECTS.get(criterion, re.escape(criterion.replace("_", " ")))
        if match.start() == 0 or re.search(subject, first, re.I):
            issues.append({"code": "status_detail_contradiction", "path": f"criteria[{criterion}]",
                           "status": record["status"], "detail": detail})
    if issues:
        raise EvidenceConsistencyError(issues)


def check_source(source: dict) -> None:
    issues = []
    for collection, field in (("components", "name"), ("parameters", "symbol")):
        seen = set()
        for index, item in enumerate(source.get(collection, [])):
            if not isinstance(item, dict) or not isinstance(item.get(field), str):
                continue  # Full schema validation owns invalid shapes.
            key = item[field].strip().lstrip("$").casefold()
            if key in seen:
                issues.append({"code": "duplicate_entity", "path": f"{collection}[{index}].{field}", "value": item[field]})
            seen.add(key)
    evidence = source.get("reproducibility_evidence", {})
    records = evidence.get("criteria", []) if isinstance(evidence, dict) else []
    statuses = {r.get("id"): r.get("status") for r in records if isinstance(r, dict)}
    parameters = {p.get("symbol"): p.get("value") for p in source.get("parameters", []) if isinstance(p, dict)}
    for index, uncertainty in enumerate(source.get("uncertainties", [])):
        if not isinstance(uncertainty, dict):
            continue
        uid = str(uncertainty.get("id", ""))
        if uncertainty.get("status") != "missing" and not uid.startswith("missing_"):
            continue
        criterion = uncertainty.get("criterion_id") or uid.removeprefix("missing_")
        if statuses.get(criterion) in {"explicit", "derived"}:
            issues.append({"code": "uncertainty_evidence_contradiction", "path": f"uncertainties[{index}]", "criterion": criterion})
        symbol = uncertainty.get("parameter_symbol")
        if symbol in parameters and parameters[symbol] is not None:
            issues.append({"code": "parameter_declared_missing", "path": f"uncertainties[{index}]", "symbol": symbol})
    if issues:
        raise EvidenceConsistencyError(issues)
