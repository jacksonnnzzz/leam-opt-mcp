"""Fail-closed S11 gates for Khan et al.'s proposed single element."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


THRESHOLD_DB = -10.0
TARGETS = (
    {
        "name": "lower_28ghz_band",
        "window_ghz": (24.0, 30.0),
        "resonance_ghz": 26.7,
        "resonance_error_max_ghz": 0.6,
        "minimum_s11_db_max": -15.0,
        "band_ghz": (24.86, 28.65),
        "band_edge_error_max_ghz": 0.5,
    },
    {
        "name": "upper_38ghz_band",
        "window_ghz": (35.0, 42.0),
        "resonance_ghz": 38.6,
        "resonance_error_max_ghz": 0.6,
        "minimum_s11_db_max": -20.0,
        "band_ghz": (36.24, 40.82),
        "band_edge_error_max_ghz": 0.5,
    },
)


def validate_paper_targets(curve_path: str | Path) -> dict[str, Any]:
    curve = Path(curve_path).expanduser().resolve()
    frequency, values = _read_curve(curve)
    if frequency[0] > 24.0 or frequency[-1] < 42.0:
        raise ValueError("S11 curve does not cover both published bands")

    checks: list[dict[str, Any]] = []
    observed_bands: list[dict[str, Any]] = []
    for target in TARGETS:
        minimum = _local_minimum_in_window(frequency, values, target["window_ghz"])
        band = (
            _bandwidth_at_index(frequency, values, THRESHOLD_DB, minimum[0])
            if minimum is not None
            else None
        )
        resonance_error = (
            abs(minimum[1] - target["resonance_ghz"])
            if minimum is not None
            else None
        )
        lower_error = abs(band[0] - target["band_ghz"][0]) if band else None
        upper_error = abs(band[1] - target["band_ghz"][1]) if band else None
        prefix = target["name"]
        checks.extend(
            [
                _check(f"{prefix}.local_minimum_exists", minimum is not None, {"window_ghz": list(target["window_ghz"])}, _minimum_record(minimum)),
                _check(f"{prefix}.resonance_error_ghz", resonance_error is not None and resonance_error <= target["resonance_error_max_ghz"], {"maximum_ghz": target["resonance_error_max_ghz"], "target_ghz": target["resonance_ghz"]}, resonance_error),
                _check(f"{prefix}.minimum_s11_db", minimum is not None and minimum[2] <= target["minimum_s11_db_max"], {"maximum_db": target["minimum_s11_db_max"]}, minimum[2] if minimum else None),
                _check(f"{prefix}.minus_10db_band_available", band is not None, {"target_ghz": list(target["band_ghz"])}, list(band) if band else None),
                _check(f"{prefix}.lower_edge_error_ghz", lower_error is not None and lower_error <= target["band_edge_error_max_ghz"], {"maximum_ghz": target["band_edge_error_max_ghz"]}, lower_error),
                _check(f"{prefix}.upper_edge_error_ghz", upper_error is not None and upper_error <= target["band_edge_error_max_ghz"], {"maximum_ghz": target["band_edge_error_max_ghz"]}, upper_error),
            ]
        )
        observed_bands.append(
            {
                "name": prefix,
                "resonance": _minimum_record(minimum),
                "minus_10db_band_ghz": list(band) if band else None,
            }
        )

    passed = all(check["passed"] for check in checks)
    return {
        "schema_version": "1.0",
        "benchmark_id": "khan_2024_28_38ghz_monopole",
        "validation_scope": "hfss_local_reference_against_single_element_paper_targets",
        "status": "passed" if passed else "failed",
        "passed": passed,
        "curve": str(curve),
        "frequency_range_ghz": [frequency[0], frequency[-1]],
        "expected": {
            "threshold_db": THRESHOLD_DB,
            "bands": [
                {
                    "name": item["name"],
                    "resonance_ghz": item["resonance_ghz"],
                    "minimum_s11_db_max": item["minimum_s11_db_max"],
                    "minus_10db_band_ghz": list(item["band_ghz"]),
                }
                for item in TARGETS
            ],
        },
        "observed": {"bands": observed_bands},
        "checks": checks,
        "interpretation": (
            "Passing establishes a local HFSS reference only under the labelled "
            "coordinate, port, conductor, boundary, and solver assumptions."
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
    parser = argparse.ArgumentParser(description="Compare a Khan HFSS curve with paper targets.")
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
