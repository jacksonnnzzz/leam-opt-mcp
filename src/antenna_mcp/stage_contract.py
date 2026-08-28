"""Fail-closed ownership checks for generated PyAEDT stage fragments.

The modeling workflow deliberately generates several Python fragments rather
than one unconstrained script.  This module enforces the responsibility of
each fragment without importing or executing PyAEDT.  It is intentionally
AST-only so a rejected candidate cannot have simulator side effects.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from typing import Iterable


SUPPORTED_STAGES = frozenset({"model_3d", "model_2d", "boolean", "simulation_setup"})

_BOOLEAN_METHODS = frozenset(
    {
        "connect",
        "imprint",
        "intersect",
        "section",
        "separate_bodies",
        "split",
        "subtract",
        "unite",
    }
)
_CLEANUP_METHODS = frozenset(
    {
        "cleanup_objects",
        "delete",
        "delete_objects_containing",
        "heal_objects",
        "purge_history",
        "remove_history",
        "simplify_objects",
    }
)
_MODELER_QUERY_PREFIXES = (
    "find_",
    "get_",
    "has_",
    "is_",
)
_GEOMETRY_METHOD_PREFIXES = (
    "chamfer",
    "clone",
    "create_",
    "duplicate_",
    "extrude",
    "fillet",
    "mirror",
    "move",
    "rotate",
    "scale",
    "sweep_",
    "thicken",
    "translate",
    "wrap",
)
_GEOMETRY_CONFIGURATION_METHODS = frozenset(
    {
        "fit_all",
        "set_working_coordinate_system",
        "set_working_units",
    }
)
_ANALYZE_PREFIXES = ("analyze", "analyse", "solve", "run_analysis")


@dataclass(frozen=True, slots=True)
class StageOwnershipViolation:
    """One machine-readable stage ownership failure."""

    stage: str
    code: str
    message: str
    line: int | None = None
    column: int | None = None
    api: str | None = None
    category: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class StageOwnershipError(ValueError):
    """Raised when a Python fragment performs work owned by another stage."""

    def __init__(self, violations: Iterable[StageOwnershipViolation]):
        self.violations = tuple(violations)
        if not self.violations:
            raise ValueError("StageOwnershipError requires at least one violation")
        details = "; ".join(_format_violation(item) for item in self.violations)
        super().__init__(f"stage ownership validation failed: {details}")

    def to_dict(self) -> dict[str, object]:
        return {
            "error": "stage_ownership_violation",
            "violations": [item.to_dict() for item in self.violations],
        }


def validate_stage_ownership(source: str, stage: str) -> None:
    """Validate that *source* contains only operations owned by *stage*.

    No generated code is evaluated.  Any HFSS/PyAEDT call that cannot be
    classified for the selected stage is rejected rather than implicitly
    permitted.  Calls unrelated to ``hfss`` remain the responsibility of the
    workflow's general generated-code safety validator.
    """

    if stage not in SUPPORTED_STAGES:
        raise StageOwnershipError(
            [
                StageOwnershipViolation(
                    stage=stage,
                    code="unsupported_stage",
                    message=(
                        f"unsupported Python stage {stage!r}; expected one of "
                        f"{', '.join(sorted(SUPPORTED_STAGES))}"
                    ),
                )
            ]
        )
    if not isinstance(source, str):
        raise StageOwnershipError(
            [
                StageOwnershipViolation(
                    stage=stage,
                    code="invalid_source",
                    message="generated stage source must be a string",
                )
            ]
        )

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise StageOwnershipError(
            [
                StageOwnershipViolation(
                    stage=stage,
                    code="syntax_error",
                    message=exc.msg,
                    line=exc.lineno,
                    column=exc.offset,
                )
            ]
        ) from exc

    aliases = _collect_aliases(tree)
    violations: list[StageOwnershipViolation] = []
    boolean_count = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            path = _resolve_path(node.func, aliases)
            category = _classify_path(path)
            if category == "boolean":
                boolean_count += 1
            violation = _validate_call(stage, path, category, node)
            if violation is not None:
                violations.append(violation)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets: list[ast.expr]
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                targets = [node.target]
            for target in targets:
                for leaf in _assignment_leaves(target):
                    violation = _validate_write(stage, leaf, aliases)
                    if violation is not None:
                        violations.append(violation)

    if stage == "boolean" and boolean_count == 0:
        violations.append(
            StageOwnershipViolation(
                stage=stage,
                code="missing_boolean_operation",
                message=(
                    "boolean stage must perform at least one explicit subtract, unite, "
                    "intersect, split, section, imprint, or connect operation"
                ),
                category="boolean",
            )
        )

    if violations:
        raise StageOwnershipError(_deduplicate(violations))


def _format_violation(violation: StageOwnershipViolation) -> str:
    location = f" at line {violation.line}" if violation.line is not None else ""
    api = f" ({violation.api})" if violation.api else ""
    return f"[{violation.code}]{location}{api} {violation.message}"


def _deduplicate(
    violations: Iterable[StageOwnershipViolation],
) -> tuple[StageOwnershipViolation, ...]:
    result: list[StageOwnershipViolation] = []
    seen: set[tuple[object, ...]] = set()
    for item in violations:
        key = (item.code, item.line, item.column, item.api, item.category)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return tuple(result)


def _collect_aliases(tree: ast.AST) -> dict[str, tuple[str, ...]]:
    """Conservatively resolve simple aliases such as ``m = hfss.modeler``."""

    aliases: dict[str, tuple[str, ...]] = {}
    assignments: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    assignments.append((target.id, value))

    # A short fixed point resolves chained aliases independent of AST walk order.
    for _ in range(len(assignments) + 1):
        changed = False
        for name, value in assignments:
            resolved = _resolve_alias_value(value, aliases)
            if resolved is not None and aliases.get(name) != resolved:
                aliases[name] = resolved
                changed = True
        if not changed:
            break
    return aliases


def _resolve_alias_value(
    value: ast.AST, aliases: dict[str, tuple[str, ...]]
) -> tuple[str, ...] | None:
    if isinstance(value, ast.Call):
        call_path = _resolve_path(value.func, aliases)
        category = _classify_path(call_path)
        if category in {
            "boundary",
            "boolean",
            "cleanup",
            "far_field",
            "geometry",
            "material",
            "modeler_query",
            "parameter",
            "solver",
        }:
            return (f"@{category}",)
        return None
    return _resolve_path(value, aliases)


def _resolve_path(
    node: ast.AST, aliases: dict[str, tuple[str, ...]]
) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, (node.id,))
    if isinstance(node, ast.Attribute):
        base = _resolve_path(node.value, aliases)
        return (*base, node.attr) if base else None
    if isinstance(node, ast.Subscript):
        base = _resolve_path(node.value, aliases)
        return (*base, "[]") if base else None
    return None


def _classify_path(path: tuple[str, ...] | None) -> str | None:
    if not path:
        return None
    if path[0].startswith("@"):
        marker = path[0][1:]
        if marker == "modeler_query" and len(path) > 1:
            method = path[-1].casefold()
            if method.startswith(_MODELER_QUERY_PREFIXES):
                return "modeler_query"
            return "geometry"
        return marker
    if path[0] != "hfss":
        return None
    if len(path) == 1:
        return "unknown_hfss"

    lowered = tuple(part.casefold() for part in path)
    if lowered[1] in {"materials", "material_manager"}:
        return "material"
    if lowered[1] in {"variable_manager", "project_variable_manager"}:
        return "parameter"
    if lowered[1] == "modeler":
        if len(lowered) < 3:
            return "unknown_hfss"
        method = lowered[-1]
        if method in _BOOLEAN_METHODS:
            return "boolean"
        if method in _CLEANUP_METHODS:
            return "cleanup"
        if method.startswith(_MODELER_QUERY_PREFIXES):
            return "modeler_query"
        if method in _GEOMETRY_CONFIGURATION_METHODS or method.startswith(
            _GEOMETRY_METHOD_PREFIXES
        ):
            return "geometry"
        return "unknown_modeler"

    method = lowered[-1]
    joined = "_".join(lowered[1:])
    if method.startswith(_ANALYZE_PREFIXES) or joined.startswith(_ANALYZE_PREFIXES):
        return "analyze"
    if (
        "far_field" in joined
        or "farfield" in joined
        or "infinite_sphere" in joined
        or "near_field" in joined
    ):
        return "far_field"
    if (
        method.startswith("assign_")
        or "boundary" in joined
        or "radiation" in joined
        or "wave_port" in joined
        or "lumped_port" in joined
        or "circuit_port" in joined
        or "terminal" in joined
        or "perfecte" in joined
        or "perfect_e" in joined
        or "open_region" in joined
        or method in {"wave_port", "lumped_port"}
    ):
        return "boundary"
    if "setup" in joined or "sweep" in joined or method == "set_solution_type":
        return "solver"
    return "unknown_hfss"


def _validate_call(
    stage: str,
    path: tuple[str, ...] | None,
    category: str | None,
    node: ast.Call,
) -> StageOwnershipViolation | None:
    # Non-HFSS Python calls are covered by the general code-safety validator.
    if category is None:
        return None
    api = ".".join(path) if path else None

    if stage in {"model_3d", "model_2d"}:
        allowed = {"geometry", "material", "modeler_query", "parameter", "cleanup"}
    elif stage == "boolean":
        allowed = {"boolean", "cleanup"}
    else:
        allowed = {"boundary", "far_field", "modeler_query", "solver"}

    if category in allowed:
        return None
    if category in {"unknown_hfss", "unknown_modeler"}:
        code = "unclassified_hfss_api"
        message = (
            f"{api or 'HFSS API'} cannot be classified as an operation owned by {stage}; "
            "unknown HFSS APIs are rejected"
        )
    else:
        code = "forbidden_operation"
        message = f"{category} operation {api or ''} is not owned by {stage} stage".strip()
    return StageOwnershipViolation(
        stage=stage,
        code=code,
        message=message,
        line=getattr(node, "lineno", None),
        column=getattr(node, "col_offset", None),
        api=api,
        category=category,
    )


def _assignment_leaves(target: ast.expr) -> Iterable[ast.expr]:
    if isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            yield from _assignment_leaves(item)
    else:
        yield target


def _validate_write(
    stage: str, target: ast.expr, aliases: dict[str, tuple[str, ...]]
) -> StageOwnershipViolation | None:
    # Binding a local alias (``modeler = hfss.modeler``) is not itself a write
    # through that alias.  Calls made through it are still resolved above.
    if isinstance(target, ast.Name):
        return None
    path = _resolve_path(target, aliases)
    if not path:
        return None

    category: str | None = None
    if path[0] == "hfss" and "[]" in path:
        category = "parameter"
    elif path[0] == "hfss":
        lowered = tuple(item.casefold() for item in path)
        if len(lowered) > 1 and lowered[1] in {"materials", "material_manager"}:
            category = "material"
        elif len(lowered) > 1 and lowered[1] in {
            "variable_manager",
            "project_variable_manager",
        }:
            category = "parameter"
        elif "solution_type" in lowered or "setup" in lowered or "sweep" in lowered:
            category = "solver"
        elif len(lowered) > 1 and lowered[1] == "modeler":
            category = "geometry"
        else:
            category = "unknown_hfss"
    elif path[0] == "@material":
        category = "material"
    elif path[0] == "@solver":
        category = "solver"

    if category is None:
        return None
    api = ".".join(path)
    if stage in {"model_3d", "model_2d"}:
        allowed = {"geometry", "material", "parameter"}
    elif stage == "boolean":
        allowed = set()
    else:
        allowed = {"solver"}
    if category in allowed:
        return None
    code = "unclassified_hfss_api" if category == "unknown_hfss" else "forbidden_write"
    return StageOwnershipViolation(
        stage=stage,
        code=code,
        message=f"{category} write {api} is not owned by {stage} stage",
        line=getattr(target, "lineno", None),
        column=getattr(target, "col_offset", None),
        api=api,
        category=category,
    )
