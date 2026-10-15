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
    status: Literal["protected", "missing", "unknown"]
    reason: str


class WorkRequest(BaseModel):
    dataset_id: str
    kind: Literal["profile", "transform"]
    transform: Transform | None = None

    @model_validator(mode="after")
    def validate_request(self):
        if self.kind == "transform" and self.transform is None:
            raise ValueError("transform job requires a transform specification")
        if self.kind == "profile" and self.transform is not None:
            raise ValueError("profile job does not accept transformations")
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


class PreviewOut(BaseModel):
    """제한된 행 미리보기와 컬럼 타입."""

    schema: dict[str, str]
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
    min: float | int | None = None
    max: float | int | None = None
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


class JobOut(BaseModel):
    id: str
    kind: Literal["profile", "transform", "iceberg_import"]
    dataset_id: str
    spec: dict[str, object]
    status: Literal["Queued", "Running", "Succeeded", "Failed", "Cancelled"]
    cancel_requested: bool
    result: dict[str, object] | None
    error: str | None
    created_at: str
    updated_at: str
    attempts: list[AttemptOut]


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
    created_at: str
    updated_at: str
    attempt_count: int
    latest_attempt: AttemptOut | None


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
