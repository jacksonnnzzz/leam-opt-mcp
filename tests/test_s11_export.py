from __future__ import annotations

import csv
from pathlib import Path

import pytest

from antenna_mcp.s11_export import (
    export_s11_curve,
    frequency_to_ghz,
    is_db_self_reflection,
    select_unique_s11_expression,
)


class _Data:
    primary_sweep = "Freq"
    units_sweeps = {"Freq": "MHz"}

    def get_expression_data(self, expression, formula):
        assert expression == "dB(S(Probe_Port_T1,Probe_Port_T1))"
        assert formula == "real"
        return [8000, 10000, 12000], [-1.0, -16.0, -2.0]


class _Post:
    def get_solution_data(self, **kwargs):
        assert kwargs["setup_sweep_name"] == "Setup1 : Sweep1"
        return _Data()


class _Hfss:
    post = _Post()

    def get_traces_for_plot(self, **kwargs):
        assert kwargs == {
            "get_self_terms": True,
            "get_mutual_terms": False,
            "category": "dB(S",
        }
        return ["dB(S(Probe_Port_T1,Probe_Port_T1))"]


def test_frequency_to_ghz_requires_explicit_unit_for_numeric_values():
    assert frequency_to_ghz("10GHz") == 10.0
    assert frequency_to_ghz(10000, "MHz") == 10.0
    with pytest.raises(ValueError, match="unable to convert"):
        frequency_to_ghz(10)


def test_self_reflection_selection_is_strict_and_unambiguous():
    assert is_db_self_reflection(" dB( S( Port_T1 , Port_T1 ) ) ")
    assert not is_db_self_reflection("dB(S(P1,P2))")
    assert not is_db_self_reflection("mag(S(P1,P1))")
    assert select_unique_s11_expression(["dB(S(P1,P1))"]) == "dB(S(P1,P1))"
    with pytest.raises(RuntimeError, match="exactly one"):
        select_unique_s11_expression(["dB(S(P1,P1))", "dB(S(P2,P2))"])


def test_export_s11_curve_writes_normalized_csv_and_refuses_overwrite(tmp_path: Path):
    destination = tmp_path / "candidate.csv"
    result = export_s11_curve(_Hfss(), destination)
    assert result["point_count"] == 3
    assert result["frequency_range_ghz"] == [8.0, 12.0]
    assert result["minimum_frequency_ghz"] == 10.0
    assert result["minimum_s11_db"] == -16.0
    with destination.open(newline="", encoding="utf-8") as stream:
        assert list(csv.reader(stream)) == [
            ["frequency_ghz", "s11_db"],
            ["8.0", "-1.0"],
            ["10.0", "-16.0"],
            ["12.0", "-2.0"],
        ]
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        export_s11_curve(_Hfss(), destination)
