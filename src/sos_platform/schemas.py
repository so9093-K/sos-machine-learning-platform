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
