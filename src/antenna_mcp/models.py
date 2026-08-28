from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Backend(str, Enum):
    HFSS = "hfss"
    CST = "cst"


class Template(str, Enum):
    STRONG = "strong_description"
    WEAK = "weak_description"
    PAPER = "paper_reconstruction"


class ModelingRequest(BaseModel):
    description: str = Field(min_length=10)
    backend: Backend = Backend.HFSS
    template: Template = Template.STRONG
    attachments: list[str] = Field(default_factory=list)
    include_2d: bool = True
    include_simulation: bool = False
    include_optimization: bool = False
    model: str | None = None

    @model_validator(mode="after")
    def valid_stages(self) -> "ModelingRequest":
        if self.include_optimization and not self.include_simulation:
            raise ValueError("include_optimization requires include_simulation")
        return self


class ParameterBound(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    lower: float
    upper: float
    unit: str = "mm"

    @model_validator(mode="after")
    def valid_interval(self) -> "ParameterBound":
        if self.upper <= self.lower:
            raise ValueError("upper must be greater than lower")
        return self


class MetricSpec(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    name: str
    expression: str = "dB(S(1,1))"
    report_category: str | None = None
    context: str | dict[str, Any] | None = None
    variations: dict[str, str | list[str]] | None = None
    reducer: Literal["min", "max", "mean", "at_frequency"] = "min"
    frequency_ghz: float | None = None
    frequency_min_ghz: float | None = None
    frequency_max_ghz: float | None = None
    goal: Literal[
        "minimize",
        "maximize",
        "target",
        "upper_bound",
        "lower_bound",
    ] = "minimize"
    target: float | None = None
    weight: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def valid_metric(self) -> "MetricSpec":
        if self.reducer == "at_frequency" and self.frequency_ghz is None:
            raise ValueError("frequency_ghz is required for at_frequency")
        if self.goal in {"target", "upper_bound", "lower_bound"} and self.target is None:
            raise ValueError(f"target is required for {self.goal} goal")
        if (self.frequency_min_ghz is None) != (self.frequency_max_ghz is None):
            raise ValueError("frequency_min_ghz and frequency_max_ghz must be set together")
        if (
            self.frequency_min_ghz is not None
            and self.frequency_max_ghz is not None
            and self.frequency_max_ghz <= self.frequency_min_ghz
        ):
            raise ValueError("frequency_max_ghz must be greater than frequency_min_ghz")
        return self


class OptimizationRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    project_path: str
    design_name: str | None = None
    setup_sweep: str = "Setup1 : Sweep1"
    parameters: list[ParameterBound] = Field(min_length=1)
    metrics: list[MetricSpec] = Field(min_length=1)
    max_trials: int = Field(default=30, ge=1, le=10000)
    seed: int = 42
    strategy: Literal["adaptive_surrogate", "random"] = "adaptive_surrogate"
    initial_samples: int | None = Field(default=None, ge=1)
    candidate_pool_size: int = Field(default=512, ge=32, le=100000)
    exploration_weight: float = Field(default=1.5, ge=0.0, le=10.0)
    require_convergence: bool = True
    max_delta_s: float = Field(default=0.02, gt=0.0)
    maximum_adaptive_passes: int | None = Field(default=None, ge=1, le=100)
    verify_parameter_effects: bool = True
    initial_points: list[dict[str, float]] = Field(default_factory=list)
    save_best_as: str = "optimized.aedt"
    session_mode: Literal["new", "existing"] = "new"
    grpc_port: int | None = Field(default=None, ge=1, le=65535)

    @model_validator(mode="after")
    def valid_optimization(self) -> "OptimizationRequest":
        setup_parts = [item.strip() for item in self.setup_sweep.split(":")]
        if len(setup_parts) != 2 or not all(setup_parts):
            raise ValueError("setup_sweep must use 'SetupName : SweepName'")
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("parameter names must be unique")
        metric_names = [metric.name for metric in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("metric names must be unique")
        if not self.save_best_as.lower().endswith(".aedt") or Path(self.save_best_as).name != self.save_best_as:
            raise ValueError("save_best_as must be a plain .aedt filename")
        bounds = {parameter.name: parameter for parameter in self.parameters}
        for point in self.initial_points:
            if set(point) != set(bounds):
                raise ValueError("every initial point must contain exactly all optimization parameters")
            for name, value in point.items():
                if not bounds[name].lower <= value <= bounds[name].upper:
                    raise ValueError(f"initial value for {name} is outside its bounds")
        if self.initial_samples is not None and self.initial_samples > self.max_trials:
            raise ValueError("initial_samples cannot exceed max_trials")
        if self.session_mode == "new" and self.grpc_port is not None:
            raise ValueError("grpc_port is only valid for session_mode='existing'")
        if self.session_mode == "existing" and self.grpc_port is None:
            raise ValueError("grpc_port is required for strict existing-session attachment")
        return self


class OptimizationPlan(BaseModel):
    """Simulator-independent optimization settings generated before a project exists."""

    model_config = ConfigDict(allow_inf_nan=False)

    design_name: str | None = None
    setup_sweep: str = "Setup1 : Sweep1"
    parameters: list[ParameterBound] = Field(min_length=1)
    metrics: list[MetricSpec] = Field(min_length=1)
    max_trials: int = Field(default=30, ge=1, le=10000)
    seed: int = 42
    strategy: Literal["adaptive_surrogate", "random"] = "adaptive_surrogate"
    initial_samples: int | None = Field(default=None, ge=1)
    candidate_pool_size: int = Field(default=512, ge=32, le=100000)
    exploration_weight: float = Field(default=1.5, ge=0.0, le=10.0)
    require_convergence: bool = True
    max_delta_s: float = Field(default=0.02, gt=0.0)
    maximum_adaptive_passes: int | None = Field(default=None, ge=1, le=100)
    verify_parameter_effects: bool = True
    initial_points: list[dict[str, float]] = Field(default_factory=list)
    save_best_as: str = "optimized.aedt"

    def to_request(
        self,
        project_path: str,
        session_mode: Literal["new", "existing"] = "new",
        grpc_port: int | None = None,
    ) -> OptimizationRequest:
        return OptimizationRequest(
            project_path=project_path,
            session_mode=session_mode,
            grpc_port=grpc_port,
            **self.model_dump(),
        )

    @model_validator(mode="after")
    def validate_as_request(self) -> "OptimizationPlan":
        # Reuse the complete bounds, metric, initial-point, and filename validation.
        OptimizationRequest(project_path="pending.aedt", **self.model_dump())
        return self


class PipelineRequest(BaseModel):
    description: str = Field(min_length=10)
    attachments: list[str] = Field(default_factory=list)
    template: Template = Template.STRONG
    backend: Backend = Backend.HFSS
    include_2d: bool = True
    model: str | None = None
    project_name: str = "antenna_pipeline.aedt"
    session_mode: Literal["new", "existing"] = "new"
    grpc_port: int | None = Field(default=None, ge=1, le=65535)

    @model_validator(mode="after")
    def valid_pipeline(self) -> "PipelineRequest":
        if Path(self.project_name).name != self.project_name or not self.project_name.lower().endswith(".aedt"):
            raise ValueError("project_name must be a plain .aedt filename")
        if self.session_mode == "new" and self.grpc_port is not None:
            raise ValueError("grpc_port is only valid for session_mode='existing'")
        if self.session_mode == "existing" and self.grpc_port is None:
            raise ValueError("grpc_port is required for strict existing-session attachment")
        return self


class Evaluation(BaseModel):
    trial: int
    parameters: dict[str, float]
    metrics: dict[str, float]
    score: float | None
    status: Literal["ok", "rejected", "failed"] = "ok"
    convergence: dict[str, Any] | None = None
    error: str | None = None


class JobState(BaseModel):
    job_id: str
    kind: Literal["modeling", "optimization", "pipeline"]
    status: Literal[
        "created",
        "running",
        "awaiting_review",
        "ready_to_optimize",
        "completed",
        "failed",
    ] = "created"
    request: dict[str, Any]
    current_stage: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
