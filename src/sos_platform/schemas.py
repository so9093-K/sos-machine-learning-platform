"""API와 실행기가 공유하는 Data Workspace 입력 계약."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    """순서가 보존되는 재현 가능한 변환 정의."""

    steps: list[Step] = Field(min_length=1, max_length=30)
    order_by: str | None = None
    group_by: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_grouping(self):
        if len(set(self.group_by)) != len(self.group_by):
            raise ValueError("group_by columns must be distinct")
        if self.order_by and self.order_by in self.group_by:
            raise ValueError("order_by must not also be a group_by column")
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


class AuthMeOut(BaseModel):
    """인증 신원의 공개 계약. 토큰 내용은 반환하지 않는다."""

    actor_id: str
    actor_type: Literal["human", "service"]
    roles: list[str]
    capabilities: list[str]
    mode: Literal["development", "token"]


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
    row_count_basis: Literal["parquet_metadata", "not_computed"]
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
    kind: Literal["profile", "transform", "iceberg_import"]
    dataset_id: str
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


class ExplorationJobOut(JobOut):
    """Discovery Job만 반환하는 신규 API 응답 계약."""

    kind: Literal["exploration"]
    result: ExplorationOut | None


class JobSummaryOut(BaseModel):
    """목록에 필요한 값과 마지막 실행 시도만 노출한다."""

    id: str
    kind: Literal["profile", "transform", "iceberg_import"]
    dataset_id: str
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
