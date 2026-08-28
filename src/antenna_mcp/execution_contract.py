"""Cross-stage semantic checks for generated PyAEDT execution fragments."""

from __future__ import annotations

import ast
import json


class ExecutionContractError(ValueError):
    """Generated Python contradicts reviewed source or solver semantics."""

    def __init__(self, stage: str, issues: list[dict[str, object]]) -> None:
        self.stage = stage
        self.issues = tuple(issues)
        super().__init__(
            json.dumps(
                {"contract": "generated_execution", "stage": stage, "issues": issues},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


def validate_execution_fragment(
    source: str,
    stage: str,
    *,
    source_analysis: dict[str, object] | None = None,
    simulation_spec: dict[str, object] | None = None,
) -> None:
    """Reject code that conflicts with explicit helper-object and far-field facts.

    This validator is evidence-driven: generic jobs without these reviewed facts are
    unaffected.  It never imports PyAEDT or evaluates generated code.
    """

    tree = ast.parse(source)
    issues: list[dict[str, object]] = []
    helper_caps = _helper_port_caps(source_analysis)

    if stage == "model_3d" and helper_caps:
        for call in _calls(tree):
            if not call.func.attr.startswith("create_"):
                continue
            name = _literal_keyword(call, "name")
            if name in helper_caps:
                issues.append(
                    {
                        "code": "helper_object_created_directly",
                        "path": f"model_3d.{name}",
                        "expected": "created by wave_port(create_pec_cap=True)",
                        "actual": call.func.attr,
                        "line": call.lineno,
                    }
                )

    if stage == "simulation_setup":
        _validate_far_field(tree, simulation_spec, issues)
        for cap_name, cap in helper_caps.items():
            _validate_helper_port(tree, cap_name, cap, source_analysis, issues)

    if issues:
        raise ExecutionContractError(stage, issues)


def _helper_port_caps(
    source_analysis: dict[str, object] | None,
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    if not isinstance(source_analysis, dict):
        return result
    components = source_analysis.get("components")
    if not isinstance(components, list):
        return result
    for item in components:
        if not isinstance(item, dict) or item.get("role") != "wave_port_cap":
            continue
        name = item.get("name")
        geometry = item.get("geometric_evidence")
        source_face = geometry.get("source_face") if isinstance(geometry, dict) else None
        if isinstance(name, str) and name and isinstance(source_face, str) and source_face:
            result[name] = {"source_face": source_face}
    return result


def _validate_far_field(
    tree: ast.AST,
    simulation_spec: dict[str, object] | None,
    issues: list[dict[str, object]],
) -> None:
    if not isinstance(simulation_spec, dict):
        return
    far_field = simulation_spec.get("far_field")
    if not isinstance(far_field, dict) or far_field.get("enabled") is not False:
        return
    for call in _calls(tree):
        if call.func.attr == "insert_infinite_sphere":
            issues.append(
                {
                    "code": "disabled_far_field_created",
                    "path": "simulation_setup.far_field",
                    "expected": False,
                    "actual": "insert_infinite_sphere",
                    "line": call.lineno,
                }
            )


def _validate_helper_port(
    tree: ast.AST,
    cap_name: str,
    cap: dict[str, str],
    source_analysis: dict[str, object] | None,
    issues: list[dict[str, object]],
) -> None:
    calls = [call for call in _calls(tree) if call.func.attr == "wave_port"]
    expected_port_name = _port_target(source_analysis)
    if len(calls) != 1:
        issues.append(
            {
                "code": "wave_port_call_count",
                "path": "simulation_setup.wave_port",
                "expected": 1,
                "actual": len(calls),
            }
        )
        return
    call = calls[0]
    assignment = _argument(call, "assignment", 0)
    reference = _argument(call, "reference", 1)
    create_cap = _argument(call, "create_pec_cap", 3)
    name = _argument(call, "name", 8)
    expected_face = cap["source_face"]
    if _expression(assignment) != expected_face:
        issues.append(
            {
                "code": "wrong_port_assignment_face",
                "path": "simulation_setup.wave_port.assignment",
                "expected": expected_face,
                "actual": _expression(assignment),
                "line": call.lineno,
            }
        )
    expected_reference = expected_face.rsplit(".", 1)[0]
    if not _matches_reference(reference, expected_reference):
        issues.append(
            {
                "code": "wrong_port_reference",
                "path": "simulation_setup.wave_port.reference",
                "expected": expected_reference,
                "actual": _expression(reference),
                "line": call.lineno,
            }
        )
    if not isinstance(create_cap, ast.Constant) or create_cap.value is not True:
        issues.append(
            {
                "code": "missing_pec_cap_creation",
                "path": "simulation_setup.wave_port.create_pec_cap",
                "expected": True,
                "actual": _literal_value(create_cap),
                "line": call.lineno,
            }
        )
    if isinstance(expected_port_name, str) and _literal_value(name) != expected_port_name:
        issues.append(
            {
                "code": "wrong_port_name",
                "path": "simulation_setup.wave_port.name",
                "expected": expected_port_name,
                "actual": _literal_value(name),
                "line": call.lineno,
            }
        )
    assignment_value = _literal_value(assignment)
    if assignment_value == cap_name:
        issues.append(
            {
                "code": "cap_used_as_port_assignment",
                "path": "simulation_setup.wave_port.assignment",
                "expected": expected_face,
                "actual": cap_name,
                "line": call.lineno,
            }
        )


def _port_target(source_analysis: dict[str, object] | None) -> str | None:
    if not isinstance(source_analysis, dict):
        return None
    operations = source_analysis.get("operations")
    if not isinstance(operations, list):
        return None
    targets = {
        item.get("target")
        for item in operations
        if isinstance(item, dict) and item.get("operation") == "assign_wave_port"
    }
    return next(iter(targets)) if len(targets) == 1 else None


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]


def _literal_keyword(call: ast.Call, name: str) -> object:
    return _literal_value(_argument(call, name, 10_000))


def _argument(call: ast.Call, name: str, position: int) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return call.args[position] if len(call.args) > position else None


def _literal_value(node: ast.AST | None) -> object:
    return node.value if isinstance(node, ast.Constant) else None


def _expression(node: ast.AST | None) -> str | None:
    return ast.unparse(node) if node is not None else None


def _matches_reference(node: ast.AST | None, expected: str) -> bool:
    if isinstance(node, ast.Constant):
        return node.value == expected
    expression = _expression(node)
    return expression in {expected, f"{expected}.name"}
