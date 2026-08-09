from __future__ import annotations

import math
import os
import random
import shutil
from pathlib import Path
from typing import Callable

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
        working = self.store.job_dir(state.job_id) / "working.aedt"
        shutil.copy2(source, working)
        state.artifacts["source_project"] = str(source)
        state.artifacts["working_project"] = str(working)
        self.store.save_state(state)
        return state

    def run(self, job_id: str) -> JobState:
        if os.getenv("ANTENNA_MCP_ALLOW_SIMULATION") != "1":
            raise PermissionError("Set ANTENNA_MCP_ALLOW_SIMULATION=1 to allow local HFSS execution")
        state = self.store.load_state(job_id)
        request = OptimizationRequest.model_validate(state.request)
        working = Path(state.artifacts["working_project"])
        evaluator = self.evaluator_factory(working, request)
        rng = random.Random(request.seed)
        initial_count = request.initial_samples or min(
            request.max_trials,
            max(4, 2 * len(request.parameters) + 1),
        )
        initial_design = latin_hypercube(rng, request, initial_count)
        state.status = "running"
        state.current_stage = "sampling"
        self.store.save_state(state)
        best: Evaluation | None = None
        successful: list[Evaluation] = []
        try:
            for trial in range(1, request.max_trials + 1):
                if trial <= len(request.initial_points):
                    point = request.initial_points[trial - 1]
                elif trial - len(request.initial_points) <= len(initial_design):
                    point = initial_design[trial - len(request.initial_points) - 1]
                elif request.strategy == "random":
                    point = random_point(rng, request)
                else:
                    point = propose_surrogate(rng, request, successful)
                try:
                    metrics = evaluator.evaluate(point)
                    evaluation = Evaluation(
                        trial=trial,
                        parameters=point,
                        metrics=metrics,
                        score=score_metrics(request.metrics, metrics),
                    )
                    if best is None or evaluation.score < best.score:
                        best = evaluation
                        evaluator.save_best(self.store.job_dir(job_id) / request.save_best_as)
                    successful.append(evaluation)
                except Exception as exc:
                    evaluation = Evaluation(
                        trial=trial,
                        parameters=point,
                        metrics={},
                        score=math.inf,
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                self.store.append_jsonl(job_id, "trials.jsonl", evaluation.model_dump())
            if best is None:
                raise RuntimeError("all optimization trials failed")
            best_path = self.store.write_artifact(job_id, "best.json", best.model_dump_json(indent=2) + "\n")
            state.artifacts["trials"] = str(self.store.job_dir(job_id) / "trials.jsonl")
            state.artifacts["best"] = str(best_path)
            state.artifacts["optimized_project"] = str(self.store.job_dir(job_id) / request.save_best_as)
            state.current_stage = "complete"
            state.status = "completed"
        except Exception as exc:
            state.status = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
        finally:
            evaluator.close()
            self.store.save_state(state)
        return state

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
        )


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
