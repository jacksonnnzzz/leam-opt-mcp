from __future__ import annotations

import hashlib
import json
import math
import os
import random
import shutil
from pathlib import Path
from typing import Any, Callable

from .evaluators import Evaluator, HfssEvaluator
from .models import Evaluation, JobState, MetricSpec, OptimizationRequest
from .workspace import WorkspaceStore


class OptimizationService:
    def __init__(
        self,
        store: WorkspaceStore,
        evaluator_factory: Callable[[Path, OptimizationRequest], Evaluator] | None = None,
    ) -> None:
        self.store = store
        self.evaluator_factory = evaluator_factory or self._hfss_factory

    def create(self, request: OptimizationRequest) -> JobState:
        source = Path(request.project_path).expanduser().resolve()
        if source.suffix.lower() != ".aedt" or not source.is_file():
            raise FileNotFoundError("project_path must point to an existing .aedt file")
        state = self.store.create_job("optimization", request.model_dump(mode="json"))
        working = self.store.job_dir(state.job_id) / f"{state.job_id}_working.aedt"
        shutil.copy2(source, working)
        snapshot = {
            "schema_version": "1.0",
            "request_sha256": _json_sha256(request.model_dump(mode="json")),
            "source_project": str(source),
            "source_project_sha256": _file_sha256(source),
            "working_project_initial_sha256": _file_sha256(working),
        }
        snapshot_path = self.store.write_artifact(
            state.job_id,
            "optimization_snapshot.json",
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        )
        state.artifacts["source_project"] = str(source)
        state.artifacts["working_project"] = str(working)
        state.artifacts["optimization_snapshot"] = str(snapshot_path)
        self.store.save_state(state)
        return state

    def run(self, job_id: str) -> JobState:
        if os.getenv("ANTENNA_MCP_ALLOW_SIMULATION") != "1":
            raise PermissionError("Set ANTENNA_MCP_ALLOW_SIMULATION=1 to allow local HFSS execution")
        state = self.store.load_state(job_id)
        if state.status == "completed":
            return state
        request = OptimizationRequest.model_validate(state.request)
        working = Path(state.artifacts["working_project"])
        if not working.is_file():
            raise FileNotFoundError("optimization working project is missing")
        snapshot = json.loads(
            Path(state.artifacts["optimization_snapshot"]).read_text(encoding="utf-8")
        )
        if snapshot.get("request_sha256") != _json_sha256(request.model_dump(mode="json")):
            raise RuntimeError("optimization request no longer matches its immutable snapshot")

        evaluator: Evaluator | None = None
        best: Evaluation | None = None
        successful: list[Evaluation] = []
        try:
            history = _load_history(self.store.job_dir(job_id), request)
            existing = {item.trial: item for item in history}
            evaluator = self.evaluator_factory(working, request)
            self._ensure_parameter_effects(
                job_id, state, request, evaluator, snapshot
            )
            rng = random.Random(request.seed)
            initial_count = request.initial_samples or min(
                request.max_trials,
                max(4, 2 * len(request.parameters) + 1),
            )
            initial_design = latin_hypercube(rng, request, initial_count)
            state.status = "running"
            state.error = None
            state.current_stage = "sampling"
            self.store.save_state(state)
            for trial in range(1, request.max_trials + 1):
                if trial <= len(request.initial_points):
                    point = request.initial_points[trial - 1]
                elif trial - len(request.initial_points) <= len(initial_design):
                    point = initial_design[trial - len(request.initial_points) - 1]
                elif request.strategy == "random":
                    point = random_point(rng, request)
                else:
                    point = propose_surrogate(rng, request, successful)
                previous = existing.get(trial)
                if previous is not None:
                    if previous.parameters != point:
                        raise RuntimeError(
                            f"trial {trial} parameters do not match deterministic replay"
                        )
                    if previous.status == "ok":
                        if previous.score is None:
                            raise RuntimeError(f"trial {trial} has no score")
                        successful.append(previous)
                        if best is None or previous.score < best.score:
                            best = previous
                    continue
                try:
                    metrics = evaluator.evaluate(point)
                    convergence = _convergence_evidence(evaluator, request)
                    if request.require_convergence and not convergence.get("converged", False):
                        evaluation = Evaluation(
                            trial=trial,
                            parameters=point,
                            metrics=metrics,
                            score=None,
                            status="rejected",
                            convergence=convergence,
                            error="convergence gate failed",
                        )
                    else:
                        evaluation = Evaluation(
                            trial=trial,
                            parameters=point,
                            metrics=metrics,
                            score=score_metrics(request.metrics, metrics),
                            convergence=convergence or None,
                        )
                        if best is None or evaluation.score < best.score:
                            evaluator.save_best(
                                self.store.job_dir(job_id) / request.save_best_as
                            )
                            best = evaluation
                        successful.append(evaluation)
                except Exception as exc:
                    evaluation = Evaluation(
                        trial=trial,
                        parameters=point,
                        metrics={},
                        score=None,
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                self.store.append_jsonl(job_id, "trials.jsonl", evaluation.model_dump())
            if best is None:
                raise RuntimeError("no converged optimization trial completed successfully")
            optimized_project = self.store.job_dir(job_id) / request.save_best_as
            if not optimized_project.is_file():
                raise RuntimeError("best-project artifact is missing")
            best_path = self.store.write_artifact(job_id, "best.json", best.model_dump_json(indent=2) + "\n")
            state.artifacts["trials"] = str(self.store.job_dir(job_id) / "trials.jsonl")
            state.artifacts["best"] = str(best_path)
            state.artifacts["optimized_project"] = str(optimized_project)
            state.current_stage = "complete"
            state.status = "completed"
        except Exception as exc:
            state.status = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
        finally:
            if evaluator is not None:
                evaluator.close()
            self.store.save_state(state)
        return state

    def preflight(self, job_id: str) -> JobState:
        state = self.store.load_state(job_id)
        if state.status == "completed":
            return state
        request = OptimizationRequest.model_validate(state.request)
        working = Path(state.artifacts["working_project"])
        if not working.is_file():
            raise FileNotFoundError("optimization working project is missing")
        snapshot = json.loads(
            Path(state.artifacts["optimization_snapshot"]).read_text(encoding="utf-8")
        )
        if snapshot.get("request_sha256") != _json_sha256(request.model_dump(mode="json")):
            raise RuntimeError("optimization request no longer matches its immutable snapshot")
        evaluator: Evaluator | None = None
        try:
            evaluator = self.evaluator_factory(working, request)
            self._ensure_parameter_effects(
                job_id, state, request, evaluator, snapshot
            )
            state.status = "created"
            state.current_stage = "preflight_complete"
            state.error = None
        except Exception as exc:
            state.status = "failed"
            state.current_stage = "preflight_failed"
            state.error = f"{type(exc).__name__}: {exc}"
        finally:
            if evaluator is not None:
                evaluator.close()
            self.store.save_state(state)
        return state

    def _ensure_parameter_effects(
        self,
        job_id: str,
        state: JobState,
        request: OptimizationRequest,
        evaluator: Evaluator,
        snapshot: dict[str, Any],
    ) -> None:
        if not request.verify_parameter_effects:
            return
        effect_path = self.store.job_dir(job_id) / "parameter_effects.json"
        if effect_path.exists():
            effect_evidence = json.loads(effect_path.read_text(encoding="utf-8"))
        else:
            method = getattr(evaluator, "verify_parameter_effects", None)
            if method is None:
                raise RuntimeError("evaluator does not provide parameter-effect evidence")
            effect_evidence = method(request.parameters)
            effect_evidence["request_sha256"] = snapshot["request_sha256"]
            self.store.write_artifact(
                job_id,
                "parameter_effects.json",
                json.dumps(effect_evidence, ensure_ascii=False, indent=2) + "\n",
            )
        if effect_evidence.get("request_sha256") != snapshot["request_sha256"]:
            raise RuntimeError(
                "parameter-effect evidence does not match the optimization request"
            )
        if effect_evidence.get("all_parameters_effective") is not True:
            ineffective = [
                str(item.get("name"))
                for item in effect_evidence.get("parameters", [])
                if item.get("geometry_changed") is not True
            ]
            raise RuntimeError(
                "optimization parameters do not change geometry: " + ", ".join(ineffective)
            )
        state.artifacts["parameter_effects"] = str(effect_path)

    @staticmethod
    def _hfss_factory(path: Path, request: OptimizationRequest) -> Evaluator:
        return HfssEvaluator(
            project_path=path,
            design_name=request.design_name,
            setup_sweep=request.setup_sweep,
            metrics=request.metrics,
            units={p.name: p.unit for p in request.parameters},
            session_mode=request.session_mode,
            grpc_port=request.grpc_port,
            max_delta_s=request.max_delta_s,
            maximum_adaptive_passes=request.maximum_adaptive_passes,
        )


def _convergence_evidence(
    evaluator: Evaluator, request: OptimizationRequest
) -> dict[str, Any]:
    method = getattr(evaluator, "convergence_evidence", None)
    if method is None:
        if request.require_convergence:
            raise RuntimeError("evaluator does not provide convergence evidence")
        return {}
    evidence = method()
    if not isinstance(evidence, dict):
        raise RuntimeError("evaluator convergence evidence must be an object")
    if request.require_convergence:
        if not isinstance(evidence.get("converged"), bool):
            raise RuntimeError("convergence evidence must contain boolean converged")
        delta = evidence.get("final_max_magnitude_delta_s")
        if not isinstance(delta, (int, float)) or not math.isfinite(float(delta)):
            raise RuntimeError("convergence evidence must contain finite final Delta S")
    return dict(evidence)


def _load_history(job_dir: Path, request: OptimizationRequest) -> list[Evaluation]:
    path = job_dir / "trials.jsonl"
    if not path.exists():
        return []
    history: list[Evaluation] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise RuntimeError(f"empty optimization history line {line_number}")
        try:
            item = Evaluation.model_validate_json(line)
        except Exception as exc:
            raise RuntimeError(f"invalid optimization history line {line_number}: {exc}") from exc
        if item.trial != line_number:
            raise RuntimeError("optimization history trials must be contiguous and immutable")
        if item.trial > request.max_trials:
            raise RuntimeError("optimization history exceeds max_trials")
        history.append(item)
    return history


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def score_metrics(specs: list[MetricSpec], values: dict[str, float]) -> float:
    score = 0.0
    for spec in specs:
        value = values[spec.name]
        if spec.goal == "minimize":
            term = value
        elif spec.goal == "maximize":
            term = -value
        elif spec.goal == "target":
            term = abs(value - spec.target)
        elif spec.goal == "upper_bound":
            term = max(value - spec.target, 0.0)
        else:
            term = max(spec.target - value, 0.0)
        score += spec.weight * term
    return score


def random_point(rng: random.Random, request: OptimizationRequest) -> dict[str, float]:
    return {
        bound.name: rng.uniform(bound.lower, bound.upper)
        for bound in request.parameters
    }


def latin_hypercube(
    rng: random.Random,
    request: OptimizationRequest,
    count: int,
) -> list[dict[str, float]]:
    """Generate a reproducible space-filling initial design."""
    if count <= 0:
        return []
    columns: dict[str, list[float]] = {}
    for bound in request.parameters:
        values = [
            bound.lower + (bound.upper - bound.lower) * ((index + rng.random()) / count)
            for index in range(count)
        ]
        rng.shuffle(values)
        columns[bound.name] = values
    return [
        {bound.name: columns[bound.name][row] for bound in request.parameters}
        for row in range(count)
    ]


def propose_surrogate(
    rng: random.Random,
    request: OptimizationRequest,
    history: list[Evaluation],
) -> dict[str, float]:
    """Select a candidate with a Gaussian-process lower confidence bound.

    Coordinates are normalized to the configured bounds. The RBF length scale is
    deliberately conservative because each HFSS solve is expensive and the sample
    count is normally small. If numerical fitting fails, exploration falls back to
    a reproducible random point instead of stopping the optimization job.
    """
    if len(history) < 2:
        return random_point(rng, request)
    try:
        import numpy as np

        bounds = request.parameters

        def normalize(point: dict[str, float]) -> list[float]:
            return [
                (point[bound.name] - bound.lower) / (bound.upper - bound.lower)
                for bound in bounds
            ]

        x_train = np.asarray([normalize(item.parameters) for item in history], dtype=float)
        y_train = np.asarray([item.score for item in history], dtype=float)
        y_mean = float(y_train.mean())
        y_scale = float(y_train.std())
        if y_scale < 1e-12:
            y_scale = 1.0
        y_standard = (y_train - y_mean) / y_scale

        length_scale = max(0.12, min(0.5, 0.8 / max(1, len(bounds)) ** 0.5))

        def kernel(left, right):
            delta = left[:, None, :] - right[None, :, :]
            squared = np.sum(delta * delta, axis=2)
            return np.exp(-0.5 * squared / (length_scale * length_scale))

        covariance = kernel(x_train, x_train)
        covariance.flat[:: len(history) + 1] += 1e-6
        alpha = np.linalg.solve(covariance, y_standard)

        pool = np.asarray(
            [
                [rng.random() for _ in bounds]
                for _ in range(request.candidate_pool_size)
            ],
            dtype=float,
        )
        cross = kernel(pool, x_train)
        mean = cross @ alpha
        solved = np.linalg.solve(covariance, cross.T)
        variance = np.maximum(1e-12, 1.0 - np.sum(cross * solved.T, axis=1))
        acquisition = mean - request.exploration_weight * np.sqrt(variance)

        # Do not repeat an existing evaluation due to a nearly singular surrogate.
        distances = np.sqrt(np.sum((pool[:, None, :] - x_train[None, :, :]) ** 2, axis=2))
        acquisition[np.min(distances, axis=1) < 1e-6] = np.inf
        selected = pool[int(np.argmin(acquisition))]
        return {
            bound.name: bound.lower + float(selected[index]) * (bound.upper - bound.lower)
            for index, bound in enumerate(bounds)
        }
    except Exception:
        return random_point(rng, request)
