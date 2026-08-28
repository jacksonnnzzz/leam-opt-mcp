"""Compare a solved HFSS curve with explicit text targets from Yeo (2019).

This is deliberately separate from candidate-to-local-reference validation. The
paper used CST and omitted several model details, so a failed check identifies a
cross-solver reproduction mismatch; it does not by itself identify its cause.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


CASE_KEYS = {
    "conventional": "conventional_inset_fed_patch",
    "scaled_slot_loaded": "scaled_slot_loaded_patch",
}

RESONANCE_WINDOWS_GHZ = {
    "conventional": [(2.35, 2.65)],
    "scaled_slot_loaded": [(2.35, 2.65), (3.25, 3.65)],
}


def validate_paper_targets(
    curves: dict[str, str | Path],
    *,
    targets_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return a machine-readable paper-target report for selected case curves."""
    source = (
        Path(targets_path).expanduser().resolve()
        if targets_path
        else Path(__file__).resolve().parent / "reference_data" / "literature_targets.json"
    )
    targets = json.loads(source.read_text("utf-8"))
    reports: dict[str, Any] = {}
    for case, curve_path in curves.items():
        if case not in CASE_KEYS:
            raise ValueError(f"unknown Yeo paper-target case: {case!r}")
        curve = Path(curve_path).expanduser().resolve()
        frequency, s11_db = _read_curve(curve)
        reports[case] = _evaluate_case(
            case,
            frequency,
            s11_db,
            targets["cases"][CASE_KEYS[case]]["s11_targets"],
            curve,
        )
    passed = bool(reports) and all(report["passed"] for report in reports.values())
    return {
        "schema_version": "1.0",
        "benchmark_id": targets["benchmark_id"],
        "validation_scope": "hfss_local_reference_against_explicit_paper_targets",
        "status": "passed" if passed else "failed",
        "passed": passed,
        "source_targets": str(source),
        "criteria": {
            "resonance_relative_error_max": 0.01,
            "first_band_edge_absolute_error_max": (
                "50% of the paper first-band width for each edge"
            ),
            "first_bandwidth_relative_error_max": 0.50,
            "threshold_db": -10.0,
            "note": "Project acceptance criteria, not universal cross-solver tolerances.",
        },
        "cases": reports,
        "interpretation": (
            "A failure is a CST-to-HFSS reproduction mismatch. Because the paper leaves "
            "conductor, port, boundary, mesh, and sweep details unresolved, it does not "
            "by itself prove that the extracted geometry is wrong."
        ),
    }


def _evaluate_case(
    case: str,
    frequency: list[float],
    s11_db: list[float],
    targets: dict[str, Any],
    curve: Path,
) -> dict[str, Any]:
    expected_resonances = [float(value) for value in targets["resonances_ghz"]]
    windows = RESONANCE_WINDOWS_GHZ[case]
    if len(expected_resonances) != len(windows):
        raise ValueError(f"resonance target/window mismatch for {case}")

    checks: list[dict[str, Any]] = []
    minima: list[tuple[int, float, float] | None] = []
    for index, (expected, window) in enumerate(zip(expected_resonances, windows), start=1):
        minimum = _local_minimum_in_window(frequency, s11_db, window)
        minima.append(minimum)
        exists = minimum is not None
        checks.append(
            _check(
                f"resonance_{index}.local_minimum_exists",
                exists,
                {"window_ghz": list(window)},
                _minimum_record(minimum),
            )
        )
        error = (
            abs(minimum[1] - expected) / max(abs(expected), math.ulp(1.0))
            if minimum is not None
            else None
        )
        checks.append(
            _check(
                f"resonance_{index}.relative_error",
                error is not None and error <= 0.01,
                {"maximum": 0.01, "target_ghz": expected},
                error,
            )
        )

    expected_band = tuple(float(value) for value in targets["first_minus_10db_band_ghz"])
    expected_width = expected_band[1] - expected_band[0]
    edge_limit = expected_width * 0.5
    first_band = (
        _bandwidth_at_index(frequency, s11_db, -10.0, minima[0][0])
        if minima and minima[0] is not None
        else None
    )
    checks.append(
        _check(
            "first_minus_10db_band.available",
            first_band is not None,
            {"threshold_db": -10.0, "target_ghz": list(expected_band)},
            list(first_band) if first_band else None,
        )
    )
    if first_band is None:
        lower_error = upper_error = width_error = None
    else:
        lower_error = abs(first_band[0] - expected_band[0])
        upper_error = abs(first_band[1] - expected_band[1])
        width_error = abs((first_band[1] - first_band[0]) - expected_width) / expected_width
    checks.extend(
        [
            _check(
                "first_minus_10db_band.lower_edge_absolute_error_ghz",
                lower_error is not None and lower_error <= edge_limit,
                {"maximum_ghz": edge_limit, "target_ghz": expected_band[0]},
                lower_error,
            ),
            _check(
                "first_minus_10db_band.upper_edge_absolute_error_ghz",
                upper_error is not None and upper_error <= edge_limit,
                {"maximum_ghz": edge_limit, "target_ghz": expected_band[1]},
                upper_error,
            ),
            _check(
                "first_minus_10db_band.bandwidth_relative_error",
                width_error is not None and width_error <= 0.50,
                {"maximum": 0.50, "target_width_ghz": expected_width},
                width_error,
            ),
        ]
    )
    passed = all(check["passed"] for check in checks)
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "curve": str(curve),
        "frequency_range_ghz": [frequency[0], frequency[-1]],
        "expected": {
            "resonances_ghz": expected_resonances,
            "first_minus_10db_band_ghz": list(expected_band),
        },
        "observed": {
            "resonances": [_minimum_record(minimum) for minimum in minima],
            "first_minus_10db_band_ghz": list(first_band) if first_band else None,
        },
        "checks": checks,
    }


def _read_curve(path: Path) -> tuple[list[float], list[float]]:
    if not path.is_file():
        raise FileNotFoundError(f"S11 curve does not exist: {path}")
    points: list[tuple[float, float]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"frequency_ghz", "s11_db"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"S11 CSV must contain {sorted(required)}: {path}")
        for line_number, row in enumerate(reader, start=2):
            try:
                point = (float(row["frequency_ghz"]), float(row["s11_db"]))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid S11 number at {path}:{line_number}") from exc
            if not all(math.isfinite(value) for value in point):
                raise ValueError(f"non-finite S11 number at {path}:{line_number}")
            points.append(point)
    if len(points) < 3:
        raise ValueError(f"S11 CSV requires at least three data rows: {path}")
    points.sort(key=lambda point: point[0])
    if any(right[0] <= left[0] for left, right in zip(points, points[1:])):
        raise ValueError(f"S11 frequencies must be unique: {path}")
    return [point[0] for point in points], [point[1] for point in points]


def _local_minimum_in_window(
    frequency: list[float], values: list[float], window: tuple[float, float]
) -> tuple[int, float, float] | None:
    candidates = [
        index
        for index in range(1, len(values) - 1)
        if window[0] < frequency[index] < window[1]
        and values[index] <= values[index - 1]
        and values[index] <= values[index + 1]
        and (values[index] < values[index - 1] or values[index] < values[index + 1])
    ]
    if not candidates:
        return None
    index = min(candidates, key=lambda candidate: values[candidate])
    return index, frequency[index], values[index]


def _bandwidth_at_index(
    frequency: list[float], values: list[float], threshold: float, resonance_index: int
) -> tuple[float, float] | None:
    if values[resonance_index] > threshold:
        return None
    left = right = resonance_index
    while left > 0 and values[left - 1] <= threshold:
        left -= 1
    while right < len(values) - 1 and values[right + 1] <= threshold:
        right += 1
    if left == 0 or right == len(values) - 1:
        return None
    low = _crossing(
        frequency[left - 1], values[left - 1], frequency[left], values[left], threshold
    )
    high = _crossing(
        frequency[right], values[right], frequency[right + 1], values[right + 1], threshold
    )
    return (low, high) if high > low else None


def _crossing(x1: float, y1: float, x2: float, y2: float, threshold: float) -> float:
    if y1 == y2:
        return x1
    return x1 + (threshold - y1) * (x2 - x1) / (y2 - y1)


def _minimum_record(minimum: tuple[int, float, float] | None) -> dict[str, float] | None:
    if minimum is None:
        return None
    return {"frequency_ghz": minimum[1], "s11_db": minimum[2]}


def _check(path: str, passed: bool, limit: Any, actual: Any) -> dict[str, Any]:
    return {"path": path, "passed": bool(passed), "limit": limit, "actual": actual}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare one solved Yeo HFSS S11 curve with explicit paper targets."
    )
    parser.add_argument("--case", choices=sorted(CASE_KEYS), required=True)
    parser.add_argument("--curve", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    if output.exists():
        parser.error(f"refusing to overwrite existing paper-target report: {output}")
    report = validate_paper_targets({args.case: args.curve})
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    report["report"] = str(output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
