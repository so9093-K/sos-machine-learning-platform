# 0025 — 모델 사용 판정과 모델 그룹의 SOS 정본화

- 상태: **Accepted**
- 대체: [ADR 0015](0015-mlflow-management-ownership.md) 결정 3(Registry 등록 완료 전 Scoring 금지)·결정 1의 Registered Model/Version 관리 정본 중 모델 그룹, [ADR 0017](0017-model-lifecycle.md) 결정 2(그룹 이름·설명·보관의 MLflow 정본)·결정 3(`sos.archived` 태그 기반 사용 제한)·결정 4의 등록 미완료 삭제 거부
- 관련: [Contracts](../contracts.md), [Workflows](../workflows.md), [Diagnostics](../diagnostics.md)

## 배경

SOS는 게시 시점에 모델 패키지·Runtime 계약·SHA를 검증하고 불변 아티팩트로 Model Revision을 게시한다.
그런데 Scoring·Playground 제출, 서빙 배포·전환, 온라인 추론(30초 캐시)마다 MLflow에 버전·그룹을 실시간
대조했고, 제공·서빙·Scoring은 MLflow 등록 완료(`registry_version`)를 조건으로 삼았다. 판본 보관과 그룹
이름·설명·보관은 MLflow 태그가 정본이었다. 그 결과 MLflow가 응답하지 않으면 이미 신뢰한 판본도 쓸 수
없었고, MLflow를 구성하지 않은 설치에서는 어떤 모델도 쓸 수 없었다. 한 판본의 사용 가능 여부가 SOS 수명주기·
제공과 MLflow 등록·태그 다섯 곳에 나뉘어 있었다.

## 결정

1. **사용 판정은 SOS가 한다:** SOS가 검증·게시한 판본의 새 사용(Scoring·단일 입력 계산·온라인 추론·서빙
   생성·전환·되돌리기·제공)은 SOS 상태만으로 판정한다. 수명주기(`Active`)·판본 보관·그룹 보관은 서로 다른
   사실로 저장하고, 판정 규칙은 `model_use.use_blocked` 하나다. 접수는 판본 행과 그룹 행을 같은 순서로
   잠가 보관·삭제·제공과 직렬화한다. Actor 권한과 다른 Actor의 사용에 필요한 제공 여부는 그대로 적용한다.
2. **MLflow 등록은 외부 기록이다:** SOS 판본은 소유자의 SOS 모델 그룹 이름으로 MLflow에 버전을 남기고,
   결과(`registry_version`)와 전송 상태(Outbox)를 기록한다. 등록 지연·실패는 이미 게시한 판본의 사용을
   막지 않는다. 신규 연결(`ml_import`)은 원본 버전 검증에 MLflow가 필요하다.
3. **보관과 모델 그룹은 SOS가 소유한다:** 판본 보관(`model_revisions.archived_at`)과 그룹의 이름·설명·보관
   (`model_groups`)은 SOS가 정본이며 MLflow 태그에 반영하지 않는다. MLflow에서 태그를 바꿔도 사용 판정은
   바뀌지 않는다. 보관은 새 사용만 막으며 제공·서빙 중인 판본(그룹)은 먼저 중단해야 보관할 수 있다. 보관한
   그룹에는 새 학습·연결 판본을 만들지 않는다. 학습·연결 접수가 그룹 행을 만든다.
4. **삭제는 외부 등록 상태에 따른다:** 등록 전송을 시도하지 않은 판본은 MLflow 없이 SOS 자산만 삭제한다.
   등록했거나 결과가 불확실한(재시도 중) 판본은 등록 버전 또는 게시 Job 태그로 MLflow의 실제 버전을 찾아
   삭제한 뒤 확정한다. 전송 중인 판본은 삭제를 거부하고, 삭제가 시작된 판본은 전송이 등록하지 않는다.
   외부에서 생산한 원본 버전은 SOS가 삭제하지 않는다.
5. **대체된 구조는 제거한다:** 제출·추론 시 Registry 대조(`verify_registered_version`·30초 캐시),
   `registry_version`을 조건으로 한 제공·서빙·Scoring 분기, `sos.archived`·`sos.title` 태그 읽기·쓰기,
   MLflow 기반 그룹 API(`/api/ml/registry/groups*`)와 버전 목록 API(`/api/ml/registry/models`),
   공급 스크립트의 Registry 등록 대기를 제거한다. 이전 MLflow 보관 태그는 옮기지 않는다(보존할 운영
   데이터가 없음).

## 근거와 영향

MLflow는 생산 Run·원본 Registry·출처와 SOS 판본의 등록 기록을 맡고, SOS는 자신이 수용한 판본을 실제로
쓸 수 있는지를 정한다. MLflow 장애는 신규 연결·등록 기록·Experiment 조회에만 영향을 주며 Scoring·
Playground·추론·서빙·제공·보관을 막지 않는다. 그룹 관리 API는 MLflow 없이 동작하고, 그룹 삭제만 MLflow에
남은 빈 Registered Model을 정리한다. 판본 보관은 `PUT/DELETE /api/ml/models/{id}/archive`, 그룹은
`/api/ml/model-groups`로 관리한다.
