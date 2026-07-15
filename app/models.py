from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ParameterSource = Literal["drawing", "derived", "inferred", "assumed", "manual"]
ParameterType = Literal["number", "expression", "text", "choice"]
ParameterValue = float | str
FeatureValue = str | float | int | bool
FeatureOperationKind = Literal["add", "cut", "intersect", "modify", "pattern"]
FeatureCoverageStatus = Literal["planned", "implemented", "approximated", "unresolved", "unsupported"]
CapabilityStatus = Literal["supported", "experimental", "unsupported"]
FeaturePatternKind = Literal["linear", "polar", "mirror", "path"]
SpecificationStatus = Literal["confirmed", "needs_input", "assumed", "conflicted"]


class CADParameter(BaseModel):
    label: str
    type: ParameterType = "number"
    value: Optional[ParameterValue] = None
    expression: Optional[str] = None
    unit: str = "mm"
    min: Optional[float] = None
    max: Optional[float] = None
    step: float = 0.1
    options: List[str] = Field(default_factory=list)
    source: ParameterSource = "manual"
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    editable: bool = True

    @model_validator(mode="after")
    def required_payload_for_type(self) -> "CADParameter":
        if self.type == "expression" and not self.expression:
            raise ValueError("expression parameters require expression")
        if self.type == "number" and self.value is None:
            raise ValueError("number parameters require value")
        if self.type == "number" and not isinstance(self.value, (int, float)):
            raise ValueError("number parameters require numeric value")
        if self.type == "text":
            if self.value is None:
                self.value = ""
            if not isinstance(self.value, str):
                self.value = str(self.value)
            if len(self.value) > 80:
                raise ValueError("text parameters are limited to 80 characters")
        if self.type == "choice":
            if self.value is None:
                self.value = self.options[0] if self.options else ""
            if not isinstance(self.value, str):
                self.value = str(self.value)
            if self.options and self.value not in self.options:
                raise ValueError("choice parameter value must be one of options")
        return self


class SourceInfo(BaseModel):
    filename: str = ""
    mime_type: str = ""
    image_ref: Optional[str] = None
    image_sha256: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    image_data: Optional[str] = None


class DrawingAnalysis(BaseModel):
    views: List[Dict[str, Any]] = Field(default_factory=list)
    dimensions: List[Dict[str, Any]] = Field(default_factory=list)
    features: List[Dict[str, Any]] = Field(default_factory=list)
    uncertainties: List[Dict[str, Any]] = Field(default_factory=list)


class SpecificationDimension(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    value: Optional[ParameterValue] = None
    expression: Optional[str] = None
    unit: str = "mm"
    source: ParameterSource = "inferred"
    confidence: float = Field(default=0.5, ge=0, le=1)
    status: SpecificationStatus = "needs_input"
    critical: bool = True
    min: Optional[float] = None
    max: Optional[float] = None
    alternatives: List[ParameterValue] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)


class FeaturePlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: Optional[str] = None
    plane: Optional[Literal["XY", "XZ", "YZ"]] = None
    origin: Optional[List[FeatureValue]] = None
    axis: Optional[str] = None
    direction: Optional[str] = None
    rotation_deg: Optional[FeatureValue] = None
    offsets: Dict[str, FeatureValue] = Field(default_factory=dict)

    @field_validator("origin")
    @classmethod
    def origin_has_three_coordinates(cls, value: Optional[List[FeatureValue]]) -> Optional[List[FeatureValue]]:
        if value is not None and len(value) != 3:
            raise ValueError("feature placement origin must contain exactly three coordinates")
        return value


class SpecificationFeature(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    type: str
    operation: FeatureOperationKind
    target: Optional[str] = None
    parameters: Dict[str, FeatureValue] = Field(default_factory=dict)
    placement: FeaturePlacement = Field(default_factory=FeaturePlacement)
    status: SpecificationStatus = "needs_input"
    critical_fields: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence: List[str] = Field(default_factory=list)
    alternatives: Dict[str, List[FeatureValue]] = Field(default_factory=dict)


class SpecificationAssumption(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value: ParameterValue
    rationale: str
    affected_ids: List[str] = Field(default_factory=list)
    status: SpecificationStatus = "assumed"


class SpecificationQuestion(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    field_id: str
    prompt: str
    alternatives: List[ParameterValue] = Field(default_factory=list)
    required: bool = True


class SpecificationAnnotation(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    field_id: str
    field_ids: List[str] = Field(default_factory=list)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    label: str


class DraftSpecification(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = "Untitled specification"
    units: str = "mm"
    source: SourceInfo = Field(default_factory=SourceInfo)
    analysis: DrawingAnalysis = Field(default_factory=DrawingAnalysis)
    dimensions: List[SpecificationDimension] = Field(default_factory=list)
    features: List[SpecificationFeature] = Field(default_factory=list)
    assumptions: List[SpecificationAssumption] = Field(default_factory=list)
    questions: List[SpecificationQuestion] = Field(default_factory=list)
    annotations: List[SpecificationAnnotation] = Field(default_factory=list)
    free_text: str = ""

    @model_validator(mode="after")
    def stable_ids_are_unique(self) -> "DraftSpecification":
        ids = [item.id for item in self.dimensions] + [item.id for item in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("dimension and feature IDs must be unique")
        return self


class SpecificationEditRequest(BaseModel):
    specification: DraftSpecification
    dimension_values: Dict[str, ParameterValue] = Field(default_factory=dict)
    accepted_feature_ids: List[str] = Field(default_factory=list)
    accepted_assumption_ids: List[str] = Field(default_factory=list)
    clarifications: Dict[str, str] = Field(default_factory=dict)


class FeatureProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    dimensions: Dict[str, FeatureValue] = Field(default_factory=dict)
    points: List[List[FeatureValue]] = Field(default_factory=list)

    @field_validator("points")
    @classmethod
    def profile_points_are_two_dimensional(cls, value: List[List[FeatureValue]]) -> List[List[FeatureValue]]:
        if any(len(point) != 2 for point in value):
            raise ValueError("feature profile points must contain exactly two coordinates")
        return value


class FeaturePattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: FeaturePatternKind
    count: Optional[FeatureValue] = None
    pitch: Optional[FeatureValue] = None
    angle_deg: Optional[FeatureValue] = None
    axis: Optional[str] = None
    path: Optional[str] = None
    start_margin: Optional[FeatureValue] = None
    end_margin: Optional[FeatureValue] = None

    @model_validator(mode="after")
    def required_pattern_fields(self) -> "FeaturePattern":
        if self.type in {"linear", "polar", "path"} and self.count is None:
            raise ValueError(f"{self.type} pattern requires count")
        if self.type == "linear" and (self.pitch is None or not self.axis):
            raise ValueError("linear pattern requires pitch and axis")
        if self.type == "polar" and (self.angle_deg is None or not self.axis):
            raise ValueError("polar pattern requires angle_deg and axis")
        if self.type == "path" and not self.path:
            raise ValueError("path pattern requires path")
        if self.type == "mirror" and not self.axis:
            raise ValueError("mirror pattern requires axis")
        return self


class FeatureEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: List[str] = Field(default_factory=list)
    dimension_ids: List[str] = Field(default_factory=list)
    source: str = "inferred"
    note: Optional[str] = None


class FeatureOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = ""
    type: str = Field(min_length=1)
    operation: FeatureOperationKind
    target: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list)
    source_feature_ids: List[str] = Field(default_factory=list)
    profile: Optional[FeatureProfile] = None
    placement: Optional[FeaturePlacement] = None
    pattern: Optional[FeaturePattern] = None
    parameters: Dict[str, FeatureValue] = Field(default_factory=dict)
    evidence: FeatureEvidence = Field(default_factory=FeatureEvidence)
    confidence: float = Field(default=1.0, ge=0, le=1)
    status: FeatureCoverageStatus = "planned"
    implementation: Optional[str] = None
    assumption: Optional[str] = None
    minimum_printable_thickness: Optional[FeatureValue] = None
    capability_status: CapabilityStatus = "experimental"

    @model_validator(mode="after")
    def operation_has_required_relationships(self) -> "FeatureOperation":
        if (
            self.status not in {"unresolved", "unsupported"}
            and self.operation in {"cut", "intersect", "modify", "pattern"}
            and not self.target
        ):
            raise ValueError(f"{self.operation} operation requires target")
        if self.status not in {"unresolved", "unsupported"} and self.operation == "pattern" and self.pattern is None:
            raise ValueError("pattern operation requires pattern")
        if self.status == "implemented" and not self.implementation:
            raise ValueError("implemented feature requires implementation")
        if self.status in {"approximated", "unresolved", "unsupported"} and not self.assumption:
            raise ValueError(f"{self.status} feature requires assumption")
        return self


class FeatureGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operations: List[FeatureOperation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_operation_relationships(self) -> "FeatureGraph":
        ids = [operation.id for operation in self.operations]
        duplicate_ids = sorted({feature_id for feature_id in ids if ids.count(feature_id) > 1})
        if duplicate_ids:
            raise ValueError(f"duplicate feature operation IDs: {', '.join(duplicate_ids)}")

        known_ids = set(ids)
        errors = []
        for operation in self.operations:
            references = ([operation.target] if operation.target else []) + operation.depends_on
            for reference in references:
                if reference == operation.id:
                    errors.append(f"feature '{operation.id}' cannot reference itself")
                elif reference not in known_ids:
                    errors.append(f"feature '{operation.id}' references unknown feature '{reference}'")
        if errors:
            raise ValueError("; ".join(errors))
        return self


class FeatureCoverageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    operation_ids: List[str] = Field(default_factory=list)
    status: FeatureCoverageStatus
    confidence: float = Field(default=0.5, ge=0, le=1)
    explanation: Optional[str] = None


class FeatureCoverageReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: List[FeatureCoverageEntry] = Field(default_factory=list)
    all_accounted_for: bool = True
    has_unresolved: bool = False


class FeatureSummary(BaseModel):
    id: str
    name: str
    type: str
    description: str


class CADSource(BaseModel):
    language: str = "cadquery-python"
    source: str
    source_kind: Literal["generated", "compiled"] = "generated"
    implemented_feature_ids: List[str] = Field(default_factory=list)
    entry_variable: str = "result"
    generation_attempt: int = 1


class RenderArtifact(BaseModel):
    view: Literal["front", "top", "right", "isometric"]
    mime_type: str = "image/png"
    image_data: str
    sha256: str
    width: int
    height: int


class VisualIssue(BaseModel):
    issue_type: Literal["missing", "extra", "misplaced", "dimension_mismatch", "other"]
    severity: Literal["low", "medium", "high"]
    description: str
    feature_id: Optional[str] = None
    view: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0, le=1)


class VisualComparison(BaseModel):
    status: Literal["not_run", "advisory", "failed"] = "not_run"
    match_score: Optional[float] = Field(default=None, ge=0, le=1)
    issues: List[VisualIssue] = Field(default_factory=list)


class GenerationResult(BaseModel):
    status: str = "new"
    syntax_status: Literal["not_run", "success", "failed"] = "not_run"
    geometry_status: Literal["not_run", "success", "failed"] = "not_run"
    semantic_status: Literal["not_run", "success", "failed"] = "not_run"
    warnings: List[str] = Field(default_factory=list)
    execution_time_ms: Optional[int] = None
    bounding_box: Optional[Dict[str, float]] = None
    volume_mm3: Optional[float] = None
    solid_count: Optional[int] = None
    feature_measurements: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    render_artifacts: Dict[str, RenderArtifact] = Field(default_factory=dict)
    visual_comparison: VisualComparison = Field(default_factory=VisualComparison)
    error: Optional[Dict[str, Any]] = None


class CADProject(BaseModel):
    version: int = 1
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = "Untitled project"
    units: str = "mm"
    source: SourceInfo = Field(default_factory=SourceInfo)
    analysis: DrawingAnalysis = Field(default_factory=DrawingAnalysis)
    parameters: Dict[str, CADParameter]
    feature_graph: FeatureGraph = Field(default_factory=FeatureGraph)
    feature_coverage: FeatureCoverageReport = Field(default_factory=FeatureCoverageReport)
    feature_summary: List[FeatureSummary] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    cad: CADSource
    generation: GenerationResult = Field(default_factory=GenerationResult)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class PreviewRequest(BaseModel):
    project: CADProject
    parameters: Dict[str, ParameterValue] = Field(default_factory=dict)


class CompareRequest(BaseModel):
    project: CADProject
