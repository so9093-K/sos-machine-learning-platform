"""API와 실행기가 공유하는 Data Workspace 입력 계약."""

from datetime import datetime
from typing import Literal
from unicodedata import category

from pydantic import (
    BaseModel,
    Field,
    StrictFloat,
    StrictInt,
    computed_field,
    field_validator,
    model_validator,
)

from .ml_outputs import output_contract


class Step(BaseModel):
    """검증 및 직렬화가 가능한 피처 변환 단계."""

    op: Literal["select", "filter_not_null", "fill_null", "cast", "lag", "rolling_mean"]
    column: str | None = None
    columns: list[str] | None = None
    value: int | float | str | bool | None = None
    dtype: Literal["String", "Int64", "Float64", "Boolean", "Date", "Datetime"] | None = None
    window: int = Field(default=3, ge=2, le=1000)
    periods: int = Field(default=1, ge=1, le=1000)
    alias: str | None = None

    @model_validator(mode="after")
    def validate_step(self):
        if self.op == "select":
            if not self.columns or len(set(self.columns)) != len(self.columns):
                raise ValueError("select requires distinct columns")
        elif not self.column:
            raise ValueError(f"{self.op} requires a column")
        if self.op == "fill_null" and self.value is None:
            raise ValueError("fill_null requires a value")
        if self.op == "cast" and self.dtype is None:
            raise ValueError("cast requires a dtype")
        return self


class Transform(BaseModel):
    """기존 변환 단계 또는 단독 불변 관측치 UUID 발급 작업."""

    steps: list[Step] = Field(default_factory=list, max_length=30)
    order_by: str | None = None
    group_by: list[str] = Field(default_factory=list, max_length=4)
    observation_id_column: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_grouping(self):
        if len(set(self.group_by)) != len(self.group_by):
            raise ValueError("group_by columns must be distinct")
        if self.order_by and self.order_by in self.group_by:
            raise ValueError("order_by must not also be a group_by column")
        if self.observation_id_column is not None:
            if self.steps or self.order_by or self.group_by:
                raise ValueError("Observation identity materialization must be a standalone transform")
        elif not self.steps:
            raise ValueError("Transform requires steps unless materializing observation IDs")
        return self


class ProblemOut(BaseModel):
    """오류 응답 공통 계약. detail은 안전한 메시지 또는 입력 검증 위치를 담는다."""

    type: str
    title: str
    status: int
    detail: object
    code: str
    category: str
    request_id: str
    instance: str
    retry_policy: str


class HealthOut(BaseModel):
    """API 프로세스가 HTTP 요청에 응답하는지 나타낸다."""

    status: Literal["ok"]


class ReadyServicesOut(BaseModel):
    """API가 요청을 처리하는 데 반드시 필요한 저장소. 하나라도 없으면 API 전체가 준비되지 않았다."""

    database: Literal["ready", "unavailable"]
    storage: Literal["ready", "unavailable"]


class ReadyFeaturesOut(BaseModel):
    """API와 따로 실행되는 기능의 상태. 하나가 멈춰도 API와 다른 기능은 계속 동작한다."""

    job_execution: Literal["ready", "unavailable"] = Field(description="비동기 Job을 실행하는 Worker")
    online_serving: Literal["ready", "unavailable", "not_configured"] = Field(
        description="온라인 추론을 실행하는 서빙 Runtime 연결")


class ReadyOut(BaseModel):
    """준비 상태. unavailable은 API 전체 장애(503), degraded는 일부 기능 장애(200)다.

    실패 시에도 동일한 JSON 구조를 반환한다.
    """

    status: Literal["ready", "degraded", "unavailable"]
    services: ReadyServicesOut
    features: ReadyFeaturesOut


class AuthMeOut(BaseModel):
    """인증 신원의 공개 계약. 토큰 내용은 반환하지 않는다."""

    actor_id: str
    actor_type: Literal["human", "service"]
    roles: list[str]
    capabilities: list[str]
    mode: Literal["development", "token"]


class BrandingSettingsOut(BaseModel):
    """인증 이전에도 노출할 수 있는 제품 표시 브랜드. 기술 식별자와 별개다."""

    brand_name: str = Field(min_length=1, max_length=48)


class BrandingSettingsUpdate(BaseModel):
    """관리자만 변경할 수 있는 표시 브랜드."""

    brand_name: str = Field(min_length=1, max_length=48)

    @model_validator(mode="after")
    def validate_name(self) -> "BrandingSettingsUpdate":
        self.brand_name = self.brand_name.strip()
        if not self.brand_name or any(category(char) in {"Cc", "Cf", "Cs"} for char in self.brand_name):
            raise ValueError("Invalid brand name")
        return self


class InstanceSettingsOut(BaseModel):
    """공통 플랫폼의 표시 전용 설정. 인증·배포 환경과 별개의 계약."""

    display_name: str = Field(min_length=1, max_length=48)


class InstanceSettingsUpdate(BaseModel):
    """관리자만 변경하는 표시명. 내부 플랫폼·리소스 식별자가 아니다."""

    display_name: str = Field(min_length=1, max_length=48)

    @model_validator(mode="after")
    def validate_name(self) -> "InstanceSettingsUpdate":
        self.display_name = self.display_name.strip()
        if not self.display_name or any(ord(char) < 32 for char in self.display_name):
            raise ValueError("Invalid instance display name")
        return self


class AuditEventOut(BaseModel):
    """감사 저장소의 노출 가능한 정제 기록."""

    id: str
    at: str
    actor_id: str
    actor_type: str
    action: str
    resource_type: str
    resource_id: str | None
    decision: str
    outcome: str
    reason: str | None
    request_id: str | None


class DiagnosticAttemptOut(BaseModel):
    id: str
    status: str
    error_code: str | None
    failure_stage: str | None
    failure_category: str | None
    exception_type: str | None


class JobDiagnosticsOut(BaseModel):
    job_id: str
    request_id: str | None
    status: str
    error_code: str | None
    failure_stage: str | None
    failure_category: str | None
    exception_type: str | None
    attempts: list[DiagnosticAttemptOut]


class StoragePolicyOut(BaseModel):
    """서버의 유효 보존 정책과 값의 출처."""

    values: dict[str, int]
    sources: dict[str, str]
    automatic_deletion: bool
    mode: Literal["observation_only"]


class StorageObjectOut(BaseModel):
    location: str
    size_bytes: int
    modified_at: str
    status: Literal["protected", "waiting", "candidate", "unknown"]
    reason: str


class StorageInspectionOut(BaseModel):
    scope: Literal["datasets/", "results/"]
    inspected_at: str
    items: list[StorageObjectOut]
    counts: dict[str, int]
    next_cursor: str | None = None
    policy: StoragePolicyOut
    deletion_enabled: bool


class IcebergReferenceOut(BaseModel):
    dataset_id: str
    table_name: str
    snapshot_id: str
    status: Literal["retained", "available_unprotected", "missing", "unknown"]
    reason: str


class ExplorationSpec(BaseModel):
    column: str = Field(min_length=1, max_length=256)
    metric: Literal["count", "null_count", "mean", "min", "max"]
    group_by: str | None = Field(default=None, max_length=256)


class ExplorationJobRequest(BaseModel):
    dataset_id: str
    exploration: ExplorationSpec


class IsolationForestParameters(BaseModel):
    """첫 모델의 허용 하이퍼파라미터를 입력 계약으로 고정한다."""

    n_estimators: int = Field(default=100, strict=True, ge=1, le=2000)
    max_samples: int | float | Literal["auto"] = "auto"
    max_features: int | float = 1.0
    bootstrap: bool = Field(default=False, strict=True)
    random_state: int = Field(default=42, strict=True, ge=0, le=2**32 - 1)

    @field_validator("max_samples", "max_features", mode="before")
    @classmethod
    def ensure_numeric_parameter(cls, value: object, info) -> object:
        """문자열·bool을 숫자로 묵시 변환하지 않고 요청자에게 수정하게 한다."""
        if info.field_name == "max_samples" and value == "auto":
            return value
        if type(value) not in (int, float):
            raise ValueError("Parameter must be a number or supported keyword")
        return value


class MLPolicyRequest(BaseModel):
    scoring_result_id: str
    mode: Literal["fixed", "quantile"]
    threshold: float | None = Field(default=None, strict=True)
    alert_fraction: float | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def validate_policy(self):
        import math

        if self.mode == "fixed":
            if self.threshold is None or not math.isfinite(self.threshold):
                raise ValueError("Fixed policy requires a finite threshold")
            if self.alert_fraction is not None:
                raise ValueError("Fixed policy cannot set alert_fraction")
        else:
            if self.threshold is not None:
                raise ValueError("Quantile policy cannot set a fixed threshold")
            if self.alert_fraction is None or not math.isfinite(self.alert_fraction):
                raise ValueError("Quantile policy requires alert_fraction")
            if not 0 < self.alert_fraction < 1:
                raise ValueError("Quantile alert_fraction must be between 0 and 1")
        return self


class MLflowExperimentOut(BaseModel):
    id: str
    name: str


class MLflowExperimentPageOut(BaseModel):
    items: list[MLflowExperimentOut]
    next_page_token: str | None = None


class MLflowRunOut(BaseModel):
    id: str
    job_id: str
    job_kind: str
    status: str | None = None
    start_time: int | None = None
    params: dict[str, str]
    metrics: dict[str, float]


class MLflowRunPageOut(BaseModel):
    items: list[MLflowRunOut]
    next_page_token: str | None = None


class MLflowModelVersionOut(BaseModel):
    dataset_id: str | None = None
    created_at: str | None = None
    archived: bool = False
    name: str
    version: str
    model_id: str
    run_id: str | None = None
    status: str
    job_id: str
    aliases: list[str] = Field(default_factory=list)


class MLflowModelVersionPageOut(BaseModel):
    items: list[MLflowModelVersionOut]
    next_page_token: str | None = None


class MLflowExportStatusOut(BaseModel):
    job_id: str
    state: Literal["Disabled", "Pending", "Sending", "Retry", "Succeeded"]
    run_id: str | None = None
    attempts: int = 0
    last_error_code: str | None = None
    retry_after: str | None = None


class MLExplanationRequest(BaseModel):
    """Scoring 행 또는 완료된 Playground Job 하나만 설명 대상으로 지정한다."""

    scoring_result_id: str | None = None
    row_index: int | None = Field(default=None, strict=True, ge=0, le=2**63 - 1)
    playground_job_id: str | None = None
    method: Literal[
        "median_reference_feature_replacement", "permutation_shap_median_v1"
    ] = "median_reference_feature_replacement"

    @model_validator(mode="after")
    def validate_source(self):
        if self.playground_job_id is not None:
            if self.scoring_result_id is not None or self.row_index is not None:
                raise ValueError("Playground explanation cannot specify a Scoring row")
        elif self.scoring_result_id is None or self.row_index is None:
            raise ValueError("Explanation requires a Scoring row or Playground Job")
        return self


class ExplanationResultOut(BaseModel):
    id: str
    job_id: str
    attempt_id: str
    scoring_result_id: str | None = None
    playground_job_id: str | None = None
    model_revision_id: str
    row_index: int | None = None
    method: str
    output: dict[str, object]
    created_at: str


class MLEvaluationRequest(BaseModel):
    scoring_result_id: str
    policy_revision_id: str | None = None


class DecisionPolicyRevisionOut(BaseModel):
    id: str
    job_id: str
    attempt_id: str
    scoring_result_id: str
    mode: str
    threshold: float
    alert_fraction: float | None
    decision_summary: dict[str, object]
    created_at: str


class EvaluationRevisionOut(BaseModel):
    id: str
    job_id: str
    attempt_id: str
    scoring_result_id: str
    policy_revision_id: str | None
    label_coverage: str
    decision_metrics: dict[str, object]
    ranking_metrics: dict[str, object] | None
    created_at: str


class ModelEvaluationPageOut(BaseModel):
    items: list[EvaluationRevisionOut]
    next_cursor: str | None = None


class MLTemporalSplitSpec(BaseModel):
    """시간순 Train/Calibration/Test 경계. Train 이외 구간은 학습에 사용하지 않는다."""

    timestamp_column: str = Field(min_length=1, max_length=256)
    train_end: datetime
    calibration_end: datetime

    @model_validator(mode="after")
    def validate_boundaries(self):
        if (
            self.train_end.tzinfo is None
            or self.calibration_end.tzinfo is None
            or self.train_end.utcoffset() is None
            or self.calibration_end.utcoffset() is None
            or self.train_end >= self.calibration_end
        ):
            raise ValueError("Temporal boundaries must be ordered and timezone-aware")
        return self


class MLScoringTemporalScope(MLTemporalSplitSpec):
    """학습한 UTC 분할 정의의 Calibration 또는 Test 구간만 점수화한다."""

    partition: Literal["calibration", "test"]


class MLPlaygroundRequest(BaseModel):
    """Dataset 없이 명시한 소량 관측치. Job 입력으로 감사·보존된다."""

    model_revision_id: str
    features: dict[str, StrictFloat | StrictInt | None] = Field(min_length=1, max_length=256)


class MLScoringRequest(BaseModel):
    model_revision_id: str
    dataset_id: str
    label_column: str | None = Field(default=None, min_length=1, max_length=256)
    temporal_scope: MLScoringTemporalScope | None = None
    observation_id_column: str | None = Field(default=None, min_length=1, max_length=256)
    observation_time_column: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_observation_column(self):
        if self.observation_id_column is not None and (
            self.observation_id_column == self.label_column
            or (
                self.temporal_scope is not None
                and self.observation_id_column == self.temporal_scope.timestamp_column
            )
        ):
            raise ValueError("Observation ID cannot also be the label or time column")
        if self.observation_time_column is not None and (
            self.observation_time_column == self.label_column
            or self.observation_time_column == self.observation_id_column
            or (
                self.temporal_scope is not None
                and self.observation_time_column != self.temporal_scope.timestamp_column
            )
        ):
            raise ValueError("Observation time must be separate from label/ID and match temporal scope")
        return self


class ScoringResultOut(BaseModel):
    id: str
    job_id: str
    attempt_id: str
    model_revision_id: str
    dataset_id: str
    dataset_version: str
    location: str
    sha256: str
    row_count: int
    score_direction: str
    feature_names: list[str]
    score_summary: dict[str, object]
    ranking_metrics: dict[str, object] | None
    label_column: str | None
    created_at: str


class MLTrainingInputPolicy(BaseModel):
    """명시적인 학습 데이터 입력 선택 정책."""

    mode: Literal["all", "sample"] = "all"
    rows: int | None = Field(default=None, strict=True, ge=2)
    seed: int | None = Field(default=None, strict=True, ge=0, le=2**32 - 1)

    @model_validator(mode="after")
    def validate_selection(self):
        if self.mode == "sample" and (self.rows is None or self.seed is None):
            raise ValueError("Sample training requires rows and seed")
        if self.mode == "all" and (self.rows is not None or self.seed is not None):
            raise ValueError("Full training cannot specify sample rows or seed")
        return self


class MLForkRequest(BaseModel):
    """원본 학습을 바탕으로 재학습하고 모델 그룹을 선택한다."""

    params: IsolationForestParameters | None = None
    registry_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,39}$")


class MLTrainRequest(BaseModel):
    dataset_id: str
    registry_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9-]{0,39}$")
    experiment_title: str = Field(default="기본 실험", min_length=1, max_length=80)
    features: list[str] = Field(min_length=1)
    params: IsolationForestParameters = Field(default_factory=IsolationForestParameters)
    training_input: MLTrainingInputPolicy = Field(default_factory=MLTrainingInputPolicy)
    missing_policy: Literal["reject", "train_median"] = "reject"
    temporal_split: MLTemporalSplitSpec | None = None

    @model_validator(mode="after")
    def validate_features(self):
        if not self.experiment_title.strip():
            raise ValueError("Experiment title must not be blank")
        if len(set(self.features)) != len(self.features):
            raise ValueError("Features must be distinct")
        if any(not name or len(name) > 256 for name in self.features):
            raise ValueError("Invalid feature name")
        if self.temporal_split is not None and self.temporal_split.timestamp_column in self.features:
            raise ValueError("Temporal split timestamp must not be a training feature")
        return self


class ModelRevisionOut(BaseModel):

    registry_version: str | None = None
    lifecycle_state: Literal["Active", "Deleting", "Deleted"] = "Active"
    id: str
    job_id: str
    attempt_id: str
    dataset_id: str
    dataset_version: str
    algorithm: str
    location: str
    sha256: str
    features: list[str]
    params: dict[str, object]
    training_score_summary: dict[str, object]
    runtime: dict[str, object]
    created_at: str

    @computed_field
    @property
    def output_type(self) -> str | None:
        """알고리즘별 출력 계약의 종류. 분석 주제·소비자가 출력의 의미를 구별한다."""
        contract = output_contract(self.algorithm)
        return contract.output_type if contract else None


class ModelOfferingRequest(BaseModel):
    """다른 Actor가 모델을 고를 때 읽는 목적·적용 대상·한계 설명."""

    description: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_description(self):
        self.description = self.description.strip()
        if not self.description:
            raise ValueError("모델 제공 설명을 입력하세요.")
        return self


class ModelOfferingStateOut(BaseModel):
    """현재 Actor가 본 모델 판본의 제공 상태. 학습 데이터 접근 권한은 포함하지 않는다."""

    offered: bool
    description: str | None = None
    offered_by: str | None = None
    offered_at: str | None = None
    withdrawn_at: str | None = None
    owned: bool


class ModelOfferingOut(BaseModel):
    model: ModelRevisionOut
    description: str
    offered_by: str
    offered_at: str


class ModelOfferingPageOut(BaseModel):
    items: list[ModelOfferingOut]
    next_cursor: str | None = None


class WorkRequest(BaseModel):
    dataset_id: str
    kind: Literal["profile", "transform"]
    transform: Transform | None = None

    @model_validator(mode="after")
    def validate_request(self):
        if self.kind == "transform" and self.transform is None:
            raise ValueError("transform job requires a transform specification")
        if self.kind != "transform" and self.transform is not None:
            raise ValueError("only transform job accepts transformations")
        return self


class FeaturePreview(BaseModel):
    dataset_id: str
    transform: Transform


class IcebergImport(BaseModel):
    dataset_id: str
    table_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")


class IcebergConnect(BaseModel):
    table_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    snapshot_id: str | None = None


class DatasetOut(BaseModel):
    """불변 Dataset 참조의 API 응답 계약."""

    id: str
    name: str
    kind: Literal["file", "iceberg"]
    location: str
    version: str
    created_at: str


class DatasetPageOut(BaseModel):
    """상한 있는 최신순 Dataset 목록 페이지."""

    items: list[DatasetOut]
    next_cursor: str | None = None


class UploadPolicyOut(BaseModel):
    max_size_bytes: int
    supported_extensions: list[str]


class DiscoveryColumnOut(BaseModel):
    name: str
    dtype: str


class DatasetOverviewOut(BaseModel):
    dataset_id: str
    version: str
    row_count: int | None
    row_count_basis: Literal["parquet_metadata", "upload_validation", "not_computed"]
    column_count: int
    size_bytes: int | None
    columns: list[DiscoveryColumnOut]


class DatasetColumnsOut(BaseModel):
    dataset_id: str
    version: str
    total: int
    columns: list[DiscoveryColumnOut]


class DatasetFilter(BaseModel):
    """필드 존재 여부를 검증한 뒤 안전한 SQL 바인딩으로 평가하는 조건."""

    column: str = Field(min_length=1, max_length=256)
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte", "is_null", "is_not_null"]
    value: str | int | float | bool | None = None

    @model_validator(mode="after")
    def validate_filter(self):
        if self.op in ("is_null", "is_not_null"):
            if self.value is not None:
                raise ValueError("Null operators do not accept values")
        elif self.value is None:
            raise ValueError("Comparison requires a non-null value")
        return self


class DatasetQuery(BaseModel):
    """임의 SQL이나 파일 경로를 받지 않는 범용 Dataset 조회."""

    columns: list[str] | None = Field(default=None, max_length=1024)
    filters: list[DatasetFilter] = Field(default_factory=list, max_length=8)
    order_by: str | None = Field(default=None, max_length=256)
    descending: bool = False
    limit: int = Field(default=30, ge=1, le=100)

    @model_validator(mode="after")
    def validate_columns(self):
        if self.columns is not None and (
            not self.columns or len(set(self.columns)) != len(self.columns)
        ):
            raise ValueError("columns must be nonempty and unique")
        return self


class ExplorationRequest(ExplorationSpec):
    """대화형 탐색은 제한된 선두 샘플만 분석한다."""


class ExplorationRowOut(BaseModel):
    category: object | None
    value: int | float | str | None


class ExplorationOut(BaseModel):
    dataset_id: str
    version: str
    scope: Literal["full", "sample"]
    sample_limit: int | None
    column: str
    metric: str
    group_by: str | None
    truncated: bool
    rows: list[ExplorationRowOut]


class UnderstandingRoleOut(BaseModel):
    role: str
    basis: str
    confirmed: bool


class UnderstandingColumnOut(BaseModel):
    name: str
    dtype: str
    null_count: int
    null_ratio: float | None
    nan_count: int | None
    distinct_count: int
    distinct_exact: bool
    roles: list[UnderstandingRoleOut]
    is_constant: bool
    min: int | float | str | None = None
    max: int | float | str | None = None
    mean: int | float | str | None = None


class UnderstandingInsightOut(BaseModel):
    kind: str
    title: str
    reason: str
    scope: Literal["sample"]
    requires_confirmation: bool
    request: ExplorationSpec


class UnderstandingProfileOut(BaseModel):
    dataset_id: str
    version: str
    algorithm_version: str
    scope: Literal["sample", "full"]
    sampling_method: str
    sample_limit: int | None
    sample_seed: int | None = None
    row_count: int
    columns: list[UnderstandingColumnOut]
    insights: list[UnderstandingInsightOut]


class UnderstandingRequest(BaseModel):
    scope: Literal["sample", "full"] = "sample"


class UnderstandingOut(BaseModel):
    dataset_id: str
    version: str
    profile: UnderstandingProfileOut | None = None
    job_status: Literal["Queued", "Running", "Succeeded", "Failed", "Cancelled"] | None = None
    job_id: str | None = None


class PreviewOut(BaseModel):
    """제한된 행 미리보기와 컬럼 타입."""

    # 외부 JSON 필드 'schema'는 유지하고 BaseModel.schema와의 이름 충돌만 피한다.
    schema_: dict[str, str] = Field(alias="schema")
    rows: list[dict[str, object]]
    returned_rows: int


class FeaturePreviewOut(PreviewOut):
    sampled: bool


class ColumnProfileOut(BaseModel):
    name: str
    dtype: str
    nulls: int
    nan_count: int | None
    unique: int
    min: int | float | str | None = None
    max: int | float | str | None = None
    mean: float | None = None


class ProfileOut(BaseModel):
    sampled: bool
    sample_limit: int | None
    row_count: int
    columns: list[ColumnProfileOut]


class AttemptOut(BaseModel):
    id: str
    job_id: str
    number: int
    status: str
    started_at: str
    finished_at: str | None = None
    error: str | None = None
    error_code: str | None = None
    failure_stage: str | None = None


class JobOut(BaseModel):
    id: str
    kind: Literal["profile", "transform", "iceberg_import", "ml_playground", "feature_register", "feature_retrieve", "feature_retire"]
    dataset_id: str | None
    spec: dict[str, object]
    status: Literal["Queued", "Running", "Succeeded", "Failed", "Cancelled"]
    cancel_requested: bool
    result: dict[str, object] | None
    error: str | None
    error_code: str | None = None
    failure_stage: str | None = None
    retry_policy: str | None = None
    request_id: str | None = None
    created_at: str
    updated_at: str
    attempts: list[AttemptOut]


class MLPolicyJobOut(JobOut):
    kind: Literal["ml_policy"]


class MLExplanationJobOut(JobOut):
    kind: Literal["ml_explain"]


class MLEvaluationJobOut(JobOut):
    kind: Literal["ml_evaluate"]


class MLScoringJobOut(JobOut):
    kind: Literal["ml_score"]


class MLPlaygroundJobOut(JobOut):
    kind: Literal["ml_playground"]


class MLTrainingJobOut(JobOut):
    """기존 Job 응답의 enum 확장 없이 ML 실행 상태를 제공한다."""

    kind: Literal["ml_train"]


class ExplorationJobOut(JobOut):
    """Discovery Job만 반환하는 신규 API 응답 계약."""

    kind: Literal["exploration"]
    result: ExplorationOut | None


class JobSummaryOut(BaseModel):
    """목록에 필요한 값과 마지막 실행 시도만 노출한다."""

    id: str
    kind: Literal["profile", "transform", "iceberg_import", "ml_playground", "feature_register", "feature_retrieve", "feature_retire"]
    dataset_id: str | None
    spec: dict[str, object]
    status: Literal["Queued", "Running", "Succeeded", "Failed", "Cancelled"]
    cancel_requested: bool
    result: dict[str, object] | None
    error: str | None
    error_code: str | None = None
    failure_stage: str | None = None
    retry_policy: str | None = None
    request_id: str | None = None
    created_at: str
    updated_at: str
    attempt_count: int
    latest_attempt: AttemptOut | None


class FeatureRegistrationSummaryOut(JobSummaryOut):
    """등록 Job의 실행 상태와 현재 SOS 폐기 상태를 구별한다."""

    retired: bool = False
    blocked: bool = Field(description="SOS 폐기 정책에 따라 신규 Feature 조회가 차단된 상태")


class FeatureRegistrationPageOut(BaseModel):
    """Feast Feature Service 등록 Job의 재사용 가능한 키셋 목록."""

    items: list[FeatureRegistrationSummaryOut]
    next_cursor: str | None = None


class MLExplanationJobSummaryOut(JobSummaryOut):
    kind: Literal["ml_explain"]


class MLExplanationJobPageOut(BaseModel):
    items: list[MLExplanationJobSummaryOut]
    next_cursor: str | None = None


class MLPolicyJobSummaryOut(JobSummaryOut):
    kind: Literal["ml_policy"]


class MLPolicyJobPageOut(BaseModel):
    items: list[MLPolicyJobSummaryOut]
    next_cursor: str | None = None


class MLEvaluationJobSummaryOut(JobSummaryOut):
    kind: Literal["ml_evaluate"]


class MLEvaluationJobPageOut(BaseModel):
    items: list[MLEvaluationJobSummaryOut]
    next_cursor: str | None = None


class MLPlaygroundJobSummaryOut(JobSummaryOut):
    kind: Literal["ml_playground"]


class MLPlaygroundJobPageOut(BaseModel):
    items: list[MLPlaygroundJobSummaryOut]
    next_cursor: str | None = None


class MLScoringJobSummaryOut(JobSummaryOut):
    kind: Literal["ml_score"]


class MLScoringJobPageOut(BaseModel):
    items: list[MLScoringJobSummaryOut]
    next_cursor: str | None = None


class MLTrainingJobSummaryOut(JobSummaryOut):
    kind: Literal["ml_train"]


class MLTrainingJobPageOut(BaseModel):
    items: list[MLTrainingJobSummaryOut]
    next_cursor: str | None = None


class ExplorationJobSummaryOut(JobSummaryOut):
    kind: Literal["exploration"]
    result: ExplorationOut | None


class ExplorationJobPageOut(BaseModel):
    items: list[ExplorationJobSummaryOut]
    next_cursor: str | None = None


class JobPageOut(BaseModel):
    """키셋 커서로 이어지는 경량 Job 목록과 다음 조회 위치."""

    items: list[JobSummaryOut]
    next_cursor: str | None = None


class ExampleOut(BaseModel):
    """예제는 별도 가짜 결과가 아니라 Dataset 등록 안내를 제공한다."""

    id: str
    title: str
    description: str
    columns: list[str]
    guide: str
    row_count: int
    version: str


class SnapshotOut(BaseModel):
    snapshot_id: str
    timestamp_ms: int
