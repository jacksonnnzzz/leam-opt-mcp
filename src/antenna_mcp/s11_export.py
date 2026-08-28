from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Any


_DB_SELF_REFLECTION = re.compile(
    r"\s*dB\s*\(\s*S\s*\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*\)\s*\)\s*",
    flags=re.IGNORECASE,
)


def frequency_to_ghz(value: object, unit: object | None = None) -> float:
    """Convert an AEDT frequency value to GHz without guessing numeric units."""
    text = str(value).strip().lower()
    factors = {"ghz": 1.0, "mhz": 1e-3, "khz": 1e-6, "hz": 1e-9}
    for suffix, factor in factors.items():
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor
    normalized_unit = str(unit or "").strip().lower()
    if normalized_unit not in factors:
        raise ValueError(
            f"unable to convert numeric frequency {value!r} with unit {unit!r} to GHz"
        )
    return float(text) * factors[normalized_unit]


def is_db_self_reflection(expression: object) -> bool:
    match = _DB_SELF_REFLECTION.fullmatch(str(expression))
    return bool(match and match.group(1).strip() == match.group(2).strip())


def select_unique_s11_expression(traces: list[object]) -> str:
    self_reflections = [str(trace) for trace in traces if is_db_self_reflection(trace)]
    if len(self_reflections) != 1:
        raise RuntimeError(
            "expected exactly one dB self-reflection trace; "
            f"found {self_reflections!r} in available traces {traces!r}"
        )
    return self_reflections[0]


def export_s11_curve(
    hfss: Any,
    destination: Path,
    *,
    setup_sweep: str = "Setup1 : Sweep1",
) -> dict[str, object]:
    """Export the unique solved dB(S(i,i)) trace without solving or saving AEDT."""
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing S11 curve: {destination}")

    traces = list(
        hfss.get_traces_for_plot(
            get_self_terms=True,
            get_mutual_terms=False,
            category="dB(S",
        )
    )
    expression = select_unique_s11_expression(traces)
    data = hfss.post.get_solution_data(
        expressions=expression,
        setup_sweep_name=setup_sweep,
        primary_sweep_variable="Freq",
    )
    if data is None:
        raise RuntimeError(f"HFSS returned no solution data for {setup_sweep!r}")

    raw_frequencies, raw_values = data.get_expression_data(expression, formula="real")
    if len(raw_frequencies) != len(raw_values) or len(raw_frequencies) < 3:
        raise RuntimeError("HFSS returned an invalid S11 curve")
    primary_sweep = getattr(data, "primary_sweep", "Freq") or "Freq"
    sweep_units = getattr(data, "units_sweeps", {}) or {}
    frequency_unit = sweep_units.get(primary_sweep)
    points = [
        (frequency_to_ghz(frequency, frequency_unit), float(value))
        for frequency, value in zip(raw_frequencies, raw_values)
    ]
    if not all(math.isfinite(item) for point in points for item in point):
        raise RuntimeError("HFSS returned non-finite S11 data")
    if any(right[0] <= left[0] for left, right in zip(points, points[1:])):
        raise RuntimeError("HFSS returned non-increasing S11 frequencies")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_ghz", "s11_db"])
        writer.writerows(points)

    minimum_frequency, minimum_s11 = min(points, key=lambda point: point[1])
    return {
        "expression": expression,
        "setup_sweep": setup_sweep,
        "point_count": len(points),
        "frequency_range_ghz": [points[0][0], points[-1][0]],
        "minimum_frequency_ghz": minimum_frequency,
        "minimum_s11_db": minimum_s11,
        "output": str(destination),
    }
