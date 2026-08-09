STAGES = (
    "source_analysis",
    "parameters",
    "materials",
    "solids",
    "dimensions",
    "model_3d",
    "model_2d",
    "boolean",
    "simulation_spec",
    "simulation_setup",
    "optimization_spec",
)

SYSTEM_PROMPT = """You are an antenna engineer and an expert in Ansys HFSS through PyAEDT.
Convert the antenna intent into inspectable, simulator-facing artifacts. Work only on the
requested stage. Preserve parameter names across stages. Use explicit Cartesian coordinates,
units, material names, object names, and boolean operands. Never execute code. Never include
filesystem, network, shell, subprocess, eval, exec, or dynamic import operations.
"""

STAGE_INSTRUCTIONS = {
    "source_analysis": """Return one JSON object with exactly these top-level keys:
input_summary, antenna_type, coordinate_system, components, parameters, operations,
derived_relations, and uncertainties. Treat images and PDF pages as evidence, not as infallible ground truth.
If the source contains multiple distinct antennas, analyze only the design named in the antenna
intent and never combine components or dimensions across examples. If no design is named, set
antenna_type to null and report the candidate designs in uncertainties instead of merging them.
coordinate_system must be an object with plane, origin, and axes fields.
For every component report name, role, primitive, material, geometric evidence, and confidence
(0 to 1). For every visible or textual parameter report symbol, value (null if unreadable), unit,
geometric meaning, evidence source, and confidence. Record unite/subtract/intersect operations in
execution order. Record every explicit or reviewed derived equation in derived_relations with
claim_id, expression, symbols, evidence, and confidence. Put illegible labels, conflicting dimensions, inferred symmetry, missing
thicknesses, and other assumptions in uncertainties. Never invent an unreadable value.""",
    "parameters": "Return JSON with a parameters array: name, value, unit, description, optimizable.",
    "materials": "Return JSON with a materials array: name, permittivity, permeability, conductivity, loss_tangent when known.",
    "solids": "Return JSON with a solids array: name, role, primitive, material, coordinate_system, dependencies.",
    "dimensions": "Return JSON with explicit coordinate-based dimensions for every solid. Do not use words such as centered or above without formulas.",
    "model_3d": "Return only a PyAEDT Python code fragment that creates all 3D primitives. Assume an existing variable named hfss.",
    "model_2d": "Return only a PyAEDT Python code fragment for sheets, polylines, extrusions, or rotations. Assume an existing variable named hfss.",
    "boolean": "Return only a PyAEDT Python code fragment that performs unite, subtract, intersect, and cleanup operations. Assume an existing variable named hfss.",
    "simulation_spec": """Return JSON with solution_type, frequency_band, excitation,
open_region, far_field, setup, sweep, required_reports, and uncertainties. Define the port
sheet plane, dimensions, integration/reference conductors, radiation clearance, adaptive
frequency, and sweep range explicitly. Do not silently guess missing excitation geometry;
record every engineering assumption in uncertainties.""",
    "simulation_setup": """Return only a PyAEDT Python fragment that implements the reviewed
simulation_spec using an existing variable named hfss. Create the explicit port sheet,
excitation, open/radiation region, driven setup, frequency sweep, and infinite sphere requested
by the spec. Do not run analyze, export data, access files, or close AEDT.""",
    "optimization_spec": """Return one JSON object matching this contract: design_name,
setup_sweep, parameters, metrics, max_trials, seed, strategy, initial_samples,
candidate_pool_size, exploration_weight, initial_points, save_best_as. Parameters are arrays of
name, lower, upper, unit. Metrics contain name, expression, reducer, optional frequency_ghz or
frequency_min_ghz/frequency_max_ghz, report_category, context, variations, goal, target when
required, and weight. Derive conservative
search bounds from the reviewed parameter baseline and preserve all geometry constraints through
derived HFSS variables. Use upper_bound/lower_bound hinge goals for engineering specifications.
Put the current baseline parameter values in initial_points as the first evaluation. Do not invent
reports or far-field contexts that the simulation_spec does not create.""",
}
