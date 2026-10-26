# 0016 — Dagster OSS를 Workflow 정본으로 채택

- 상태: **Accepted**
- 관련 문서: [Architecture](../architecture.md), [Contracts](../contracts.md), [Workflows](../workflows.md), [Runtimes](../runtimes.md)
- 대체 범위: SOS 자체 등록형 Pipeline 단계 전이와 Pipeline Run 상태 머신

## 배경

최초 활동 이상 탐지를 위한 자체 `pipeline_runs.py`가 Job 제출·단계 전이·취소·복구·실행 상태를 직접 관리한다. 이 구현을 확장하면 ML 기능마다 Workflow 상태 머신과 운영·Lineage UI를 별도로 유지해야 한다. SOS는 독립 ML 기능을 연결하는 공통 플랫폼이며, 특정 Pipeline 구현을 Core에 고정하지 않는다.

## 결정

1. **단일 Orchestrator:** 등록형 데이터·ML Workflow 정의, 단계 의존성, Workflow Run 상태와 운영 이벤트의 정본은 자체 호스팅 **Dagster OSS**다. 기존 SOS 자체 Pipeline 조정기를 제거하며 병행 실행 경로, Fallback 및 동일 상태의 복제 관리 계층을 두지 않는다.
2. **선택적 Workflow:** 독립적인 SOS Data·ML Job, Playground, Scoring, 평가와 외부 API는 Dagster를 통과하지 않아도 된다. 사용자 경험과 모델·데이터 의미는 Dagster의 내부 타입에 종속되지 않는다.
3. **SOS 도메인 정본:** Dataset 판본·Actor 접근권한·Job/Attempt·신뢰된 아티팩트·Scoring·Policy·Evaluation의 완료 및 무결성은 SOS가 소유한다. Dagster Step 성공은 해당 SOS 결과가 확정됐음을 확인한 뒤 기록한다.
4. **MLflow 정본:** Experiment·Tracking Run·Registered Model·Model Version·Alias는 [ADR 0015](0015-mlflow-management-ownership.md)대로 MLflow가 관리한다. 학습 Job 성공과 Registry 등록 성공은 다르며 후속 Scoring은 고정 버전 확인 전 진행하지 않는다.
5. **제출·권한 경계:** SOS API가 Actor, Dataset Revision, 요청 지문과 Idempotency-Key를 검증한다. SOS는 Dagster Run ID와 연결할 최소 제출 식별만 보관하며 Stage 위치·Workflow 성공/실패를 다시 판정하는 상태 머신을 만들지 않는다. 네트워크 실패 후 재요청 시 Dagster Run 태그와 SOS 제출 기록을 대조한다.
6. **취소와 재시도:** Dagster Run 취소 요청과 실제 SOS Attempt 중단은 다르다. 실행 중 SOS Job에도 취소를 전달한다. Dagster Step 재시도는 같은 SOS Job을 중복 생성하지 않으며 기존 Job/Attempt의 안전성과 Iceberg 외부 커밋 경계를 보존한다.
7. **Asset 경계:** 논리적으로 안정적인 생산물의 의존 관계는 Dagster Asset으로 관찰한다. 사용자별 임시 실행, SOS 불변 Revision ID를 무조건 새 Asset Key로 생성하지 않는다. 타 시스템 소유 자산은 외부 참조와 실제 게시 근거를 함께 기록한다.
8. **운영 보안:** Dagster OSS UI·GraphQL은 운영자에게만 내부 접근으로 노출한다. 일반 사용자용 실행·조회·권한 검사는 SOS API를 경유한다. Dagster 메타데이터는 SOS DB와 별도 데이터베이스를 사용한다.
9. **배포 경계:** Dagster 실행 서비스를 독립 이미지/구성으로 관리한다. Dagster 패키지가 독립 SOS API/Worker의 필수 Python 의존성이 되지 않게 한다.
10. **전환 원칙:** 동일 PR의 완료 상태에서 레거시 Pipeline 실행기, 테이블, API 타입, 미사용 UI/검증 참조를 제거한다. 이미 적용된 Alembic 이력은 유지하고 새로운 migration으로 운영 테이블을 정리한다. 유효한 Dataset·Model·Scoring 결과는 삭제하지 않는다.

## 근거

SOS의 기술 독립성, API-first, 자산 추적, 사용자 중심 Model Experience와 직접 실행 능력을 유지하면서 일반적인 Workflow 실행·재처리·Lineage 역량을 오픈소스에 위임한다. 특정 외부 오케스트레이터가 데이터·모델의 판본과 접근권한까지 소유하는 이중 정본 구조를 피한다.

## 영향과 검증

Dagster Webserver·Daemon·Code Location, Dagster 운영 DB가 새 배포 의존성이 된다. Dagster 장애 시 등록형 Workflow의 신규 실행·조회가 제한될 수 있지만 독립 SOS Job의 실행·최종 결과는 유지된다. 실제 한 번의 전체 활동 이상 탐지, 제출 응답 유실 재시도, 단계 실패·취소·Registry 등록 대기, 재시작 복구, 참조·권한, 웹 결과 확인으로 전환 계약을 확인한다. 미실행 검증은 PR에 명시한다.
