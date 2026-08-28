"""Frozen local implementation of the official PyAEDT probe-fed patch example.

Importing this module does not start AEDT. Call ``build_reference(hfss)`` only with a
deliberately created, empty HFSS Terminal design.
"""


def build_reference(hfss):
    from ansys.aedt.core.modeler.advanced_cad.stackup_3d import Stackup3D

    hfss.modeler.model_units = "mm"
    stackup = Stackup3D(hfss)
    ground = stackup.add_ground_layer(
        "ground", material="copper", thickness=0.035, fill_material="air"
    )
    stackup.add_dielectric_layer(
        "dielectric", thickness="0.5mm", material="Duroid (tm)"
    )
    signal = stackup.add_signal_layer(
        "signal", material="copper", thickness=0.035, fill_material="air"
    )
    patch = signal.add_patch(
        patch_length=9.57,
        patch_width=9.25,
        patch_name="Patch",
        frequency=1.0e10,
    )
    stackup.resize_around_element(patch)
    region = hfss.modeler.create_region([3, 3, 3, 3, 3, 3], is_percentage=False)
    hfss.assign_radiation_boundary_to_objects(region)
    patch.create_probe_port(
        ground,
        rel_x_offset=0.485,
        rel_y_offset=0.0,
        r=0.01,
        name="Probe",
    )

    setup = hfss.create_setup(
        name="Setup1",
        setup_type="HFSSDriven",
        Frequency="10GHz",
    )
    setup.create_frequency_sweep(
        unit="GHz",
        name="Sweep1",
        start_frequency=8,
        stop_frequency=12,
        sweep_type="Interpolating",
    )
    return hfss
