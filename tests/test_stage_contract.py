import pytest

from antenna_mcp.stage_contract import (
    StageOwnershipError,
    validate_stage_ownership,
)


def test_model_stages_accept_geometry_but_reject_other_stage_work():
    validate_stage_ownership(
        """hfss["width"] = "10mm"
material = hfss.materials.add_material("custom")
material.permittivity = 2.2
box = hfss.modeler.create_box([0, 0, 0], [1, 1, 1], name="box")
box.move([1, 0, 0])
""",
        "model_3d",
    )

    source = """hfss.modeler.create_box([0, 0, 0], [1, 1, 1], name="box")
hfss.modeler.subtract("ground", "tool", keep_originals=False)
hfss.assign_radiation_boundary_to_objects(["Region"])
hfss.create_setup("Setup1")
hfss.analyze_setup("Setup1")
"""
    with pytest.raises(StageOwnershipError) as captured:
        validate_stage_ownership(source, "model_3d")

    categories = {item.category for item in captured.value.violations}
    assert {"boolean", "boundary", "solver", "analyze"} <= categories
    assert captured.value.to_dict()["error"] == "stage_ownership_violation"


def test_boolean_accepts_only_boolean_and_cleanup_and_requires_boolean():
    validate_stage_ownership(
        'hfss.modeler.subtract("ground", "tool", keep_originals=False)\n'
        'hfss.modeler.delete("tool")\n',
        "boolean",
    )

    with pytest.raises(StageOwnershipError) as captured:
        validate_stage_ownership('hfss.modeler.delete("tool")\n', "boolean")
    assert {item.code for item in captured.value.violations} == {
        "missing_boolean_operation"
    }

    source = """tool = hfss.modeler.create_cylinder(
    origin=[0, 0, 0], axis="Z", radius=1, height=1, name="tool"
)
hfss["radius"] = "1mm"
hfss.materials.add_material("air")
hfss.modeler.subtract("ground", "tool")
hfss.create_setup("Setup1")
"""
    with pytest.raises(StageOwnershipError) as captured:
        validate_stage_ownership(source, "boolean")
    categories = {item.category for item in captured.value.violations}
    assert {"geometry", "parameter", "material", "solver"} <= categories


def test_simulation_setup_accepts_solver_work_but_rejects_geometry_and_rebuilds():
    validate_stage_ownership(
        """hfss.solution_type = "Modal"
faces = hfss.modeler.get_object_faces("feed")
hfss.assign_wave_port(faces[0], name="P1")
setup = hfss.create_setup("Setup1")
setup.props["Frequency"] = "10GHz"
setup.update()
hfss.create_linear_count_sweep(setup=setup.name, start_frequency=8, stop_frequency=12)
hfss.insert_infinite_sphere(name="InfiniteSphere1")
""",
        "simulation_setup",
    )

    source = """hfss["width"] = "10mm"
hfss.materials.add_material("air")
hfss.modeler.create_rectangle("XY", [0, 0, 0], [1, 1], name="port_sheet")
hfss.modeler.subtract("ground", "tool")
hfss.assign_wave_port(1, name="P1")
"""
    with pytest.raises(StageOwnershipError) as captured:
        validate_stage_ownership(source, "simulation_setup")
    categories = {item.category for item in captured.value.violations}
    assert {"parameter", "material", "geometry", "boolean"} <= categories


def test_aliases_cannot_bypass_stage_ownership():
    source = """modeler = hfss.modeler
make = modeler.create_box
make([0, 0, 0], [1, 1, 1], name="box")
hfss.modeler.subtract("ground", "tool")
"""
    with pytest.raises(StageOwnershipError) as captured:
        validate_stage_ownership(source, "boolean")
    violation = next(item for item in captured.value.violations if item.category == "geometry")
    assert violation.api == "hfss.modeler.create_box"


def test_unknown_hfss_apis_and_syntax_errors_fail_closed():
    with pytest.raises(StageOwnershipError, match="unknown HFSS APIs are rejected") as captured:
        validate_stage_ownership("hfss.do_something_unclassified()", "model_3d")
    assert captured.value.violations[0].code == "unclassified_hfss_api"

    with pytest.raises(StageOwnershipError) as captured:
        validate_stage_ownership("if:", "model_3d")
    assert captured.value.violations[0].code == "syntax_error"


@pytest.mark.parametrize("stage", ["unknown", "materials"])
def test_unsupported_stage_is_structured(stage):
    with pytest.raises(StageOwnershipError) as captured:
        validate_stage_ownership("hfss.modeler.fit_all()", stage)
    assert captured.value.violations[0].code == "unsupported_stage"
