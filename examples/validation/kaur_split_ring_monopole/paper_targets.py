"""Evaluate solved Kaur-case S11 curves against explicit paper targets.

The publication reports VSWR.  The runner exports ``dB(S11)``; the exact
conversion ``VSWR=2 <=> |S11|=-9.542425... dB`` is used rather than silently
rounding the physical boundary to -10 dB.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any


VSWR_TWO_S11_DB = 20.0 * math.log10((2.0 - 1.0) / (2.0 + 1.0))
BOUNDARY_TOLERANCE_DB = 1e-6
SIMULATED_PEAK_VSWR_RELATIVE_ERROR_MAX = 0.20

TARGETS: dict[str, dict[str, Any]] = {
    "baseline": {
        "matched_bands_ghz": [[3.0, 12.0]],
        "notch_band_ghz": None,
        "notch_center_ghz": None,
        "simulated_peak_vswr": None,
        "measured_peak_vswr": None,
    },
    "wlan_notch": {
        "matched_bands_ghz": [[3.0, 5.15], [5.81, 12.0]],
        "notch_band_ghz": [5.15, 5.81],
        "notch_center_ghz": 5.3,
        "simulated_peak_vswr": 5.44,
        "measured_peak_vswr": 4.97,
    },
    "xband_notch": {
        "matched_bands_ghz": [[3.0, 7.16], [7.71, 12.0]],
        "notch_band_ghz": [7.16, 7.71],
        "notch_center_ghz": 7.4,
        "simulated_peak_vswr": 5.66,
        "measured_peak_vswr": 4.66,
    },
}


def evaluate_paper_targets(curve_path: str | Path, case: str) -> dict[str, Any]:
    """Return a machine-readable evaluation of one dB(S11) CSV."""
    if case not in TARGETS:
        raise ValueError(f"unknown Kaur paper-target case: {case!r}")
    path = Path(curve_path).expanduser().resolve()
    frequency, s11_db = _read_curve(path)
    target = TARGETS[case]
    if frequency[0] > 3.0 or frequency[-1] < 12.0:
        raise ValueError("S11 curve must cover the complete 3-12 GHz paper range")

    checks: list[dict[str, Any]] = []
    matched_maxima: list[dict[str, Any]] = []
    for index, band in enumerate(target["matched_bands_ghz"], start=1):
        samples = _interval_samples(frequency, s11_db, *band)
        maximum = max(value for _, value in samples)
        matched_maxima.append({"band_ghz": band, "maximum_s11_db": maximum})
        checks.append(
            _check(
                f"matched_band_{index}.s11_at_or_below_vswr_2",
                maximum <= VSWR_TWO_S11_DB + BOUNDARY_TOLERANCE_DB,
                {"maximum_db": VSWR_TWO_S11_DB, "band_ghz": band},
                maximum,
            )
        )

    notch_observed: dict[str, Any] | None = None
    notch_band = target["notch_band_ghz"]
    if notch_band is not None:
        notch_samples = _interval_samples(frequency, s11_db, *notch_band)
        peak_frequency, peak_s11 = max(notch_samples, key=lambda point: point[1])
        # A rejected band is high reflection: the whole stated interval must
        # remain at or above the VSWR=2 boundary.  This is intentionally the
        # opposite inequality from the passband check.
        notch_minimum = min(value for _, value in notch_samples)
        center_error = abs(peak_frequency - float(target["notch_center_ghz"]))
        notch_observed = {
            "peak_frequency_ghz": peak_frequency,
            "peak_s11_db": peak_s11,
            "peak_vswr": _s11_db_to_vswr(peak_s11),
            "minimum_s11_in_rejection_band_db": notch_minimum,
        }
        checks.extend(
            [
                _check(
                    "notch.entire_reported_band_at_or_above_vswr_2",
                    notch_minimum >= VSWR_TWO_S11_DB - BOUNDARY_TOLERANCE_DB,
                    {"minimum_db": VSWR_TWO_S11_DB, "band_ghz": notch_band},
                    notch_minimum,
                ),
                _check(
                    "notch.peak_center_absolute_error_ghz",
                    center_error <= 0.15,
                    {"maximum_ghz": 0.15, "target_ghz": target["notch_center_ghz"]},
                    center_error,
                ),
                _check(
                    "notch.peak_is_high_reflection",
                    peak_s11 >= VSWR_TWO_S11_DB - BOUNDARY_TOLERANCE_DB,
                    {"minimum_db": VSWR_TWO_S11_DB},
                    peak_s11,
                ),
                _check(
                    "notch.simulated_peak_vswr_relative_error",
                    abs(notch_observed["peak_vswr"] - float(target["simulated_peak_vswr"]))
                    / float(target["simulated_peak_vswr"])
                    <= SIMULATED_PEAK_VSWR_RELATIVE_ERROR_MAX,
                    {
                        "maximum": SIMULATED_PEAK_VSWR_RELATIVE_ERROR_MAX,
                        "target_vswr": target["simulated_peak_vswr"],
                    },
                    abs(notch_observed["peak_vswr"] - float(target["simulated_peak_vswr"]))
                    / float(target["simulated_peak_vswr"]),
                ),
            ]
        )

    passed = all(check["passed"] for check in checks)
    return {
        "schema_version": "1.0",
        "benchmark_id": f"kaur_2021_{case}",
        "case": case,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "curve": str(path),
        "quantity": "dB(S11) self-reflection",
        "frequency_unit": "GHz",
        "vswr_two_s11_db": VSWR_TWO_S11_DB,
        "simulated_peak_vswr_relative_error_max": SIMULATED_PEAK_VSWR_RELATIVE_ERROR_MAX,
        "paper_target": target,
        "observed": {"matched_bands": matched_maxima, "notch": notch_observed},
        "checks": checks,
        "interpretation": (
            "Passbands exclude the intended notch window. A notch is evaluated as "
            "high reflection (S11 at or above the VSWR=2 boundary), not as good matching."
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
    if any(right[0] <= left[0] for left, right in zip(points, points[1:])):
        raise ValueError(f"S11 frequencies must be strictly increasing: {path}")
    return [point[0] for point in points], [point[1] for point in points]


def _interval_samples(
    frequency: list[float], values: list[float], lower: float, upper: float
) -> list[tuple[float, float]]:
    if frequency[0] > lower or frequency[-1] < upper:
        raise ValueError(f"S11 curve does not cover {lower}-{upper} GHz")
    points = [(lower, _interpolate(frequency, values, lower))]
    points.extend(
        (item, value)
        for item, value in zip(frequency, values)
        if lower < item < upper
    )
    points.append((upper, _interpolate(frequency, values, upper)))
    return points


def _interpolate(frequency: list[float], values: list[float], target: float) -> float:
    for index, current in enumerate(frequency):
        if current == target:
            return values[index]
        if current > target:
            if index == 0:
                raise ValueError("interpolation target below curve")
            previous = frequency[index - 1]
            fraction = (target - previous) / (current - previous)
            return values[index - 1] + fraction * (values[index] - values[index - 1])
    raise ValueError("interpolation target above curve")


def _s11_db_to_vswr(s11_db: float) -> float:
    magnitude = 10.0 ** (s11_db / 20.0)
    if magnitude >= 1.0:
        return math.inf
    return (1.0 + magnitude) / (1.0 - magnitude)


def _check(path: str, passed: bool, limit: Any, actual: Any) -> dict[str, Any]:
    return {"path": path, "passed": bool(passed), "limit": limit, "actual": actual}
