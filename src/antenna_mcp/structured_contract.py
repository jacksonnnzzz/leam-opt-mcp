from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


class StructuredContractError(ValueError):
    """Raised when a structured modeling artifact violates its stage contract."""

    def __init__(self, contract: str, issues: list[dict[str, object]]) -> None:
        self.contract = contract
        self.issues = issues
        super().__init__(
            json.dumps(
                {"contract": contract, "issues": issues},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


_IDENTITY_FIELDS = ("name", "role", "primitive", "material")
_KNOWN_RELATIONSHIP_FIELDS = ("parent_layer", "boundary")
_PRESERVED_OPTIONAL_FIELDS = ("fill_material", "body_material")
_STACKUP_LAYER_TOKENS = {
    "stackup_ground_layer": "ground",
    "stackup_dielectric_layer": "dielectric",
    "stackup_signal_layer": "signal",
}
_GEOMETRY_TOLERANCE = 1.0e-7


def validate_dimensions_against_solids(dimensions: object, solids: object) -> None:
    """Validate the identity and declared relationships of dimensioned solids.

    ``solids`` is the authoritative component contract.  ``dimensions`` must carry a
    one-to-one ``solids`` collection and preserve each component's name, role,
    primitive, and material verbatim.  Relationships are never guessed from object
    names.  Patch and open-region semantic primitives must explicitly declare the
    relationships that their downstream HFSS construction needs.
    """

    contract = "dimensions_against_solids"
    issues: list[dict[str, object]] = []
    expected = _solid_index(solids, "solids.solids", issues)
    actual = _solid_index(dimensions, "dimensions.solids", issues)

    expected_names = set(expected)
    actual_names = set(actual)
    for name in sorted(expected_names - actual_names):
        issues.append(
            {
                "code": "missing_record",
                "path": "dimensions.solids",
                "expected": name,
                "actual": None,
            }
        )
    for name in sorted(actual_names - expected_names):
        issues.append(
            {
                "code": "unexpected_record",
                "path": "dimensions.solids",
                "expected": None,
                "actual": name,
            }
        )

    for name in sorted(expected_names & actual_names):
        expected_record = expected[name]
        actual_record = actual[name]
        for field in _IDENTITY_FIELDS[1:]:
            _compare_required_field(
                expected_record,
                actual_record,
                field,
                f"dimensions.solids[{name!r}].{field}",
                issues,
            )

        relation_fields = _relationship_fields(expected_record, actual_record)
        for field in sorted(relation_fields):
            expected_value = expected_record.get(field)
            actual_value = actual_record.get(field)
            if not _is_nonempty_relationship(expected_value):
                issues.append(
                    {
                        "code": "missing_relationship",
                        "path": f"solids.solids[{name!r}].{field}",
                        "expected": "explicit non-empty relationship",
                        "actual": expected_value,
                    }
                )
                continue
            if not _is_nonempty_relationship(actual_value):
                issues.append(
                    {
                        "code": "missing_relationship",
                        "path": f"dimensions.solids[{name!r}].{field}",
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )
                continue
            if actual_value != expected_value:
                issues.append(
                    {
                        "code": "value_mismatch",
                        "path": f"dimensions.solids[{name!r}].{field}",
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )

        for field in _PRESERVED_OPTIONAL_FIELDS:
            if field not in expected_record or not _is_nonempty_relationship(
                expected_record.get(field)
            ):
                continue
            expected_value = expected_record[field]
            actual_value = actual_record.get(field)
            if actual_value != expected_value:
                issues.append(
                    {
                        "code": "missing_field" if field not in actual_record else "value_mismatch",
                        "path": f"dimensions.solids[{name!r}].{field}",
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )

    # Identity alone is insufficient for a simulator-facing stackup.  Apply
    # physical checks only when records explicitly use stackup semantic
    # primitives/roles, so ordinary boxes, sheets, and cylinders remain valid.
    _validate_stackup_topology(solids, expected, "solids.solids", issues)
    _validate_stackup_topology(dimensions, actual, "dimensions.solids", issues)
    _validate_explicit_geometry_preservation(expected, actual, issues)

    if issues:
        raise StructuredContractError(contract, issues)


def validate_source_component_topology(payload: object) -> None:
    """Fail early when explicit source components contradict Stackup physics.

    The source schema validator owns field syntax. This gate only evaluates numeric
    geometry and material semantics that the source response explicitly supplied;
    non-stackup and unresolved evidence remains untouched.
    """

    if not isinstance(payload, Mapping):
        return
    contract = "source_component_topology"
    issues: list[dict[str, object]] = []
    proxy = {
        "solids": payload.get("components"),
        "operations": payload.get("operations"),
    }
    records = _solid_index(proxy, "source_analysis.components", issues)
    _validate_stackup_topology(proxy, records, "source_analysis.components", issues)
    if issues:
        raise StructuredContractError(contract, issues)


def validate_simulation_spec(payload: object) -> None:
    """Validate the portable, benchmarkable HFSS simulation-spec schema."""

    contract = "simulation_spec"
    issues: list[dict[str, object]] = []
    if not isinstance(payload, Mapping):
        raise StructuredContractError(
            contract,
            [
                {
                    "code": "invalid_type",
                    "path": "simulation_spec",
                    "expected": "object",
                    "actual": _type_name(payload),
                }
            ],
        )

    design_type = payload.get("design_type")
    if design_type != "HFSS":
        issues.append(
            {
                "code": "missing_field" if design_type is None else "invalid_value",
                "path": "simulation_spec.design_type",
                "expected": "HFSS",
                "actual": design_type,
            }
        )
    _require_nonempty_string(payload, "solution_type", "simulation_spec", issues)

    setup = _require_mapping(payload, "setup", "simulation_spec", issues)
    if setup is not None:
        _require_nonempty_string(setup, "name", "simulation_spec.setup", issues)
        _require_nonempty_string(setup, "type", "simulation_spec.setup", issues)
        adaptive = _require_mapping(
            setup,
            "adaptive_frequency",
            "simulation_spec.setup",
            issues,
        )
        if adaptive is not None:
            _require_frequency(adaptive, "simulation_spec.setup.adaptive_frequency", issues)

    sweep = _require_mapping(payload, "sweep", "simulation_spec", issues)
    if sweep is not None:
        _require_nonempty_string(sweep, "name", "simulation_spec.sweep", issues)
        _require_nonempty_string(sweep, "type", "simulation_spec.sweep", issues)
        for edge in ("start", "stop"):
            frequency = _require_mapping(sweep, edge, "simulation_spec.sweep", issues)
            if frequency is not None:
                _require_frequency(frequency, f"simulation_spec.sweep.{edge}", issues)

    _require_nonempty_string(payload, "s_parameter", "simulation_spec", issues)

    for optional_object in ("excitation", "open_region", "far_field"):
        if optional_object in payload and not isinstance(payload[optional_object], Mapping):
            issues.append(
                {
                    "code": "invalid_type",
                    "path": f"simulation_spec.{optional_object}",
                    "expected": "object",
                    "actual": _type_name(payload[optional_object]),
                }
            )
    if "uncertainties" in payload and not _is_array(payload["uncertainties"]):
        issues.append(
            {
                "code": "invalid_type",
                "path": "simulation_spec.uncertainties",
                "expected": "array",
                "actual": _type_name(payload["uncertainties"]),
            }
        )

    if issues:
        raise StructuredContractError(contract, issues)


def _solid_index(
    payload: object,
    path: str,
    issues: list[dict[str, object]],
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        issues.append(
            {
                "code": "invalid_type",
                "path": path.rsplit(".", 1)[0],
                "expected": "object",
                "actual": _type_name(payload),
            }
        )
        return {}
    records = payload.get("solids")
    if not _is_array(records):
        issues.append(
            {
                "code": "missing_field" if records is None else "invalid_type",
                "path": path,
                "expected": "array",
                "actual": records if records is None else _type_name(records),
            }
        )
        return {}

    indexed: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        record_path = f"{path}[{index}]"
        if not isinstance(record, Mapping):
            issues.append(
                {
                    "code": "invalid_type",
                    "path": record_path,
                    "expected": "object",
                    "actual": _type_name(record),
                }
            )
            continue
        name = record.get("name")
        if not isinstance(name, str) or not name.strip():
            issues.append(
                {
                    "code": "missing_field" if name is None else "invalid_value",
                    "path": f"{record_path}.name",
                    "expected": "non-empty string",
                    "actual": name,
                }
            )
            continue
        if name in indexed:
            issues.append(
                {
                    "code": "duplicate_name",
                    "path": f"{record_path}.name",
                    "expected": "unique solid name",
                    "actual": name,
                }
            )
            continue
        indexed[name] = record
    return indexed


def _validate_stackup_topology(
    payload: object,
    records: Mapping[str, Mapping[str, Any]],
    path: str,
    issues: list[dict[str, object]],
) -> None:
    """Validate only explicitly declared Stackup3D-like topology.

    The checks deliberately key off semantic roles/primitives rather than object
    names.  Numeric geometry is checked when it is present; symbolic-only or
    non-stackup antenna representations are left untouched.
    """

    layers = {name: record for name, record in records.items() if _layer_kind(record)}
    if not layers:
        return

    probe_records = {
        name: record for name, record in records.items() if _is_probe_inner(record)
    }
    operation_order = _stackup_operation_order(payload, set(layers))

    grouped: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for name, record in layers.items():
        grouped.setdefault(_stackup_group(record), []).append((name, record))

    for group_records in grouped.values():
        group_names = {name for name, _ in group_records}
        group_operation_order = [name for name in operation_order if name in group_names]
        extents = [
            (name, record, extent)
            for name, record in group_records
            if (extent := _z_extent(record)) is not None
        ]
        if len(extents) != len(group_records) or len(extents) < 2:
            continue

        by_elevation = sorted(extents, key=lambda item: (item[2][0], item[2][1], item[0]))
        physical_order = [name for name, _, _ in by_elevation]
        if group_operation_order:
            comparable_physical_order = [
                name for name in physical_order if name in set(group_operation_order)
            ]
            if comparable_physical_order != group_operation_order:
                issues.append(
                    {
                        "code": "stackup_order_mismatch",
                        "path": f"{path}.stackup_order",
                        "expected": group_operation_order,
                        "actual": comparable_physical_order,
                    }
                )

        for (lower_name, _, lower), (upper_name, _, upper) in zip(
            by_elevation,
            by_elevation[1:],
        ):
            if _close(lower[1], upper[0]):
                continue
            issues.append(
                {
                    "code": "stackup_overlap" if upper[0] < lower[1] else "stackup_gap",
                    "path": f"{path}[{upper_name!r}].geometry.z_min",
                    "expected": lower[1],
                    "actual": upper[0],
                    "adjacent_to": lower_name,
                }
            )

    signal_layers = {
        name: record for name, record in layers.items() if _layer_kind(record) == "signal"
    }
    ground_layers = {
        name: record for name, record in layers.items() if _layer_kind(record) == "ground"
    }

    # A Stackup3D patch occupies its parent signal layer from the layer's
    # elevation for the layer thickness.  In particular, it does not start on
    # the signal layer's top face.
    patch_signal_names: list[str] = []
    for name, record in records.items():
        if not _is_patch(record):
            continue
        parent_name = record.get("parent_layer")
        if not isinstance(parent_name, str) or parent_name not in signal_layers:
            continue
        patch_signal_names.append(parent_name)
        parent_extent = _z_extent(signal_layers[parent_name])
        patch_extent = _z_extent(record)
        if parent_extent is None or patch_extent is None:
            continue
        if not _close(patch_extent[0], parent_extent[0]):
            issues.append(
                {
                    "code": "patch_parent_elevation_mismatch",
                    "path": f"{path}[{name!r}].geometry.z_min",
                    "expected": parent_extent[0],
                    "actual": patch_extent[0],
                    "parent_layer": parent_name,
                }
            )
        parent_thickness = parent_extent[1] - parent_extent[0]
        patch_thickness = patch_extent[1] - patch_extent[0]
        if not _close(patch_thickness, parent_thickness):
            issues.append(
                {
                    "code": "patch_parent_thickness_mismatch",
                    "path": f"{path}[{name!r}].geometry.z_span",
                    "expected": parent_thickness,
                    "actual": patch_thickness,
                    "parent_layer": parent_name,
                }
            )

    # Explicit signal fill metadata is meaningful: a signal layer containing a
    # separate patch is a fill volume, not another copy of the conductor.  Do
    # not require the optional field when the evidence contract does not expose
    # it, but reject self-contradictory values when it is supplied.
    for signal_name in set(patch_signal_names):
        signal_record = signal_layers[signal_name]
        fill_material = _fill_material(signal_record)
        body_material = signal_record.get("body_material")
        conductor_material = signal_record.get("material")
        if (
            isinstance(fill_material, str)
            and fill_material.strip()
            and isinstance(body_material, str)
            and body_material.strip()
            and fill_material.strip().casefold() != body_material.strip().casefold()
        ):
            issues.append(
                {
                    "code": "signal_body_fill_mismatch",
                    "path": f"{path}[{signal_name!r}].body_material",
                    "expected": fill_material,
                    "actual": body_material,
                }
            )
        if (
            isinstance(fill_material, str)
            and fill_material.strip()
            and isinstance(conductor_material, str)
            and fill_material.strip().casefold() == conductor_material.strip().casefold()
        ):
            issues.append(
                {
                    "code": "invalid_signal_fill_material",
                    "path": f"{path}[{signal_name!r}].fill_material",
                    "expected": "fill material distinct from signal conductor material",
                    "actual": fill_material,
                }
            )
        if (
            isinstance(body_material, str)
            and body_material.strip()
            and isinstance(conductor_material, str)
            and body_material.strip().casefold() == conductor_material.strip().casefold()
        ):
            issues.append(
                {
                    "code": "invalid_signal_body_material",
                    "path": f"{path}[{signal_name!r}].body_material",
                    "expected": "fill material distinct from signal conductor material",
                    "actual": body_material,
                }
            )

    # Resolve an implicit ground reference only when it is unambiguous.  With
    # multiple ground/signal layers, explicit relationships are required before
    # this validator makes a physical claim.
    for probe_name, probe_record in probe_records.items():
        signal_name = _probe_signal_reference(probe_record, records, patch_signal_names)
        ground_name = _probe_ground_reference(probe_record, ground_layers)
        if signal_name is None or ground_name is None:
            continue
        signal_extent = _z_extent(signal_layers[signal_name])
        ground_extent = _z_extent(ground_layers[ground_name])
        probe_extent = _z_extent(probe_record)
        if signal_extent is None or ground_extent is None or probe_extent is None:
            continue
        expected_span = (ground_extent[1], signal_extent[0])
        if expected_span[1] < expected_span[0] - _GEOMETRY_TOLERANCE:
            issues.append(
                {
                    "code": "invalid_probe_layer_order",
                    "path": f"{path}[{probe_name!r}].geometry.z_span",
                    "expected": "ground top at or below signal elevation",
                    "actual": [expected_span[0], expected_span[1]],
                }
            )
            continue
        if not (
            _close(probe_extent[0], expected_span[0])
            and _close(probe_extent[1], expected_span[1])
        ):
            issues.append(
                {
                    "code": "probe_stackup_span_mismatch",
                    "path": f"{path}[{probe_name!r}].geometry.z_span",
                    "expected": [expected_span[0], expected_span[1]],
                    "actual": [probe_extent[0], probe_extent[1]],
                    "reference_ground": ground_name,
                    "signal_layer": signal_name,
                }
            )


def _validate_explicit_geometry_preservation(
    expected: Mapping[str, Mapping[str, Any]],
    actual: Mapping[str, Mapping[str, Any]],
    issues: list[dict[str, object]],
) -> None:
    """Keep explicit stackup Z geometry and fill metadata immutable downstream."""

    relevant_names = {
        name
        for name, record in expected.items()
        if _layer_kind(record) or _is_probe_inner(record)
    }
    relevant_names.update(
        name
        for name, record in expected.items()
        if any(_explicit_axis_range(record, axis) is not None for axis in ("x", "y", "z"))
    )
    relevant_names.update(
        name
        for name, record in expected.items()
        if _is_patch(record)
        and isinstance(record.get("parent_layer"), str)
        and record.get("parent_layer") in expected
        and _layer_kind(expected[str(record.get("parent_layer"))]) == "signal"
    )
    for name in sorted(relevant_names & set(actual)):
        for axis in ("x", "y", "z"):
            expected_extent = _explicit_axis_range(expected[name], axis)
            if expected_extent is None and axis == "z":
                expected_extent = _z_extent(expected[name])
            if expected_extent is None:
                continue
            actual_extent = _explicit_axis_range(actual[name], axis)
            if actual_extent is None and axis == "z":
                actual_extent = _z_extent(actual[name])
            if actual_extent is None:
                issues.append(
                    {
                        "code": "missing_explicit_geometry",
                        "path": f"dimensions.solids[{name!r}].geometry.{axis}_extent",
                        "expected": [expected_extent[0], expected_extent[1]],
                        "actual": None,
                    }
                )
            elif not _extent_close(expected_extent, actual_extent):
                issues.append(
                    {
                        "code": "explicit_geometry_mismatch",
                        "path": f"dimensions.solids[{name!r}].geometry.{axis}_extent",
                        "expected": [expected_extent[0], expected_extent[1]],
                        "actual": [actual_extent[0], actual_extent[1]],
                    }
                )



def _layer_kind(record: Mapping[str, Any]) -> str | None:
    for field in ("role", "primitive"):
        value = record.get(field)
        if isinstance(value, str):
            kind = _STACKUP_LAYER_TOKENS.get(value.strip().casefold())
            if kind:
                return kind
    return None


def _is_patch(record: Mapping[str, Any]) -> bool:
    for field in ("role", "primitive"):
        value = record.get(field)
        if isinstance(value, str):
            token = value.strip().casefold()
            if token == "patch" or token.endswith("_patch"):
                return True
    return False


def _is_probe_inner(record: Mapping[str, Any]) -> bool:
    return any(
        isinstance(record.get(field), str)
        and record[field].strip().casefold() == "probe_inner"
        for field in ("role", "primitive")
    )


def _stackup_group(record: Mapping[str, Any]) -> str:
    for field in ("stackup_id", "stackup_name", "stackup"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "__default__"


def _stackup_operation_order(payload: object, layer_names: set[str]) -> list[str]:
    if not isinstance(payload, Mapping) or not _is_array(payload.get("operations")):
        return []
    ordered: list[tuple[float, int, str]] = []
    for index, operation in enumerate(payload["operations"]):
        if not isinstance(operation, Mapping):
            continue
        action = operation.get("operation", operation.get("action"))
        target = operation.get("target")
        if not (
            isinstance(action, str)
            and action.strip().casefold()
            in {"add_ground_layer", "add_dielectric_layer", "add_signal_layer"}
            and isinstance(target, str)
            and target in layer_names
        ):
            continue
        raw_order = operation.get("order")
        order = float(raw_order) if _finite_number(raw_order) else float(index)
        ordered.append((order, index, target))
    result: list[str] = []
    for _, _, name in sorted(ordered):
        if name not in result:
            result.append(name)
    return result


def _probe_signal_reference(
    probe: Mapping[str, Any],
    records: Mapping[str, Mapping[str, Any]],
    patch_signal_names: list[str],
) -> str | None:
    for field in ("signal_layer", "parent_layer"):
        value = probe.get(field)
        if isinstance(value, str) and value in records and _layer_kind(records[value]) == "signal":
            return value
    dependencies = probe.get("dependencies")
    if isinstance(dependencies, Mapping) and _is_array(dependencies.get("objects")):
        for object_name in dependencies["objects"]:
            if not isinstance(object_name, str) or object_name not in records:
                continue
            dependency = records[object_name]
            if _is_patch(dependency):
                parent = dependency.get("parent_layer")
                if isinstance(parent, str) and parent in records and _layer_kind(records[parent]) == "signal":
                    return parent
            if _layer_kind(dependency) == "signal":
                return object_name
    unique_signals = set(patch_signal_names)
    return next(iter(unique_signals)) if len(unique_signals) == 1 else None


def _probe_ground_reference(
    probe: Mapping[str, Any],
    ground_layers: Mapping[str, Mapping[str, Any]],
) -> str | None:
    for field in ("reference_ground", "ground_layer", "reference_layer"):
        value = probe.get(field)
        if isinstance(value, str) and value in ground_layers:
            return value
    return next(iter(ground_layers)) if len(ground_layers) == 1 else None


def _fill_material(record: Mapping[str, Any]) -> object:
    for field in ("fill_material", "layer_fill_material"):
        if field in record:
            return record[field]
    geometry = record.get("geometry")
    if isinstance(geometry, Mapping) and "fill_material" in geometry:
        return geometry["fill_material"]
    return None


def _z_extent(record: Mapping[str, Any]) -> tuple[float, float] | None:
    explicit = _explicit_axis_range(record, "z")
    if explicit is not None:
        return explicit

    for container_name in ("geometry", "dimensions"):
        geometry = record.get(container_name)
        if not isinstance(geometry, Mapping):
            continue
        position = geometry.get("position", geometry.get("origin"))
        if position is None:
            position = geometry.get("global_origin_mm", geometry.get("origin_mm"))
        start = _axis_number(position, "z")
        if start is not None:
            size = _axis_number(geometry.get("size", geometry.get("sizes")), "z")
            if size is None:
                size = _number(
                    geometry.get(
                        "signed_height_mm",
                        geometry.get(
                            "height_mm",
                            geometry.get("height", geometry.get("thickness")),
                        ),
                    )
                )
            if size is None:
                size = _number(geometry.get("size_z_mm"))
            if size is not None:
                return min(start, start + size), max(start, start + size)
            return start, start
        center = _axis_number(geometry.get("center"), "z")
        if center is not None:
            return center, center

    for start_field, end_field in (
        ("base_corner", "opposite_corner"),
        ("base_center", "top_center"),
    ):
        start = _axis_number(record.get(start_field), "z")
        end = _axis_number(record.get(end_field), "z")
        if start is not None and end is not None:
            return min(start, end), max(start, end)

    start = None
    for field in ("base_corner", "base_center", "corner", "origin", "position"):
        start = _axis_number(record.get(field), "z")
        if start is not None:
            break
    if start is not None:
        size = _axis_number(record.get("size", record.get("sizes")), "z")
        if size is None:
            size = _number(record.get("height", record.get("thickness")))
        if size is not None:
            return min(start, start + size), max(start, start + size)
        return start, start

    center = _axis_number(record.get("center"), "z")
    if center is not None:
        return center, center

    vertices = record.get("vertices")
    if _is_array(vertices):
        z_values = [
            value
            for vertex in vertices
            if (value := _axis_number(vertex, "z")) is not None
        ]
        if z_values:
            return min(z_values), max(z_values)

    elevation = _number(record.get("elevation"))
    if elevation is not None:
        thickness = _number(record.get("thickness"))
        if thickness is None:
            return elevation, elevation
        return min(elevation, elevation + thickness), max(elevation, elevation + thickness)
    return None


def _explicit_axis_range(
    record: Mapping[str, Any],
    axis: str,
) -> tuple[float, float] | None:
    key = f"{axis}_range_mm"
    for candidate in (
        record,
        record.get("geometric_evidence"),
        record.get("geometry"),
        record.get("dimensions"),
    ):
        if not isinstance(candidate, Mapping):
            continue
        raw = candidate.get(key)
        if not _is_array(raw) or len(raw) != 2:
            continue
        start = _number(raw[0])
        end = _number(raw[1])
        if start is not None and end is not None:
            return min(start, end), max(start, end)
    return None


def _axis_number(value: object, axis: str) -> float | None:
    if isinstance(value, Mapping):
        return _number(value.get(axis))
    if _is_array(value):
        index = {"x": 0, "y": 1, "z": 2}[axis]
        return _number(value[index]) if len(value) > index else None
    return None


def _number(value: object) -> float | None:
    if isinstance(value, Mapping):
        for field in ("value_mm", "value"):
            if field in value:
                return _number(value[field])
        return None
    if not _finite_number(value):
        return None
    return float(value)


def _finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _close(left: float, right: float) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=_GEOMETRY_TOLERANCE,
        abs_tol=_GEOMETRY_TOLERANCE,
    )


def _extent_close(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return _close(left[0], right[0]) and _close(left[1], right[1])


def _compare_required_field(
    expected_record: Mapping[str, Any],
    actual_record: Mapping[str, Any],
    field: str,
    path: str,
    issues: list[dict[str, object]],
) -> None:
    expected_value = expected_record.get(field)
    if field == "material" and expected_value is None:
        if field not in actual_record or actual_record.get(field) is not None:
            issues.append(
                {
                    "code": "missing_field" if field not in actual_record else "value_mismatch",
                    "path": path,
                    "expected": None,
                    "actual": actual_record.get(field),
                }
            )
        return
    if not isinstance(expected_value, str) or not expected_value.strip():
        issues.append(
            {
                "code": "missing_field",
                "path": path.replace("dimensions.solids", "solids.solids", 1),
                "expected": "non-empty string",
                "actual": expected_value,
            }
        )
        return
    actual_value = actual_record.get(field)
    if actual_value is None:
        issues.append(
            {
                "code": "missing_field",
                "path": path,
                "expected": expected_value,
                "actual": None,
            }
        )
    elif actual_value != expected_value:
        issues.append(
            {
                "code": "value_mismatch",
                "path": path,
                "expected": expected_value,
                "actual": actual_value,
            }
        )


def _relationship_fields(
    expected_record: Mapping[str, Any],
    actual_record: Mapping[str, Any],
) -> set[str]:
    required: set[str] = set()
    semantic_tokens = {
        str(expected_record.get("role", "")).strip().casefold(),
        str(expected_record.get("primitive", "")).strip().casefold(),
    }
    if any(token == "patch" or token.endswith("_patch") for token in semantic_tokens):
        required.add("parent_layer")
    if "open_region" in semantic_tokens:
        required.add("boundary")

    for record in (expected_record, actual_record):
        declared = record.get("required_relationships")
        if _is_array(declared):
            required.update(
                item.strip()
                for item in declared
                if isinstance(item, str) and item.strip()
            )
        for field in _KNOWN_RELATIONSHIP_FIELDS:
            if _is_nonempty_relationship(record.get(field)):
                required.add(field)
    return required


def _is_nonempty_relationship(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def _require_mapping(
    parent: Mapping[str, Any],
    field: str,
    parent_path: str,
    issues: list[dict[str, object]],
) -> Mapping[str, Any] | None:
    value = parent.get(field)
    path = f"{parent_path}.{field}"
    if not isinstance(value, Mapping):
        issues.append(
            {
                "code": "missing_field" if value is None else "invalid_type",
                "path": path,
                "expected": "object",
                "actual": value if value is None else _type_name(value),
            }
        )
        return None
    return value


def _require_nonempty_string(
    parent: Mapping[str, Any],
    field: str,
    parent_path: str,
    issues: list[dict[str, object]],
) -> None:
    value = parent.get(field)
    if not isinstance(value, str) or not value.strip():
        issues.append(
            {
                "code": "missing_field" if value is None else "invalid_value",
                "path": f"{parent_path}.{field}",
                "expected": "non-empty string",
                "actual": value,
            }
        )


def _require_frequency(
    payload: Mapping[str, Any],
    path: str,
    issues: list[dict[str, object]],
) -> None:
    value = payload.get("value")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        issues.append(
            {
                "code": "missing_field" if value is None else "invalid_value",
                "path": f"{path}.value",
                "expected": "finite number",
                "actual": value,
            }
        )
    _require_nonempty_string(payload, "unit", path, issues)


def _is_array(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, Mapping):
        return "object"
    if _is_array(value):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__
