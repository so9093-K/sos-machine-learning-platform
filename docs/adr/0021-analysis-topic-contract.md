# 0021 — 분석 주제와 등록 Pipeline의 분리

- 상태: **Accepted**
- 관련: [Contracts](../contracts.md#분석-주제), [Experience](../experience.md#분석-주제), [ADR 0016](0016-dagster-orchestration.md), [ADR 0019](0019-ray-serve-online-serving.md)
- 범위: 분석 주제의 식별·분류·입력·출력 계약, 모델의 주제 적합성 판정, 알고리즘별 출력 계약의 위치

## 배경

활동 이상 탐지 주제의 분류(학습 유형·분석 과제)와 입력 Feature는 등록 Pipeline
`activity-anomaly-if`의 정의에 들어 있었다. 웹은 주제를 그 Pipeline ID에 연결했고, 모델이 주제에
맞는지를 Pipeline의 알고리즘과 Feature 순서가 같은지로 판정했다. 그래서 같은 주제에 다른 알고리즘의
모델을 공급하면 Playground와 온라인 API 안내에서 사라지고, Pipeline이 없으면 주제를 제공할 수 없었다.
모델 출력의 의미(종류·방향·척도)도 Scoring·Playground·서빙·설명에 같은 문자열로 따로 들어 있었다.

## 결정

1. **주제는 독립 카탈로그다:** 분석 주제는 식별자, 이름, 학습 유형·분석 과제, 입력 Feature, 출력
   계약(`output_type`)을 가진다(`analysis_topics.py`, `GET /api/topics`). 주제의 분류와 입력 의미는
   주제가 소유한다.
2. **Pipeline은 주제를 가리킨다:** 등록 Pipeline은 주제에 모델을 공급하는 선택적 Workflow다. 정의의
   `topic`으로 주제를 가리키고 입력은 주제의 Feature를 따른다. 분류를 따로 갖지 않는다. 주제는 Pipeline
   없이도 제공될 수 있다.
3. **적합성은 계약으로 판정한다:** 모델이 주제에 맞는지는 입력 Feature 이름 집합과 출력 계약이 같은지로
   판정하며 알고리즘과 Pipeline은 보지 않는다. 입력은 이름으로 대응하므로 순서는 비교하지 않는다.
4. **출력 계약은 알고리즘별로 한곳에 둔다:** 알고리즘이 내는 출력 계약(종류·점수 방향·척도)은
   `ml_outputs.py`가 정하고, Scoring·Playground·온라인 추론·설명이 이를 쓴다. 모델 응답은 알고리즘에서
   정해지는 `output_type`을 함께 보낸다. 새 알고리즘은 자기 출력 계약을 여기에 더한다.

## 근거

- 사용자 주제(무엇을 이해하고 활용하는가)와 Workflow(어떻게 학습·평가·공급하는가)는 다른 책임이다.
  한쪽에 다른 쪽의 식별자와 분류를 넣으면 둘 중 하나를 늘릴 때 다른 쪽도 바뀐다.
- 주제의 입력·출력 계약만 맞으면 Playground·온라인 추론·API 안내는 알고리즘과 관계없이 같은 방식으로
  동작한다. 알고리즘별 차이는 학습·복원·출력 계약에서만 다룬다.
- 범용 카탈로그 프레임워크 없이 주제 정의와 출력 계약을 분리하는 것으로 충분하다.

## 영향

- `GET /api/topics`가 추가되고, Pipeline 정의 응답에서 `learning_type`·`task_type`이 빠진다.
- 모델 판본 응답에 `output_type`이 추가된다(`isolation_forest` → `anomaly_score_v1`).
- 웹의 분류 탐색·주제 Playground·모델 적용 경로는 주제 카탈로그를 쓴다. 등록 Pipeline 화면은 그대로다.
