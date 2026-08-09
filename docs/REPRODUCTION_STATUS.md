# LEAM-to-HFSS reproduction status

The implementation follows LEAM's inspectable staged workflow while targeting
Ansys HFSS/PyAEDT rather than CST/VBA.

## Implemented pipeline

1. `source_analysis` reads image/PDF evidence once and records topology,
   dimensions, materials, boolean operations, confidence, and uncertainties.
2. Parameter, material, solid, coordinate-dimension, 3D, 2D+, and boolean
   stages consume the validated source artifact.
3. Optional `simulation_spec` and `simulation_setup` stages describe and create
   ports, boundaries, open region, sweep, and far-field configuration. They are
   opt-in because these details are often absent from a paper's geometry figure.
4. The HFSS builder applies reviewed PyAEDT fragments only when the local
   simulation execution gate is enabled.
5. The optimizer copies the source project, evaluates every design in HFSS,
   checkpoints every trial, and preserves the best project separately.

## Image recognition

`analyze_antenna_source` is the direct MCP entry point. PNG/JPEG inputs are sent
as multimodal image content and PDFs are sent as file content through the OpenAI
Responses API. Set `OPENAI_VISION_MODEL` to route the visual stage separately
from `OPENAI_MODEL`. Later stages receive the validated JSON instead of rereading
the image, preventing inconsistent OCR/geometric interpretations across stages.

The visual stage can use either OpenAI Responses or local Ollama. Set
`ANTENNA_VISION_PROVIDER=ollama` and `OLLAMA_VISION_MODEL=qwen3-vl:8b` to render
PDF pages locally with PyMuPDF and send only those in-memory images to the local
model. Text planning and PyAEDT code generation can independently use DeepSeek
through `ANTENNA_TEXT_PROVIDER=deepseek`. DeepSeek's current API model remains
text-only and cannot itself be selected as the vision provider. The checked-in
Case 3 fixture provides a deterministic regression artifact when all APIs are
offline.

## Optimization

The default strategy uses Latin-hypercube initial sampling followed by an online
Gaussian-process surrogate. A lower-confidence-bound acquisition function trades
off predicted performance and uncertainty before selecting the next expensive
HFSS evaluation. Random search remains available as a baseline.

Metrics can minimize, maximize, approach a target, or impose upper/lower-bound
hinge penalties. A frequency sub-range can be attached to each reducer, which is
needed for UWB worst-case S11 and realized-gain specifications.

## Case 3 checkpoint

`examples/leam_case3` contains the visually reconstructed slotted monopole,
parametric PyAEDT builder, and a 3.1-10.6 GHz optimization request. Geometry-only
execution has been exercised during development, but `.aedt` outputs are deliberately
excluded from the repository. A reviewed CPW port and open-region setup are still
required before real HFSS optimization trials should run.
