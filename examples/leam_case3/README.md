# LEAM Case 3 multimodal regression fixture

This fixture reproduces the slotted printed monopole shown in Fig. 7 of the LEAM
paper. `recognized_source.json` is the inspectable output expected from the new
`source_analysis` stage. `build_model.py` converts the reviewed parameters and
topology into PyAEDT geometry using an existing `hfss` object.

`visual_audit.json` is the operator-reviewed evidence binding for Fig. 7. It is
used when a small local vision model transcribes numbers correctly but cannot
reliably follow every dimension arrow. Supplying it to `source-refine` does not
bypass approval: the candidate must bind its entities and claims exactly, and
the audit plus rendered source crops are included in the approval hash.

The copper thickness remains an explicit engineering assumption because Fig. 7
does not specify it. The reviewed visual audit places both slot arms at the
radiator center. Simulation setup is kept out of this geometry fixture because
the source does not specify its port, open region, mesh, or sweep.

For a reviewed modeling job, propose `CuT` with `model-assume-propose`, inspect
the candidate, return its hash with `model-assume-approve`, and then pass that
same hash to `model-compile --assumption-approval-hash ... --profile leam_case3`.
This preserves `CuT=null` in the approved
paper-evidence artifact while binding the user-approved baseline, generated
PyAEDT fragments, and geometry checks into the final build review hash.

`optimization_request.json` encodes the 3.1-10.6 GHz constraint penalties from
the source optimization paper: worst S11 no higher than -10 dB, maximum realized
gain no higher than 3 dB, and minimum realized gain no lower than 1 dB. It becomes
runnable after an HFSS port, radiation boundary, far-field setup, and sweep named
`Setup1 : Sweep1` have been reviewed and added.
