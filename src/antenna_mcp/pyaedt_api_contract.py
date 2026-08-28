"""Static PyAEDT API contract for generated HFSS fragments.

The modeling pipeline deliberately validates generated source without importing or
starting AEDT.  This module captures the small, version-pinned surface that the
generator currently emits and that has been verified against PyAEDT 0.26.3.
"""

from __future__ import annotations

import ast


SUPPORTED_PYAEDT_VERSION = "0.26.3"


class PyAedtApiContractError(ValueError):
    """A generated fragment uses an API incompatible with the pinned PyAEDT."""

    def __init__(self, stage: str, version: str, issues: list[str]) -> None:
        self.stage = stage
        self.version = version
        self.issues = tuple(issues)
        details = "\n".join(f"- {issue}" for issue in issues)
        super().__init__(
            f"{stage} violates the PyAEDT {version} static API contract:\n{details}"
        )


_CYLINDER_KEYWORDS = {
    "orientation",
    "origin",
    "radius",
    "height",
    "num_sides",
    "name",
    "material",
}

_METHOD_KEYWORDS = {
    "get_faceid_from_position": {"position", "assignment", "units"},
    "assign_radiation_boundary_to_objects": {"assignment", "name"},
    "assign_perfecte_to_sheets": {"assignment", "name", "is_infinite_ground"},
    "wave_port": {
        "assignment",
        "reference",
        "create_port_sheet",
        "create_pec_cap",
        "integration_line",
        "port_on_plane",
        "modes",
        "impedance",
        "name",
        "renormalize",
        "deembed",
        "is_microstrip",
        "vfactor",
        "hfactor",
        "terminals_rename",
        "characteristic_impedance",
    },
    "create_linear_count_sweep": {
        "setup",
        "unit",
        "start_frequency",
        "stop_frequency",
        "num_of_freq_points",
        "name",
        "save_fields",
        "save_rad_fields",
        "sweep_type",
        "interpolation_tol",
        "interpolation_max_solutions",
    },
}


def validate_pyaedt_api_fragment(
    source: str,
    stage: str,
    version: str = SUPPORTED_PYAEDT_VERSION,
) -> None:
    """Validate supported PyAEDT calls in *source* without launching AEDT.

    The contract is intentionally strict.  It accepts positional arguments where
    the 0.26.3 signature makes their meaning unambiguous, but rejects deprecated,
    invented, or dynamically unpacked keyword spellings that cannot be checked
    safely before execution.
    """

    if version != SUPPORTED_PYAEDT_VERSION:
        raise ValueError(
            f"unsupported PyAEDT API contract version {version!r}; "
            f"supported version is {SUPPORTED_PYAEDT_VERSION}"
        )
    if not isinstance(stage, str) or not stage.strip():
        raise ValueError("stage must be a non-empty string")

    tree = ast.parse(source)
    issues: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        line = getattr(node, "lineno", "?")
        prefix = f"line {line}: {method}()"
        keywords = {keyword.arg for keyword in node.keywords if keyword.arg is not None}

        if method in {"create_rectangle", "create_circle"}:
            if not node.args and "orientation" not in keywords:
                issues.append(
                    f"{prefix} requires orientation as its first positional argument "
                    "or as the orientation= keyword"
                )
            _reject_duplicate_positional_keyword(
                node, "orientation", 0, prefix, issues
            )
            continue

        if method == "create_cylinder":
            if "axis" in keywords:
                issues.append(
                    f"{prefix} uses unsupported axis=; PyAEDT 0.26.3 requires "
                    "orientation= (or the first positional argument)"
                )
            if not node.args and "orientation" not in keywords and "axis" not in keywords:
                issues.append(
                    f"{prefix} requires orientation as its first positional argument "
                    "or as the orientation= keyword"
                )
            for keyword in node.keywords:
                if keyword.arg is None:
                    issues.append(
                        f"{prefix} uses **kwargs, so its PyAEDT keyword contract "
                        "cannot be validated statically"
                    )
                elif keyword.arg not in _CYLINDER_KEYWORDS and keyword.arg != "axis":
                    issues.append(
                        f"{prefix} has unsupported keyword {keyword.arg}=; allowed "
                        "PyAEDT 0.26.3 keywords are "
                        f"{', '.join(sorted(_CYLINDER_KEYWORDS))}"
                    )
            for position, keyword in enumerate(
                ("orientation", "origin", "radius", "height")
            ):
                _reject_duplicate_positional_keyword(
                    node, keyword, position, prefix, issues
                )
            continue

        if method == "get_face_by_position":
            issues.append(
                f"{prefix} is not a PyAEDT 0.26.3 method; use "
                "get_faceid_from_position(position=..., assignment=...) instead "
                "(position is the first argument)"
            )
            continue

        if method == "assign_wave_port":
            issues.append(
                f"{prefix} is not a PyAEDT 0.26.3 method; use "
                "hfss.wave_port(assignment=..., ...) instead"
            )
            continue

        if method == "assign_perfecte":
            issues.append(
                f"{prefix} is not a PyAEDT 0.26.3 method; use "
                "hfss.assign_perfecte_to_sheets(assignment=..., name=...) or "
                "hfss.assign_perfect_e(assignment=..., name=...) instead"
            )
            continue

        if method in _METHOD_KEYWORDS:
            _reject_unknown_keywords(
                node, method, _METHOD_KEYWORDS[method], prefix, issues
            )

        if method == "create_setup" and "frequency" in keywords:
            issues.append(
                f"{prefix} uses frequency=, which is ignored by the HFSS setup "
                "property map; PyAEDT 0.26.3 requires the case-sensitive "
                "native property Frequency="
            )

        if method == "create_linear_count_sweep":
            if "units" in keywords:
                issues.append(
                    f"{prefix} uses unsupported units=; PyAEDT 0.26.3 requires unit="
                )
            if len(node.args) < 2 and "unit" not in keywords:
                issues.append(
                    f"{prefix} requires unit as its second positional argument "
                    "or as the unit= keyword"
                )
            _reject_duplicate_positional_keyword(node, "unit", 1, prefix, issues)
            for keyword_name, position in (
                ("start_frequency", 2),
                ("stop_frequency", 3),
            ):
                value = _call_argument(node, keyword_name, position)
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    issues.append(
                        f"{prefix} supplies {keyword_name}= as a unit-bearing string; "
                        "when unit= is present PyAEDT 0.26.3 appends that unit, so use "
                        "a numeric frequency value instead"
                    )

    if issues:
        raise PyAedtApiContractError(stage, version, issues)


def _reject_unknown_keywords(
    call: ast.Call,
    method: str,
    allowed: set[str],
    prefix: str,
    issues: list[str],
) -> None:
    for keyword in call.keywords:
        if keyword.arg is None:
            issues.append(
                f"{prefix} uses **kwargs, so its PyAEDT keyword contract "
                "cannot be validated statically"
            )
        elif keyword.arg not in allowed:
            issues.append(
                f"{prefix} has unsupported keyword {keyword.arg}=; allowed PyAEDT "
                f"0.26.3 keywords are {', '.join(sorted(allowed))}"
            )


def _call_argument(call: ast.Call, keyword: str, position: int) -> ast.AST | None:
    for item in call.keywords:
        if item.arg == keyword:
            return item.value
    return call.args[position] if len(call.args) > position else None


def _reject_duplicate_positional_keyword(
    call: ast.Call,
    keyword: str,
    position: int,
    prefix: str,
    issues: list[str],
) -> None:
    if len(call.args) > position and any(
        item.arg == keyword for item in call.keywords
    ):
        issues.append(
            f"{prefix} supplies {keyword!r} both positionally and by keyword"
        )
