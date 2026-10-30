"""분석 주제 카탈로그. 화면·외부 시스템이 주제의 입력 의미와 출력 계약을 같은 기준으로 읽는다."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .analysis_topics import topics
from .security import require

READ_JOB = Depends(require("job.read"))


class AnalysisTopicOut(BaseModel):
    id: str
    name: str
    learning_type: str
    task_type: str
    required_features: list[str] = Field(
        description="주제의 입력 Feature. 이름이 모두 같은 모델이 이 주제의 입력을 받는다.")
    output_type: str = Field(description="주제가 기대하는 모델 출력 계약(모델의 output_type과 비교)")


def topic_router() -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/topics",
        tags=["Analysis Topics"],
        summary="분석 주제 조회",
        description="제공하는 분석 주제의 분류·입력 Feature·출력 계약을 반환합니다. 주제에 맞는 모델은 "
        "알고리즘과 관계없이 입력 Feature 이름과 출력 계약(output_type)이 같은 모델입니다.",
        response_model=list[AnalysisTopicOut],
    )
    def list_topics(actor=READ_JOB):
        return topics()

    return router
