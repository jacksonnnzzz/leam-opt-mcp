from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

from .llm import LlmProvider, _extract_pdf_text, provider_from_env, vision_provider_from_env
from .modeling import _strip_fence, _validate_confidence, _validate_source_analysis
from .models import ModelingRequest
from .prompts import STAGE_INSTRUCTIONS
from .workspace import WorkspaceStore


REFINEMENT_SYSTEM = """You are the engineering-review stage of an antenna modeling pipeline.
Reconcile machine-read visual evidence with locally extracted document text. Correct OCR and
semantic errors, but never invent missing geometry. Preserve every supported numeric value and
mark assumptions explicitly. Distinguish conductors, dielectrics, void/slot subtraction tools,
feeds, grounds, radiators, radius versus diameter, independent versus derived parameters, and
visible geometry versus simulation settings. Return only the requested source-analysis JSON.
"""

VISUAL_AUDIT_SYSTEM = """You are the independent visual-evidence auditor in an antenna
reconstruction pipeline. Inspect only the target design named by the user. Trace dimension-arrow
endpoints, extension lines, conductor boundaries, gaps, layers, and boolean cutter shapes before
assigning any parameter meaning. Treat prior machine analysis as untrusted hypotheses, not facts.
Never transfer geometry from another figure or antenna. Return only the requested JSON object.
"""

VISUAL_VERDICT_SYSTEM = """You are the final visual quality gate for an antenna reconstruction.
Compare the candidate source analysis against the target figure and its adjacent caption/prose.
Reject wrong parameter-to-geometry bindings, wrong layer placement, extra or missing components,
cross-design contamination, and unsupported topology even when all numeric values are correct.
Return only the requested JSON object and never repair the candidate silently.
"""

MIN_EVIDENCE_CONFIDENCE = 0.75


class SourceRefinementService:
    def __init__(
        self,
        store: WorkspaceStore,
        provider: LlmProvider | None = None,
        vision_provider: LlmProvider | None = None,
    ) -> None:
        self.store = store
        self.provider = provider
        self.vision_provider = vision_provider

    def refine(
        self,
        job_id: str,
        target_description: str | None = None,
        visual_audit_path: str | None = None,
    ) -> dict[str, Any]:
        state = self.store.load_state(job_id)
        if state.kind != "modeling":
            raise ValueError("source refinement requires a modeling job")
        raw_path_value = state.artifacts.get("source_analysis")
        if not raw_path_value:
            raise ValueError("the modeling job has no source_analysis artifact")
        raw_path = Path(raw_path_value).resolve()
        raw_payload = json.loads(raw_path.read_text("utf-8"))
        request = ModelingRequest.model_validate(state.request)
        target = target_description or request.description
        evidence = _collect_text_evidence(request.attachments)

        state.status = "running"
        state.current_stage = "source_refinement"
        state.error = None
        self.store.save_state(state)
        try:
            visual_attachments = _prepare_visual_attachments(
                self.store,
                job_id,
                request.attachments,
                target,
            )
            for index, path in enumerate(visual_attachments, start=1):
                state.artifacts[f"source_visual_input_{index}"] = str(path)
            self.store.save_state(state)
            provider = self.provider or provider_from_env(request.model)
            visual_provider = self.vision_provider
            if visual_provider is None:
                visual_provider = (
                    self.provider
                    if self.provider is not None
                    else vision_provider_from_env(visual_only=True)
                )
            supplied_visual_audit_path = visual_audit_path
            visual_audit = None
            visual_audit_artifact_path = None
            trusted_visual_audit = False
            if supplied_visual_audit_path:
                supplied_audit = Path(supplied_visual_audit_path).expanduser().resolve()
                visual_audit = json.loads(supplied_audit.read_text("utf-8"))
                _validate_visual_audit(visual_audit)
                trusted_visual_audit = True
                visual_audit_artifact_path = self.store.write_artifact(
                    job_id,
                    "source_visual_audit.json",
                    json.dumps(visual_audit, ensure_ascii=False, indent=2) + "\n",
                )
                state.artifacts["source_visual_audit"] = str(visual_audit_artifact_path)
                self.store.save_state(state)
            elif visual_attachments:
                audit_result = visual_provider.generate(
                    system=VISUAL_AUDIT_SYSTEM,
                    prompt=_visual_audit_prompt(target, raw_payload),
                    attachments=visual_attachments,
                )
                visual_audit = json.loads(_strip_fence(audit_result))
                _validate_visual_audit(visual_audit)
                visual_audit_artifact_path = self.store.write_artifact(
                    job_id,
                    "source_visual_audit.json",
                    json.dumps(visual_audit, ensure_ascii=False, indent=2) + "\n",
                )
                state.artifacts["source_visual_audit"] = str(visual_audit_artifact_path)
                self.store.save_state(state)

            prompt = _refinement_prompt(target, raw_payload, evidence, visual_audit)
            result = provider.generate(system=REFINEMENT_SYSTEM, prompt=prompt, attachments=[])
            cleaned = _strip_fence(result)
            reviewed = json.loads(cleaned)
            _validate_source_analysis(reviewed)

            visual_verdict = None
            verdict_path = None
            if visual_attachments and not trusted_visual_audit:
                verdict_result = visual_provider.generate(
                    system=VISUAL_VERDICT_SYSTEM,
                    prompt=_visual_verdict_prompt(target, reviewed, visual_audit),
                    attachments=visual_attachments,
                )
                visual_verdict = json.loads(_strip_fence(verdict_result))
                _validate_visual_verdict(visual_verdict)
                verdict_path = self.store.write_artifact(
                    job_id,
                    "source_visual_verdict.json",
                    json.dumps(visual_verdict, ensure_ascii=False, indent=2) + "\n",
                )
                state.artifacts["source_visual_verdict"] = str(verdict_path)

            report = _audit_refinement(
                raw_payload,
                reviewed,
                visual_audit,
                visual_verdict,
                evidence,
                "operator_supplied" if trusted_visual_audit else "model_generated",
            )

            candidate_path = self.store.write_artifact(
                job_id,
                "source_analysis_candidate.json",
                json.dumps(reviewed, ensure_ascii=False, indent=2) + "\n",
            )
            report_path = self.store.write_artifact(
                job_id,
                "source_refinement_report.json",
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            )
            evidence_artifacts = []
            if visual_audit_artifact_path is not None:
                evidence_artifacts.append(("visual_audit", visual_audit_artifact_path))
            if verdict_path is not None:
                evidence_artifacts.append(("visual_verdict", verdict_path))
            evidence_artifacts.extend(
                (f"visual_input_{index}", path)
                for index, path in enumerate(visual_attachments, start=1)
            )
            packet = _review_packet(candidate_path, report_path, evidence_artifacts)
            packet_path = self.store.write_artifact(
                job_id,
                "source_review_packet.json",
                json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
            )
            state.artifacts.update(
                {
                    "source_analysis_candidate": str(candidate_path),
                    "source_refinement_report": str(report_path),
                    "source_review_packet": str(packet_path),
                }
            )
            state.status = "awaiting_review"
            return {
                "job_id": job_id,
                "status": state.status,
                "candidate": str(candidate_path),
                "report": str(report_path),
                "review_packet": str(packet_path),
                "approval_hash": packet["approval_hash"],
                "quality_gate_passed": report["quality_gate_passed"],
            }
        except Exception as exc:
            state.status = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.store.save_state(state)

    def approve(self, job_id: str, approval_hash: str) -> dict[str, Any]:
        state = self.store.load_state(job_id)
        if state.kind != "modeling" or state.status != "awaiting_review":
            raise ValueError("an awaiting-review source refinement is required")
        candidate = Path(state.artifacts["source_analysis_candidate"]).resolve()
        report = Path(state.artifacts["source_refinement_report"]).resolve()
        evidence_artifacts = []
        for artifact_name, packet_name in (
            ("source_visual_audit", "visual_audit"),
            ("source_visual_verdict", "visual_verdict"),
        ):
            raw_path = state.artifacts.get(artifact_name)
            if raw_path:
                evidence_artifacts.append((packet_name, Path(raw_path).resolve()))
        visual_input_names = sorted(
            name for name in state.artifacts if name.startswith("source_visual_input_")
        )
        for index, artifact_name in enumerate(visual_input_names, start=1):
            evidence_artifacts.append(
                (f"visual_input_{index}", Path(state.artifacts[artifact_name]).resolve())
            )
        packet = _review_packet(candidate, report, evidence_artifacts)
        if not hmac.compare_digest(packet["approval_hash"], approval_hash):
            raise PermissionError("source approval hash does not match the current candidate and report")
        report_payload = json.loads(report.read_text("utf-8"))
        if not report_payload.get("quality_gate_passed"):
            raise ValueError("source refinement quality gate failed; correct and refine it again")
        candidate_text = candidate.read_text("utf-8")
        _validate_source_analysis(json.loads(candidate_text))
        approved_path = self.store.write_artifact(job_id, "source_analysis_approved.json", candidate_text)
        state.artifacts["source_analysis_approved"] = str(approved_path)
        state.status = "completed"
        state.current_stage = "source_analysis_approved"
        state.error = None
        self.store.save_state(state)
        return {"job_id": job_id, "status": state.status, "approved": str(approved_path)}

    def recheck(self, job_id: str, visual_audit_path: str | None = None) -> dict[str, Any]:
        """Deterministically reconcile a candidate to an operator-supplied source audit."""

        state = self.store.load_state(job_id)
        if state.kind != "modeling" or state.status != "awaiting_review":
            raise ValueError("an awaiting-review source refinement is required")
        required_artifacts = {
            "source_analysis",
            "source_analysis_candidate",
            "source_refinement_report",
        }
        missing = required_artifacts - state.artifacts.keys()
        if missing:
            raise ValueError(f"source recheck is missing artifacts: {sorted(missing)}")

        prior_report_path = Path(state.artifacts["source_refinement_report"]).resolve()
        prior_report = json.loads(prior_report_path.read_text("utf-8"))
        if prior_report.get("visual_audit_provenance") != "operator_supplied":
            raise ValueError("source recheck requires an operator-supplied visual audit")

        raw = json.loads(Path(state.artifacts["source_analysis"]).read_text("utf-8"))
        candidate_path = Path(state.artifacts["source_analysis_candidate"]).resolve()
        candidate = json.loads(candidate_path.read_text("utf-8"))
        if visual_audit_path:
            supplied_path = Path(visual_audit_path).expanduser().resolve()
            audit = json.loads(supplied_path.read_text("utf-8"))
            _validate_visual_audit(audit)
            audit_path = self.store.write_artifact(
                job_id,
                "source_visual_audit.json",
                json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
            )
            state.artifacts["source_visual_audit"] = str(audit_path)
            self.store.save_state(state)
        else:
            raw_audit_path = state.artifacts.get("source_visual_audit")
            if not raw_audit_path:
                raise ValueError("source recheck requires a source_visual_audit artifact")
            audit_path = Path(raw_audit_path).resolve()
            audit = json.loads(audit_path.read_text("utf-8"))
        _validate_visual_audit(audit)
        repaired, repairs = _apply_operator_audit(candidate, audit)
        _validate_source_analysis(repaired)

        request = ModelingRequest.model_validate(state.request)
        text_evidence = _collect_text_evidence(request.attachments)
        report = _audit_refinement(
            raw,
            repaired,
            audit,
            None,
            text_evidence,
            "operator_supplied",
        )
        report["canonical_repairs"] = repairs

        candidate_path = self.store.write_artifact(
            job_id,
            "source_analysis_candidate.json",
            json.dumps(repaired, ensure_ascii=False, indent=2) + "\n",
        )
        report_path = self.store.write_artifact(
            job_id,
            "source_refinement_report.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        evidence_artifacts = _state_evidence_artifacts(state)
        packet = _review_packet(candidate_path, report_path, evidence_artifacts)
        packet_path = self.store.write_artifact(
            job_id,
            "source_review_packet.json",
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
        )
        state.artifacts.update(
            {
                "source_analysis_candidate": str(candidate_path),
                "source_refinement_report": str(report_path),
                "source_review_packet": str(packet_path),
            }
        )
        state.status = "awaiting_review"
        state.current_stage = "source_recheck"
        state.error = None
        self.store.save_state(state)
        return {
            "job_id": job_id,
            "status": state.status,
            "candidate": str(candidate_path),
            "report": str(report_path),
            "review_packet": str(packet_path),
            "approval_hash": packet["approval_hash"],
            "quality_gate_passed": report["quality_gate_passed"],
            "canonical_repair_count": len(repairs),
        }


def _collect_text_evidence(attachments: list[str]) -> str:
    sections: list[str] = []
    for raw in attachments:
        path = Path(raw).expanduser().resolve()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if mime == "application/pdf":
            sections.append(f"[{path.name} extracted text]\n{_extract_pdf_text(path)}")
        elif mime.startswith("text/") or path.suffix.lower() in {".md", ".json", ".csv"}:
            sections.append(f"[{path.name}]\n{path.read_text('utf-8')}")
        else:
            sections.append(f"[{path.name}] visual-only attachment; use the raw visual analysis")
    return "\n\n".join(sections) or "No extractable attachment text."


def _prepare_visual_attachments(
    store: WorkspaceStore,
    job_id: str,
    attachments: list[str],
    target: str,
) -> list[Path]:
    result: list[Path] = []
    sequence = 0
    for raw in attachments:
        path = Path(raw).expanduser().resolve()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if mime.startswith("image/"):
            sequence += 1
            suffix = path.suffix.lower() if re.fullmatch(r"\.[a-z0-9]+", path.suffix.lower()) else ".png"
            result.append(
                store.write_binary_artifact(
                    job_id,
                    f"source_visual_input_{sequence}{suffix}",
                    path.read_bytes(),
                )
            )
        elif mime == "application/pdf":
            for page_index, image in _render_target_pdf_pages(path, target):
                sequence += 1
                result.append(
                    store.write_binary_artifact(
                        job_id,
                        f"source_visual_input_{sequence}_page_{page_index + 1}.png",
                        image,
                    )
                )
    return result


def _render_target_pdf_pages(path: Path, target: str) -> list[tuple[int, bytes]]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("target-scoped PDF vision requires PyMuPDF") from exc

    document = fitz.open(path)
    try:
        if document.page_count == 0:
            raise ValueError(f"PDF has no pages: {path}")
        page_texts = [document.load_page(index).get_text("text") for index in range(document.page_count)]
        selected = _select_target_pages(page_texts, target)
        dpi = int(os.getenv("SOURCE_AUDIT_PDF_DPI", "220"))
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        rendered = []
        for index in selected:
            page = document.load_page(index)
            for clip in _target_figure_clips(page, target):
                rendered.append(
                    (
                        index,
                        page.get_pixmap(matrix=matrix, alpha=False, clip=clip).tobytes("png"),
                    )
                )
        return rendered
    finally:
        document.close()


def _select_target_pages(page_texts: list[str], target: str, max_pages: int = 2) -> list[int]:
    figure_ids = re.findall(r"\bfig(?:ure)?\.?\s*(\d+[a-z]?)", target, flags=re.IGNORECASE)
    matched: list[int] = []
    for figure_id in figure_ids:
        pattern = re.compile(
            rf"\bfig(?:ure)?\.?\s*{re.escape(figure_id)}(?:\b|\()",
            flags=re.IGNORECASE,
        )
        matched.extend(index for index, text in enumerate(page_texts) if pattern.search(text))
    unique_matches = list(dict.fromkeys(matched))
    if unique_matches:
        return unique_matches[:max_pages]

    stopwords = {
        "about", "after", "antenna", "design", "figure", "from", "into", "only",
        "reconstruct", "source", "that", "this", "with",
    }
    target_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", target)
        if token.casefold() not in stopwords
    }
    scores = []
    for index, text in enumerate(page_texts):
        normalized = text.casefold()
        scores.append((sum(token in normalized for token in target_tokens), index))
    ranked = [index for score, index in sorted(scores, reverse=True) if score > 0]
    if ranked:
        return sorted(ranked[:max_pages])
    return list(range(min(len(page_texts), max_pages)))


def _target_figure_clips(page: Any, target: str) -> list[Any | None]:
    """Crop a target figure around its caption while retaining adjacent prose and labels."""

    figure_ids = re.findall(r"\bfig(?:ure)?\.?\s*(\d+[a-z]?)", target, flags=re.IGNORECASE)
    if not figure_ids:
        return [None]
    blocks = page.get_text("blocks")
    captions = []
    for figure_id in figure_ids:
        pattern = re.compile(
            rf"^\s*fig(?:ure)?\.?\s*{re.escape(figure_id)}(?:[.:]|\s)",
            flags=re.IGNORECASE,
        )
        for block in blocks:
            text = " ".join(str(block[4]).split())
            if pattern.search(text):
                captions.append(block)
    if not captions:
        return [None]

    caption = max(captions, key=lambda item: item[1])
    page_rect = page.rect
    center = page_rect.width / 2
    caption_center = (caption[0] + caption[2]) / 2
    caption_width = caption[2] - caption[0]
    if caption_width < page_rect.width * 0.62:
        if caption_center >= center:
            x0, x1 = center, page_rect.x1
        else:
            x0, x1 = page_rect.x0, center
    else:
        x0, x1 = page_rect.x0, page_rect.x1
    y0 = max(page_rect.y0, caption[1] - page_rect.height * 0.30)
    y1 = min(page_rect.y1, caption[3] + page_rect.height * 0.025)
    full_clip = page.rect.__class__(x0, y0, x1, y1)

    subfigure_labels = []
    for block in blocks:
        text = " ".join(str(block[4]).split())
        if not re.match(r"^\s*\([a-z]\)", text, flags=re.IGNORECASE):
            continue
        center_x = (block[0] + block[2]) / 2
        if full_clip.x0 <= center_x <= full_clip.x1 and full_clip.y0 <= block[1] <= caption[1]:
            subfigure_labels.append(block)
    subfigure_labels.sort(key=lambda item: (item[0] + item[2]) / 2)
    if len(subfigure_labels) < 2:
        return [full_clip]

    centers = [(item[0] + item[2]) / 2 for item in subfigure_labels]
    boundaries = [full_clip.x0]
    boundaries.extend((left + right) / 2 for left, right in zip(centers, centers[1:]))
    boundaries.append(full_clip.x1)
    subclips = []
    for index, label in enumerate(subfigure_labels):
        subclips.append(
            page.rect.__class__(
                boundaries[index],
                full_clip.y0,
                boundaries[index + 1],
                min(full_clip.y1, label[3] + page_rect.height * 0.015),
            )
        )
    return [full_clip, *subclips]


def _visual_audit_prompt(target: str, raw_payload: dict[str, Any]) -> str:
    unique_parameters = []
    seen_symbols: set[str] = set()
    for item in raw_payload.get("parameters", []):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().strip("$").upper()
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        unique_parameters.append(
            {key: item.get(key) for key in ("symbol", "value", "unit")}
        )
    compact_hypotheses = {
        "antenna_type": raw_payload.get("antenna_type"),
        "components": [
            {
                key: item.get(key)
                for key in ("name",)
            }
            for item in raw_payload.get("components", [])
            if isinstance(item, dict)
        ],
        "parameters": unique_parameters,
    }
    return (
        f"Target antenna intent:\n{target}\n\n"
        "First-pass transcription hints (names and numeric values only; they contain no trusted "
        "geometric meanings):\n"
        f"{json.dumps(compact_hypotheses, ensure_ascii=False, indent=2)}\n\n"
        "Inspect the target figure at high attention. Do not infer a meaning from a symbol's name. "
        "For each dimension, trace its arrow or extension-line endpoints to the actual geometric "
        "feature. Explicitly determine whether conductors are coplanar or on opposite faces. "
        "List hypotheses imported from other figures under rejected_hypotheses. Return each "
        "parameter symbol exactly once. Do not synthesize a derived equation from aligned arrows; "
        "include a relation only when an exact equation is printed in the visible source or stated "
        "in the target intent.\n\n"
        "Return exactly one JSON object with these keys:\n"
        "target_design (string), source_scope (string), components (array), "
        "parameter_bindings (array), derived_relations (array), required_operations (array), "
        "rejected_hypotheses (array), and unresolved (array).\n"
        "Each component must contain entity_id, name, role, shape, primitive_class, "
        "material_class, layer, layer_class, geometric_relation, visual_evidence, source_locator, "
        "and confidence. primitive_class must be box, cylinder, sheet, polyline, or other. "
        "material_class must be dielectric, conductor, void, or other. layer_class must be volume, "
        "top_coplanar, bottom, subtraction, or other. Each parameter binding must contain "
        "claim_id, evidence_mode, symbol, value (null if unreadable), unit, entity_id, quantity, axis, "
        "geometric_meaning, dimension_evidence, source_locator, and confidence. quantity must be "
        "one of length, width, radius, diameter, thickness, gap, offset, angle, "
        "material_property, or other. Each derived relation must contain claim_id, expression, "
        "symbols, evidence, source_locator, and confidence. Use stable unique entity_id and "
        "claim_id strings. evidence_mode must be visual, text, or unresolved. Each required "
        "operation must contain operation, target, operands, and order. Confidence must be from 0 to 1."
    )


def _visual_verdict_prompt(
    target: str,
    reviewed: dict[str, Any],
    visual_audit: dict[str, Any] | None,
) -> str:
    return (
        f"Target antenna intent:\n{target}\n\n"
        "Candidate that must be graded, not repaired:\n"
        f"{json.dumps(reviewed, ensure_ascii=False, indent=2)}\n\n"
        "Earlier targeted visual audit (also re-check it against the image):\n"
        f"{json.dumps(visual_audit, ensure_ascii=False, indent=2)}\n\n"
        "Return exactly one JSON object with keys: passed (boolean), critical_findings (array of "
        "strings), component_count_correct (boolean), topology_correct (boolean), "
        "parameter_checks (array), and cross_design_contamination (array of strings). Each "
        "parameter check must contain symbol, value_correct (boolean), meaning_correct (boolean), "
        "and visual_evidence (string). Set passed=false if any visible parameter meaning is wrong "
        "or ambiguous, any component is extra/missing, any conductor layer is wrong, or topology "
        "from another design has been introduced."
    )


def _refinement_prompt(
    target: str,
    raw_payload: dict[str, Any],
    evidence: str,
    visual_audit: dict[str, Any] | None = None,
) -> str:
    return (
        f"Target antenna intent:\n{target}\n\n"
        "Raw local-vision analysis (untrusted and possibly inconsistent):\n"
        f"{json.dumps(raw_payload, ensure_ascii=False, indent=2)}\n\n"
        "Independent targeted visual audit (higher priority for geometric meaning):\n"
        f"{json.dumps(visual_audit, ensure_ascii=False, indent=2)}\n\n"
        f"Locally extracted source text:\n{evidence}\n\n"
        "Engineering reconciliation rules:\n"
        "- Analyze only the target antenna; never merge separate examples.\n"
        "- Follow dimension-arrow evidence from the targeted visual audit for every geometric meaning.\n"
        "- Do not add a component rejected by the visual audit or unsupported by the target prose.\n"
        "- If the source explicitly gives a solid count, return exactly that many components.\n"
        "- For every component supported by the source audit, add evidence_binding with mode "
        "'visual' and copy the "
        "exact entity_id, primitive_class, material_class, and layer_class. The component material "
        "must agree with material_class; a void/subtraction tool must not be copper.\n"
        "- For every audited parameter, add semantic_binding and copy the exact evidence_mode as "
        "mode plus claim_id, entity_id, quantity, and axis. Never discard claim IDs for text or "
        "unresolved evidence, and never bind by guessing from a symbol.\n"
        "- Add a top-level derived_relations array that copies every audited relation's claim_id, "
        "expression, and symbols exactly. Copy required_operations into operations with the same "
        "operation, target, operands, and order; do not replace or omit topology operations.\n"
        "- Return every unique evidenced parameter exactly once.\n"
        "- Preserve evidenced numeric values unless the source text explicitly corrects them.\n"
        "- Add named parameters for textual material properties and thicknesses.\n"
        "- Put unknown conductor thickness, ports, boundaries, mesh, and sweeps in uncertainties.\n"
        "- Slot cutter objects are void/subtraction tools, not copper remaining after subtraction.\n"
        "- Use an XY modeling plane and explicit +z thickness direction when supported.\n\n"
        f"Output contract:\n{STAGE_INSTRUCTIONS['source_analysis']}"
    )


def _audit_refinement(
    raw: dict[str, Any],
    reviewed: dict[str, Any],
    visual_audit: dict[str, Any] | None = None,
    visual_verdict: dict[str, Any] | None = None,
    text_evidence: str = "",
    visual_audit_provenance: str = "none",
) -> dict[str, Any]:
    raw_parameters, raw_duplicates = _parameter_map(raw.get("parameters", []))
    reviewed_parameters, reviewed_duplicates = _parameter_map(reviewed.get("parameters", []))
    missing = sorted(set(raw_parameters) - set(reviewed_parameters))
    added = sorted(set(reviewed_parameters) - set(raw_parameters))
    changed = []
    for symbol in sorted(set(raw_parameters) & set(reviewed_parameters)):
        before = raw_parameters[symbol].get("value")
        after = reviewed_parameters[symbol].get("value")
        if before is not None and before != after:
            changed.append({"symbol": symbol, "raw": before, "reviewed": after})
    warnings = []
    if changed:
        warnings.append("Numeric values changed from visual evidence; verify each change against the source.")
    if added:
        warnings.append("Parameters were added from extracted text or explicit assumptions; review their evidence.")
    component_count_changed = len(raw.get("components", [])) != len(reviewed.get("components", []))
    if component_count_changed:
        warnings.append("Component count changed from the first visual pass; visual confirmation is required.")

    low_confidence_components = sorted(
        str(item.get("name", ""))
        for item in reviewed.get("components", [])
        if isinstance(item, dict) and item.get("confidence", 0) < MIN_EVIDENCE_CONFIDENCE
    )
    low_confidence_parameters = sorted(
        str(item.get("symbol", ""))
        for item in reviewed.get("parameters", [])
        if isinstance(item, dict)
        and item.get("value") is not None
        and item.get("confidence", 0) < MIN_EVIDENCE_CONFIDENCE
    )
    if low_confidence_components or low_confidence_parameters:
        warnings.append("Resolved geometry still contains low-confidence evidence bindings.")

    visual_parameters, _ = _parameter_map(
        (visual_audit or {}).get("parameter_bindings", [])
    )
    missing_visual_symbols = sorted(set(visual_parameters) - set(reviewed_parameters))
    visual_value_disagreements = []
    for symbol in sorted(set(visual_parameters) & set(reviewed_parameters)):
        visual_value = visual_parameters[symbol].get("value")
        reviewed_value = reviewed_parameters[symbol].get("value")
        if visual_value is not None and visual_value != reviewed_value:
            visual_value_disagreements.append(
                {"symbol": symbol, "visual": visual_value, "reviewed": reviewed_value}
            )

    visual_component_count_mismatch = False
    if visual_audit is not None:
        visual_component_count_mismatch = len(visual_audit.get("components", [])) != len(
            reviewed.get("components", [])
        )

    binding_conflicts: list[dict[str, Any]] = []
    component_material_conflicts: list[dict[str, str]] = []
    component_primitive_conflicts: list[dict[str, str]] = []
    missing_visual_entities: list[str] = []
    unsupported_components: list[str] = []
    missing_visual_claim_ids: list[str] = []
    if visual_audit is not None:
        visual_entities = {
            str(item["entity_id"]): item for item in visual_audit.get("components", [])
        }
        entity_binding_counts = {entity_id: 0 for entity_id in visual_entities}
        normalized_evidence = _normalize_evidence_text(text_evidence)
        for component in reviewed.get("components", []):
            name = str(component.get("name", ""))
            binding = component.get("evidence_binding")
            if not isinstance(binding, dict):
                unsupported_components.append(name)
                continue
            mode = binding.get("mode")
            if mode == "visual":
                entity_id = str(binding.get("entity_id", ""))
                if entity_id not in visual_entities:
                    unsupported_components.append(name)
                else:
                    entity_binding_counts[entity_id] += 1
                    expected_component = visual_entities[entity_id]
                    expected_fields = {
                        "entity_id": entity_id,
                        "primitive_class": expected_component.get("primitive_class"),
                        "material_class": expected_component.get("material_class"),
                        "layer_class": expected_component.get("layer_class"),
                    }
                    actual_fields = {key: binding.get(key) for key in expected_fields}
                    if actual_fields != expected_fields:
                        binding_conflicts.append(
                            {
                                "component": name,
                                "issue": "component evidence binding mismatch",
                                "expected": expected_fields,
                                "reviewed": actual_fields,
                            }
                        )
                    material_class = str(expected_component.get("material_class", "other"))
                    if not _material_matches_class(str(component.get("material", "")), material_class):
                        component_material_conflicts.append(
                            {
                                "component": name,
                                "material": str(component.get("material", "")),
                                "expected_class": material_class,
                            }
                        )
                    primitive_class = str(expected_component.get("primitive_class", "other"))
                    if not _primitive_matches_class(
                        str(component.get("primitive", "")), primitive_class
                    ):
                        component_primitive_conflicts.append(
                            {
                                "component": name,
                                "primitive": str(component.get("primitive", "")),
                                "expected_class": primitive_class,
                            }
                        )
            elif mode == "text":
                quote = _normalize_evidence_text(str(binding.get("evidence_quote", "")))
                if len(quote) < 8 or quote not in normalized_evidence:
                    unsupported_components.append(name)
            else:
                unsupported_components.append(name)
        missing_visual_entities = sorted(
            entity_id
            for entity_id, item in visual_entities.items()
            if item.get("confidence", 0) >= MIN_EVIDENCE_CONFIDENCE
            and entity_binding_counts[entity_id] != 1
        )

        visual_claims = {
            str(item["claim_id"]): item for item in visual_audit.get("parameter_bindings", [])
        }
        claim_binding_counts = {claim_id: 0 for claim_id in visual_claims}
        visual_claim_by_symbol = {
            str(item.get("symbol", "")).strip().strip("$").upper(): item
            for item in visual_audit.get("parameter_bindings", [])
        }
        for parameter in reviewed.get("parameters", []):
            symbol = str(parameter.get("symbol", "")).strip().strip("$").upper()
            expected = visual_claim_by_symbol.get(symbol)
            if expected is None:
                continue
            binding = parameter.get("semantic_binding")
            expected_mode = expected.get("evidence_mode", "visual")
            if not isinstance(binding, dict) or binding.get("mode") != expected_mode:
                binding_conflicts.append(
                    {
                        "symbol": symbol,
                        "issue": "semantic evidence mode mismatch",
                        "expected_mode": expected_mode,
                        "reviewed_mode": binding.get("mode") if isinstance(binding, dict) else None,
                    }
                )
                continue
            claim_id = str(binding.get("claim_id", ""))
            if claim_id in claim_binding_counts:
                claim_binding_counts[claim_id] += 1
            expected_fields = {
                "claim_id": expected.get("claim_id"),
                "entity_id": expected.get("entity_id"),
                "quantity": expected.get("quantity"),
                "axis": expected.get("axis"),
            }
            actual_fields = {key: binding.get(key) for key in expected_fields}
            if actual_fields != expected_fields:
                binding_conflicts.append(
                    {
                        "symbol": symbol,
                        "issue": "visual claim binding mismatch",
                        "expected": expected_fields,
                        "reviewed": actual_fields,
                    }
                )
        missing_visual_claim_ids = sorted(
            claim_id
            for claim_id, item in visual_claims.items()
            if item.get("confidence", 0) >= MIN_EVIDENCE_CONFIDENCE
            and claim_binding_counts[claim_id] != 1
        )

    missing_derived_relation_claims: list[str] = []
    derived_relation_conflicts: list[dict[str, Any]] = []
    if visual_audit is not None:
        reviewed_relations = reviewed.get("derived_relations", [])
        if not isinstance(reviewed_relations, list):
            reviewed_relations = []
        reviewed_relation_map = {
            str(item.get("claim_id", "")): item
            for item in reviewed_relations
            if isinstance(item, dict) and item.get("claim_id")
        }
        for relation in visual_audit.get("derived_relations", []):
            claim_id = str(relation.get("claim_id", ""))
            candidate_relation = reviewed_relation_map.get(claim_id)
            if candidate_relation is None:
                missing_derived_relation_claims.append(claim_id)
                continue
            expected_expression = _normalize_expression(str(relation.get("expression", "")))
            reviewed_expression = _normalize_expression(
                str(candidate_relation.get("expression", ""))
            )
            expected_symbols = {
                str(symbol).strip().upper() for symbol in relation.get("symbols", [])
            }
            reviewed_symbols = {
                str(symbol).strip().upper() for symbol in candidate_relation.get("symbols", [])
            }
            if (
                expected_expression != reviewed_expression
                or expected_symbols != reviewed_symbols
            ):
                derived_relation_conflicts.append(
                    {
                        "claim_id": claim_id,
                        "expected_expression": relation.get("expression"),
                        "reviewed_expression": candidate_relation.get("expression"),
                        "expected_symbols": sorted(expected_symbols),
                        "reviewed_symbols": sorted(reviewed_symbols),
                    }
                )

    missing_required_operations: list[dict[str, Any]] = []
    unexpected_operations: list[dict[str, Any]] = []
    unknown_operation_references: list[str] = []
    if visual_audit is not None and visual_audit.get("required_operations"):
        required_operations = visual_audit["required_operations"]
        reviewed_operations = [
            item for item in reviewed.get("operations", []) if isinstance(item, dict)
        ]
        required_map = {_operation_signature(item): item for item in required_operations}
        reviewed_map = {_operation_signature(item): item for item in reviewed_operations}
        missing_required_operations = [
            required_map[key] for key in sorted(set(required_map) - set(reviewed_map))
        ]
        unexpected_operations = [
            reviewed_map[key] for key in sorted(set(reviewed_map) - set(required_map))
        ]
        component_names = {
            _normalize_component_name(str(item.get("name", "")))
            for item in reviewed.get("components", [])
            if isinstance(item, dict)
        }
        unknown_operation_references = sorted(
            {
                str(reference)
                for operation in reviewed_operations
                for reference in _operation_references(operation)
                if reference is None
                or _normalize_component_name(str(reference)) not in component_names
            }
        )

    failed_parameter_checks = []
    if visual_verdict is not None:
        failed_parameter_checks = sorted(
            str(item.get("symbol", ""))
            for item in visual_verdict.get("parameter_checks", [])
            if isinstance(item, dict)
            and (not item.get("value_correct") or not item.get("meaning_correct"))
        )
    visual_critical_findings = list((visual_verdict or {}).get("critical_findings", []))
    cross_design_contamination = list(
        (visual_verdict or {}).get("cross_design_contamination", [])
    )
    visual_verdict_passed = visual_verdict is None or (
        visual_verdict.get("passed") is True
        and visual_verdict.get("component_count_correct") is True
        and visual_verdict.get("topology_correct") is True
        and not failed_parameter_checks
        and not visual_critical_findings
        and not cross_design_contamination
    )
    unresolved_count = len(reviewed.get("uncertainties", []))
    quality_gate_passed = (
        not missing
        and not reviewed_duplicates
        and not component_count_changed
        and not low_confidence_components
        and not low_confidence_parameters
        and not missing_visual_symbols
        and not visual_value_disagreements
        and not visual_component_count_mismatch
        and not binding_conflicts
        and not component_material_conflicts
        and not component_primitive_conflicts
        and not missing_visual_entities
        and not unsupported_components
        and not missing_visual_claim_ids
        and not missing_derived_relation_claims
        and not derived_relation_conflicts
        and not missing_required_operations
        and not unexpected_operations
        and not unknown_operation_references
        and visual_verdict_passed
    )
    return {
        "quality_gate_passed": quality_gate_passed,
        "requires_human_review": True,
        "raw_duplicate_symbols": raw_duplicates,
        "reviewed_duplicate_symbols": reviewed_duplicates,
        "missing_raw_symbols": missing,
        "added_symbols": added,
        "changed_numeric_values": changed,
        "component_count_changed": component_count_changed,
        "low_confidence_components": low_confidence_components,
        "low_confidence_parameters": low_confidence_parameters,
        "visual_audit_present": visual_audit is not None,
        "visual_audit_provenance": visual_audit_provenance,
        "missing_visual_symbols": missing_visual_symbols,
        "visual_value_disagreements": visual_value_disagreements,
        "visual_component_count_mismatch": visual_component_count_mismatch,
        "binding_conflicts": binding_conflicts,
        "component_material_conflicts": component_material_conflicts,
        "component_primitive_conflicts": component_primitive_conflicts,
        "missing_visual_entities": missing_visual_entities,
        "unsupported_components": sorted(set(unsupported_components)),
        "missing_visual_claim_ids": missing_visual_claim_ids,
        "missing_derived_relation_claims": sorted(missing_derived_relation_claims),
        "derived_relation_conflicts": derived_relation_conflicts,
        "missing_required_operations": missing_required_operations,
        "unexpected_operations": unexpected_operations,
        "unknown_operation_references": unknown_operation_references,
        "visual_verdict_passed": visual_verdict_passed,
        "visual_failed_parameter_checks": failed_parameter_checks,
        "visual_critical_findings": visual_critical_findings,
        "cross_design_contamination": cross_design_contamination,
        "raw_component_count": len(raw.get("components", [])),
        "reviewed_component_count": len(reviewed.get("components", [])),
        "reviewed_uncertainty_count": unresolved_count,
        "warnings": warnings,
    }


def _validate_visual_audit(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("source visual audit must be a JSON object")
    required = {
        "target_design",
        "source_scope",
        "components",
        "parameter_bindings",
        "derived_relations",
        "required_operations",
        "rejected_hypotheses",
        "unresolved",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"source visual audit is missing keys: {sorted(missing)}")
    for key in (
        "components",
        "parameter_bindings",
        "derived_relations",
        "required_operations",
        "rejected_hypotheses",
        "unresolved",
    ):
        if not isinstance(payload[key], list):
            raise ValueError(f"source visual audit.{key} must be an array")
    entity_ids: set[str] = set()
    for index, component in enumerate(payload["components"]):
        fields = {
            "entity_id", "name", "role", "shape", "primitive_class", "material_class",
            "layer", "layer_class", "geometric_relation", "visual_evidence", "source_locator",
            "confidence",
        }
        if not isinstance(component, dict) or fields - component.keys():
            raise ValueError(f"source visual audit.components[{index}] does not match the contract")
        entity_id = str(component["entity_id"]).strip()
        if not entity_id or entity_id in entity_ids:
            raise ValueError(f"source visual audit contains duplicate entity_id: {entity_id!r}")
        entity_ids.add(entity_id)
        if component["primitive_class"] not in {"box", "cylinder", "sheet", "polyline", "other"}:
            raise ValueError(
                f"source visual audit.components[{index}].primitive_class is unsupported"
            )
        if component["material_class"] not in {"dielectric", "conductor", "void", "other"}:
            raise ValueError(
                f"source visual audit.components[{index}].material_class is unsupported"
            )
        if component["layer_class"] not in {
            "volume", "top_coplanar", "bottom", "subtraction", "other"
        }:
            raise ValueError(
                f"source visual audit.components[{index}].layer_class is unsupported"
            )
        _validate_confidence(component["confidence"], f"source visual audit.components[{index}].confidence")
    claim_ids: set[str] = set()
    parameter_symbols: set[str] = set()
    for index, parameter in enumerate(payload["parameter_bindings"]):
        fields = {
            "claim_id", "evidence_mode", "symbol", "value", "unit", "entity_id", "quantity",
            "axis", "geometric_meaning", "dimension_evidence", "source_locator", "confidence",
        }
        if not isinstance(parameter, dict) or fields - parameter.keys():
            raise ValueError(
                f"source visual audit.parameter_bindings[{index}] does not match the contract"
            )
        claim_id = str(parameter["claim_id"]).strip()
        if not claim_id or claim_id in claim_ids:
            raise ValueError(f"source visual audit contains duplicate claim_id: {claim_id!r}")
        claim_ids.add(claim_id)
        symbol = str(parameter["symbol"]).strip().strip("$").upper()
        if not symbol or symbol in parameter_symbols:
            raise ValueError(f"source visual audit contains duplicate parameter symbol: {symbol!r}")
        parameter_symbols.add(symbol)
        if parameter["evidence_mode"] not in {"visual", "text", "unresolved"}:
            raise ValueError(
                f"source visual audit.parameter_bindings[{index}].evidence_mode is unsupported"
            )
        if str(parameter["entity_id"]) not in entity_ids:
            raise ValueError(
                f"source visual audit parameter references unknown entity_id: {parameter['entity_id']!r}"
            )
        quantities = {
            "length", "width", "radius", "diameter", "thickness", "gap", "offset",
            "angle", "material_property", "other",
        }
        if parameter["quantity"] not in quantities:
            raise ValueError(
                f"source visual audit.parameter_bindings[{index}].quantity is unsupported"
            )
        _validate_confidence(
            parameter["confidence"],
            f"source visual audit.parameter_bindings[{index}].confidence",
        )
    for index, relation in enumerate(payload["derived_relations"]):
        fields = {"claim_id", "expression", "symbols", "evidence", "source_locator", "confidence"}
        if not isinstance(relation, dict) or fields - relation.keys():
            raise ValueError(
                f"source visual audit.derived_relations[{index}] does not match the contract"
            )
        relation_id = str(relation["claim_id"]).strip()
        if not relation_id or relation_id in claim_ids:
            raise ValueError(f"source visual audit contains duplicate claim_id: {relation_id!r}")
        claim_ids.add(relation_id)
        if not isinstance(relation["symbols"], list):
            raise ValueError(f"source visual audit.derived_relations[{index}].symbols must be an array")
        _validate_confidence(
            relation["confidence"],
            f"source visual audit.derived_relations[{index}].confidence",
        )
    component_names = {
        str(item["name"]).strip().casefold() for item in payload["components"]
    }
    operation_orders: set[int] = set()
    for index, operation in enumerate(payload["required_operations"]):
        fields = {"operation", "target", "operands", "order"}
        if not isinstance(operation, dict) or fields - operation.keys():
            raise ValueError(
                f"source visual audit.required_operations[{index}] does not match the contract"
            )
        if operation["operation"] not in {"unite", "subtract", "intersect"}:
            raise ValueError(
                f"source visual audit.required_operations[{index}].operation is unsupported"
            )
        if not isinstance(operation["operands"], list) or not operation["operands"]:
            raise ValueError(
                f"source visual audit.required_operations[{index}].operands must be a non-empty array"
            )
        references = [operation["target"], *operation["operands"]]
        unknown = [
            name for name in references if str(name).strip().casefold() not in component_names
        ]
        if unknown:
            raise ValueError(
                f"source visual audit.required_operations[{index}] references unknown components: {unknown}"
            )
        order = operation["order"]
        if not isinstance(order, int) or isinstance(order, bool) or order in operation_orders:
            raise ValueError(
                f"source visual audit.required_operations[{index}].order must be a unique integer"
            )
        operation_orders.add(order)


def _normalize_evidence_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _apply_operator_audit(
    candidate: dict[str, Any],
    audit: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    repaired = json.loads(json.dumps(candidate, ensure_ascii=False))
    repairs: list[dict[str, Any]] = []
    audit_entities = {str(item["entity_id"]): item for item in audit["components"]}
    audit_entities_by_name = {
        _normalize_component_name(str(item["name"])): item for item in audit["components"]
    }
    canonical_materials = {
        "dielectric": "FR4_epoxy",
        "conductor": "copper",
        "void": "vacuum",
    }
    for index, component in enumerate(repaired.get("components", [])):
        if not isinstance(component, dict):
            continue
        binding = component.get("evidence_binding")
        entity_id = str(binding.get("entity_id", "")) if isinstance(binding, dict) else ""
        expected = audit_entities.get(entity_id) or audit_entities_by_name.get(
            _normalize_component_name(str(component.get("name", "")))
        )
        if expected is None:
            continue
        desired_binding = {
            "mode": "visual",
            "entity_id": expected["entity_id"],
            "primitive_class": expected["primitive_class"],
            "material_class": expected["material_class"],
            "layer_class": expected["layer_class"],
        }
        _replace_with_repair(
            component,
            "evidence_binding",
            desired_binding,
            f"components[{index}].evidence_binding",
            repairs,
        )
        material_class = str(expected["material_class"])
        if not _material_matches_class(str(component.get("material", "")), material_class):
            canonical_material = canonical_materials.get(material_class)
            if canonical_material:
                _replace_with_repair(
                    component,
                    "material",
                    canonical_material,
                    f"components[{index}].material",
                    repairs,
                )

    audit_parameters = {
        str(item["symbol"]).strip().strip("$").upper(): item
        for item in audit["parameter_bindings"]
    }
    for index, parameter in enumerate(repaired.get("parameters", [])):
        if not isinstance(parameter, dict):
            continue
        symbol = str(parameter.get("symbol", "")).strip().strip("$").upper()
        expected = audit_parameters.get(symbol)
        if expected is None:
            continue
        desired_binding = {
            "mode": expected["evidence_mode"],
            "claim_id": expected["claim_id"],
            "entity_id": expected["entity_id"],
            "quantity": expected["quantity"],
            "axis": expected["axis"],
        }
        _replace_with_repair(
            parameter,
            "semantic_binding",
            desired_binding,
            f"parameters[{index}].semantic_binding",
            repairs,
        )

    _replace_with_repair(
        repaired,
        "derived_relations",
        audit["derived_relations"],
        "derived_relations",
        repairs,
    )
    _replace_with_repair(
        repaired,
        "operations",
        audit["required_operations"],
        "operations",
        repairs,
    )
    return repaired, repairs


def _replace_with_repair(
    container: dict[str, Any],
    key: str,
    value: Any,
    path: str,
    repairs: list[dict[str, Any]],
) -> None:
    before = container.get(key)
    if before == value:
        return
    container[key] = json.loads(json.dumps(value, ensure_ascii=False))
    repairs.append({"path": path, "before": before, "after": value})


def _state_evidence_artifacts(state: Any) -> list[tuple[str, Path]]:
    evidence_artifacts: list[tuple[str, Path]] = []
    for artifact_name, packet_name in (
        ("source_visual_audit", "visual_audit"),
        ("source_visual_verdict", "visual_verdict"),
    ):
        raw_path = state.artifacts.get(artifact_name)
        if raw_path:
            evidence_artifacts.append((packet_name, Path(raw_path).resolve()))
    visual_input_names = sorted(
        name for name in state.artifacts if name.startswith("source_visual_input_")
    )
    for index, artifact_name in enumerate(visual_input_names, start=1):
        evidence_artifacts.append(
            (f"visual_input_{index}", Path(state.artifacts[artifact_name]).resolve())
        )
    return evidence_artifacts


def _normalize_component_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _normalize_expression(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _operation_signature(operation: dict[str, Any]) -> tuple[str, str, tuple[str, ...], int | None]:
    operands = operation.get("operands", [])
    if not isinstance(operands, list):
        operands = []
    order = operation.get("order")
    if isinstance(order, bool) or not isinstance(order, int):
        order = None
    return (
        str(operation.get("operation", "")).strip().casefold(),
        _normalize_component_name(str(operation.get("target", ""))),
        tuple(sorted(_normalize_component_name(str(item)) for item in operands)),
        order,
    )


def _operation_references(operation: dict[str, Any]) -> list[Any]:
    operands = operation.get("operands", [])
    if not isinstance(operands, list):
        operands = []
    return [operation.get("target"), *operands]


def _material_matches_class(material: str, material_class: str) -> bool:
    normalized = material.casefold()
    if material_class == "void":
        return any(token in normalized for token in ("void", "vacuum", "air"))
    if material_class == "conductor":
        return any(token in normalized for token in ("copper", "pec", "conductor", "metal"))
    if material_class == "dielectric":
        return any(token in normalized for token in ("fr-4", "fr4", "dielectric", "substrate"))
    return bool(normalized.strip())


def _primitive_matches_class(primitive: str, primitive_class: str) -> bool:
    normalized = primitive.casefold()
    aliases = {
        "box": ("box", "brick", "rectangular prism"),
        "cylinder": ("cylinder", "disc", "disk", "circle"),
        "sheet": ("sheet", "rectangle", "polygon"),
        "polyline": ("polyline", "line"),
    }
    if primitive_class == "other":
        return bool(normalized.strip())
    return any(token in normalized for token in aliases[primitive_class])


def _validate_visual_verdict(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("source visual verdict must be a JSON object")
    required = {
        "passed",
        "critical_findings",
        "component_count_correct",
        "topology_correct",
        "parameter_checks",
        "cross_design_contamination",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"source visual verdict is missing keys: {sorted(missing)}")
    for key in ("passed", "component_count_correct", "topology_correct"):
        if not isinstance(payload[key], bool):
            raise ValueError(f"source visual verdict.{key} must be a boolean")
    for key in ("critical_findings", "parameter_checks", "cross_design_contamination"):
        if not isinstance(payload[key], list):
            raise ValueError(f"source visual verdict.{key} must be an array")
    for index, check in enumerate(payload["parameter_checks"]):
        fields = {"symbol", "value_correct", "meaning_correct", "visual_evidence"}
        if not isinstance(check, dict) or fields - check.keys():
            raise ValueError(
                f"source visual verdict.parameter_checks[{index}] does not match the contract"
            )
        if not isinstance(check["value_correct"], bool) or not isinstance(
            check["meaning_correct"], bool
        ):
            raise ValueError(
                f"source visual verdict.parameter_checks[{index}] flags must be booleans"
            )


def _parameter_map(parameters: list[Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    result: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for item in parameters:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().strip("$").upper()
        if not symbol:
            continue
        if symbol in result:
            duplicates.append(symbol)
        result[symbol] = item
    return result, sorted(set(duplicates))


def _review_packet(
    candidate: Path,
    report: Path,
    evidence_artifacts: list[tuple[str, Path]] | None = None,
) -> dict[str, Any]:
    entries = []
    paths = [("candidate", candidate), ("report", report), *(evidence_artifacts or [])]
    for name, path in paths:
        data = path.read_bytes()
        entries.append(
            {"name": name, "path": str(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return {
        "artifacts": entries,
        "approval_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "instruction": "Review the candidate and report, then approve this exact hash. Any edit invalidates it.",
    }
