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
requested stage. Treat source_analysis as an immutable evidence contract: preserve its parameter
symbols, values, units, component names, roles, primitives, and materials exactly in downstream
JSON. Never omit an evidenced record or introduce an unevidenced one. Use explicit Cartesian
coordinates, units, material names, object names, and boolean operands. Never execute code. Never
include filesystem, network, shell, subprocess, eval, exec, or dynamic import operations.
"""

_HFSS_FRAGMENT_CONTRACT = """Return only executable PyAEDT statements that run immediately
against the existing variable named hfss. This output is a code fragment, not a module or a
callable: never return def build, any other def or async def, a class, a lambda, an import, or
code that creates a new HFSS/AEDT session. Do not rebind hfss. Target the supported PyAEDT
0.18-0.26 API, including the installed 0.26.3 spellings: create_rectangle and create_circle
require orientation=; create_cylinder requires orientation= (never axis=);
get_faceid_from_position(position=..., assignment=...) replaces get_face_by_position;
use hfss.wave_port rather than assign_wave_port; create_linear_count_sweep uses unit= rather
than units=. PyAEDT 0.26.3 boundary methods use name= (never boundary_name=), wave_port
uses name= and reference= (never port_name= or reference_conductor=), a sweep uses name=
(never sweep_name=), sweep bounds use start_frequency=/stop_frequency= (never start=/stop=),
and HFSS setup frequency is the case-sensitive Frequency= native property. There is no
hfss.assign_perfecte method; use assign_perfecte_to_sheets or assign_perfect_e.
Never repeat work owned by another generation stage."""

STAGE_INSTRUCTIONS = {
    "source_analysis": """Return one JSON object with exactly these top-level keys:
input_summary, antenna_type, coordinate_system, components, parameters, operations,
derived_relations, and uncertainties. Treat images and PDF pages as evidence, not as infallible ground truth.
If the source contains multiple distinct antennas, analyze only the design named in the antenna
intent and never combine components or dimensions across examples. If no design is named, set
antenna_type to null and report the candidate designs in uncertainties instead of merging them.
For a text-only request, treat the antenna intent as source evidence and enumerate every stated
component and parameter here before any downstream stage; do not defer their discovery.
coordinate_system must be an object with plane, origin, and axes fields. Use the exact HFSS shape
{"plane":"XY","origin":[0,0,0],"axes":["X","Y","Z"]} when those facts are known. axes must
be either a JSON array of axis labels or null; never return axes as an object or a single string.
Likewise, origin should be a three-element JSON array or null, not a prose description.
input_summary must be a concise string, not an object. For every component report name, role,
primitive, material, geometric_evidence, and confidence (0 to 1). role and primitive must be stable
machine identifiers such as probe_inner or stackup_signal_layer, never prose; copy an attached
reference.objects role verbatim when it exists. If attached producer metadata includes
generation_evidence.source_contract, use its component_roles, component_material_semantics, and
required_relationships as the exact machine-readable source schema. Copy any
component_geometric_evidence object into the corresponding component's geometric_evidence field
verbatim; do not replace structured ranges, origins, sizes, or formulas with a prose summary.
Treat any resolved_semantics statements as resolved producer facts, not ambiguities. When the
evidence explicitly supplies them, also
report parent_layer, boundary, fill_material, body_material, and required_relationships as
structured component fields. Omit an optional field when it is unknown or inapplicable; never add
null placeholders. required_relationships, when present, is an array containing only field names
that are present and non-empty on the same component, for example ["parent_layer"]; never put prose
claims in that array. For every visible or textual parameter use exactly the fields symbol, value,
unit, geometric_meaning, evidence_source, and confidence. The key is evidence_source, never
evidence. For an attached frozen benchmark, the parameter set is exactly reference.parameters:
copy every listed name and add no others. Producer implementation constants such as resize
percentages, radius multipliers, feed-length multipliers, face-selection rules, and cap-thickness
formulas belong only in derived_relations or component geometric evidence unless they are also
explicitly listed in reference.parameters. Record unite/subtract/intersect operations in
execution order. For an attached frozen benchmark, copy every reference.operations record in
order, including helper-internal boolean operations and keep_originals flags; do not collapse or
omit them. Record every explicit or reviewed derived equation in derived_relations with
claim_id, expression, symbols, evidence, and confidence. Put illegible labels, conflicting dimensions, inferred symmetry, missing
thicknesses, and other assumptions in uncertainties. Never invent an unreadable value.""",
    "parameters": """Return JSON with a parameters array containing exactly one record for every
source_analysis.parameters record and no others. Copy source symbol to name verbatim, and copy
value and unit without conversion or normalization. You may add description and optimizable, but
those fields must not alter the source facts.""",
    "materials": """Return JSON with a materials array containing exactly one record for every
distinct non-empty material named by source_analysis.components and no others. Preserve each
material name verbatim. Include permittivity, permeability, conductivity, and loss_tangent when
known. Do not add air, vacuum, PEC, copper, or another system/default material unless the source
components explicitly name it.""",
    "solids": """Return JSON with a solids array in a one-to-one correspondence with
source_analysis.components. Copy name, role, primitive, and material verbatim for every component;
do not add, remove, rename, reinterpret, or substitute components or primitives. Also copy every
explicit parent_layer, boundary, fill_material, body_material, and required_relationships field
verbatim. When geometric_evidence is a structured JSON object, copy it verbatim as well. Add
coordinate_system and dependencies as downstream modeling metadata. Do not leave an
evidenced relationship or layer-fill/body-material distinction only in prose, and do not invent
one that is not evidenced.""",
    "dimensions": """Return JSON with a solids array containing exactly one record for every
validated solids.solids record and no others. Copy each name, role, primitive, and material
verbatim. Also copy every explicit parent_layer, boundary, fill_material, body_material, and
required_relationships field verbatim; relationship metadata is part of the reviewed topology
and must never be dropped.
Then give explicit coordinate-based dimensions. Do not use words such as centered or above
without formulas. Put each object's numeric ranges, origins, sizes, radii, heights, and formulas in
a structured `dimensions` object; never leave an evidenced geometric range only in prose. For an
evidenced stackup, use any one explicit global Z origin but keep ground,
dielectric, and signal layers face-contiguous in their reviewed order. A patch belonging to a
finite-thickness signal layer starts at that signal layer's elevation, and a through-substrate
probe runs from the reference-ground top face to the signal-layer elevation; do not add conductor
thickness to the substrate span. Do not change topology, add helper objects, or repeat solver
settings.""",
    "model_3d": _HFSS_FRAGMENT_CONTRACT
    + """
Create variables, evidenced materials, and all requested 3D primitives by acting directly on
hfss. This stage owns primitive construction only. Do not perform subtract/unite/intersect,
assign boundaries or ports, set solution_type, create a setup or sweep, or insert a far-field
sphere. Use create_box(origin=..., sizes=...), create_rectangle(orientation=..., origin=...,
sizes=...), create_circle(orientation=..., origin=..., radius=...), and
create_cylinder(orientation=..., origin=..., radius=..., height=...) with supported keywords.
Bind created objects to stable Python variables when later stages reference their faces. A
component with role wave_port_cap and geometric_evidence.source_face is a helper-generated
result: do not create it directly here; wave_port(create_pec_cap=True) owns its creation.""",
    "model_2d": _HFSS_FRAGMENT_CONTRACT
    + """
Create only the requested sheets, polylines, extrusions, or rotations directly on hfss. Do not
perform boolean operations and do not create boundaries, ports, setups, sweeps, reports, or
far-field definitions.""",
    "boolean": _HFSS_FRAGMENT_CONTRACT
    + """
Perform only the reviewed unite, subtract, intersect, and cleanup operations on objects already
created by model_3d/model_2d. Do not create replacement/helper geometry, redefine variables or
materials, assign boundaries or ports, or create solver/far-field objects. Use each reviewed
boolean operation exactly once.""",
    "simulation_spec": """Return one JSON object with design_type, solution_type,
frequency_band, excitation, open_region, far_field, setup, sweep, s_parameter,
required_reports, and uncertainties. For the HFSS backend design_type must be HFSS. setup must
contain name, type, and adaptive_frequency as {value, unit}; sweep must contain name, type,
start as {value, unit}, and stop as {value, unit}. Use the reviewed names and numeric values;
do not rename Sweep1 to Sweep. s_parameter must explicitly name the self-reflection result used
for validation (for example S11_dB), and required_reports must include the data required to
evaluate it. far_field must always be a JSON object: use {"enabled": false} when no far-field
setup is reviewed, never null. Define the existing port-sheet object, integration/reference conductors, radiation
clearance, adaptive frequency, and sweep range explicitly. Do not silently guess missing
excitation geometry; record every engineering assumption in uncertainties.""",
    "simulation_setup": _HFSS_FRAGMENT_CONTRACT + """
Implement only the reviewed solver assignments from simulation_spec on geometry that already
exists. Do not redefine variables/materials, create or replace geometry, or perform boolean
operations. Set solution_type before creating excitation. Assign the existing open-region
boundary, Perfect E/reference conductor, and port with an explicit integration line/reference;
then create the named driven setup and frequency sweep. Insert an infinite sphere only when
simulation_spec.far_field.enabled is true; never insert one when it is false. For a reviewed
wave_port_cap with geometric_evidence.source_face, pass that exact face expression as the
wave_port assignment directly (not a temporary face-ID variable obtained by position), its owning
object as reference, create_pec_cap=True, and the reviewed port
name; do not assign the generated cap object as the port face. Use hfss.wave_port and
create_linear_count_sweep(unit=..., start_frequency=..., stop_frequency=...), passing numeric
frequency values when unit= is supplied rather than strings such as "8GHz". Create the setup with
the reviewed setup_type= and case-sensitive Frequency= properties. Attribute expressions such as
Probe_feed_outer.bottom_face_z must be Python expressions, not quoted strings. If source geometry
declares perfect_e_face_selector=maximum_area_lateral_face, select that face from the existing
object's faces by maximum area with an ordinary loop (lambdas are forbidden in fragments), then
apply Perfect E with a supported method. Do not run analyze,
export data, access files, or close AEDT.""",
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
