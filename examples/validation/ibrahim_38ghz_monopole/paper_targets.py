"""Evaluate a solved HFSS curve against Ibrahim et al.'s explicit S11 targets."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


TARGET_RESONANCE_GHZ = 38.0
TARGET_BAND_GHZ = (36.5, 39.5)
THRESHOLD_DB = -10.0
MINIMUM_S11_LIMIT_DB = -25.0


def validate_paper_targets(curve_path: str | Path) -> dict[str, Any]:
    """Return a fail-closed report for the selected single-element paper scope."""
    curve = Path(curve_path).expanduser().resolve()
    frequency, values = _read_curve(curve)
    if frequency[0] > TARGET_BAND_GHZ[0] or frequency[-1] < TARGET_BAND_GHZ[1]:
        raise ValueError("S11 curve does not cover the reported 36.5-39.5 GHz band")

    minimum = _local_minimum_in_window(frequency, values, (37.0, 39.0))
    band = _bandwidth_at_index(frequency, values, THRESHOLD_DB, minimum[0]) if minimum else None
    resonance_error = (
        abs(minimum[1] - TARGET_RESONANCE_GHZ) / TARGET_RESONANCE_GHZ
        if minimum
        else None
    )
    lower_error = abs(band[0] - TARGET_BAND_GHZ[0]) if band else None
    upper_error = abs(band[1] - TARGET_BAND_GHZ[1]) if band else None
    target_width = TARGET_BAND_GHZ[1] - TARGET_BAND_GHZ[0]
    width_error = (
        abs((band[1] - band[0]) - target_width) / target_width if band else None
    )
    checks = [
        _check("resonance.local_minimum_exists", minimum is not None, {"window_ghz": [37.0, 39.0]}, _minimum_record(minimum)),
        _check("resonance.relative_error", resonance_error is not None and resonance_error <= 0.01, {"maximum": 0.01, "target_ghz": 38.0}, resonance_error),
        _check("resonance.minimum_s11_db", minimum is not None and minimum[2] <= MINIMUM_S11_LIMIT_DB, {"maximum_db": MINIMUM_S11_LIMIT_DB, "reported_approximately_db": -30.0}, minimum[2] if minimum else None),
        _check("minus_10db_band.available", band is not None, {"target_ghz": list(TARGET_BAND_GHZ)}, list(band) if band else None),
        _check("minus_10db_band.lower_edge_absolute_error_ghz", lower_error is not None and lower_error <= 0.3, {"maximum_ghz": 0.3, "target_ghz": TARGET_BAND_GHZ[0]}, lower_error),
        _check("minus_10db_band.upper_edge_absolute_error_ghz", upper_error is not None and upper_error <= 0.3, {"maximum_ghz": 0.3, "target_ghz": TARGET_BAND_GHZ[1]}, upper_error),
        _check("minus_10db_band.bandwidth_relative_error", width_error is not None and width_error <= 0.1, {"maximum": 0.1, "target_width_ghz": target_width}, width_error),
    ]
    passed = all(check["passed"] for check in checks)
    return {
        "schema_version": "1.0",
        "benchmark_id": "ibrahim_2023_38ghz_monopole",
        "validation_scope": "hfss_local_reference_against_explicit_single_element_paper_targets",
        "status": "passed" if passed else "failed",
        "passed": passed,
        "curve": str(curve),
        "frequency_range_ghz": [frequency[0], frequency[-1]],
        "expected": {
            "resonance_ghz": TARGET_RESONANCE_GHZ,
            "minimum_s11_db_max": MINIMUM_S11_LIMIT_DB,
            "reported_minimum_s11_db": -30.0,
            "minus_10db_band_ghz": list(TARGET_BAND_GHZ),
        },
        "observed": {
            "resonance": _minimum_record(minimum),
            "minus_10db_band_ghz": list(band) if band else None,
        },
        "checks": checks,
        "interpretation": (
            "Passing establishes an accepted local HFSS reference under the labelled "
            "assumptions. It does not by itself validate an independently generated candidate."
        ),
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
    low = _crossing(frequency[left - 1], values[left - 1], frequency[left], values[left], threshold)
    high = _crossing(frequency[right], values[right], frequency[right + 1], values[right + 1], threshold)
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
    parser = argparse.ArgumentParser(description="Compare an Ibrahim HFSS curve with the paper targets.")
    parser.add_argument("--curve", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    if output.exists():
        parser.error(f"refusing to overwrite existing paper-target report: {output}")
    report = validate_paper_targets(args.curve)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    report["report"] = str(output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
