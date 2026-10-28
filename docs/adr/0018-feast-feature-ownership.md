# 0018 — Feast Feature 관리·조회 정본 전환

- 상태: **Accepted**
- 관련: [Architecture](../architecture.md), [Contracts](../contracts.md), [ADR 0004](0004-duckdb-discovery.md), [ADR 0015](0015-mlflow-management-ownership.md), [ADR 0016](0016-dagster-orchestration.md)
- 범위: 등록 Feature의 Entity·Feature View·Feature Service·Historical/Online Retrieval 소유권

## 배경

SOS는 Dataset의 불변 판본, Polars 기반 일반 변환, 모델별 입력 Feature 서명과
ML Job을 관리하지만 독립적인 Feature Registry·Historical Retrieval의 정본은 없다.
이를 SOS에 새로 구현하면 Feature 정의·시점 정합성·재사용·제공 방법까지
별도의 Feature Store를 유지하게 된다. MLflow·Dagster의 기존 정본 분리와 일관되게
전문 책임은 Feast에 위임한다.

## 결정

1. **Feature 관리 정본:** 등록된 Entity, Feature View, Feature Service와
   등록 Feature의 Historical/Online Retrieval은 Feast가 소유한다.
   SOS에 평행한 Feature Registry·PIT Join 구현·Fallback Retrieval을 만들지 않는다.
2. **SOS 정본:** Actor 권한, 원본 Dataset과 Iceberg Snapshot, Job/Attempt,
   불변 Retrieval Result Dataset, Model Revision, 감사·결과 게시·보존은 SOS가 소유한다.
   Feast Registry의 현재 상태가 과거 Dataset/Model 근거를 소급 변경하지 못한다.
3. **독립 기능:** 일반 Data Explorer, Polars Data Preparation, 직접 Feature 벡터를
   이용한 학습·Scoring·Playground는 Feature Store가 아닌 독립 기능으로 유지한다.
   등록 Feature를 요청하는 경우에만 Feast를 정본으로 사용한다.
4. **계산과 조회 구분:** Lag·Rolling·집계 등 원본으로부터 Feature를 계산하는
   Runtime과 Feature 정의·조회는 별개의 책임이다. Dagster는 Workflow,
   MLflow는 모델·실험 정본을 계속 소유한다.
5. **Entity·시간 계약:** Entity의 지속적 대상 식별자는 특정 Dataset Revision
   안에서만 안정적인 `sos_observation_id`와 다르다. UTC Event Timestamp,
   모델의 Prediction Timestamp, Data Availability Timestamp를 혼동하지 않는다.
   Point-in-Time Join은 올바른 upstream 계산·데이터 도착 의미 없이 누수를 모두 막지 못한다.
6. **명시적 판본:** Feature View·Service 정의, Feature 입력 Dataset 판본,
   Entity 데이터 판본, TTL, Schema, 실제 Retrieval 출력과 실행 조건을 고정한다.
   Feature Registry 정의 버전만으로 과거 데이터의 물리적 보존을 가정하지 않는다.
7. **접근:** Feast의 관리 자격 증명·Registry·스토리지에 클라이언트를
   직접 연결하지 않는다. SOS API가 Actor·Dataset 접근을 검사하며,
   Worker는 신뢰된 Job 명세로만 Feast를 호출한다.
8. **Runtime 독립:** Feast SDK는 Feature 등록·조회 경계에서만 호출한다.
   독립 SOS API·Data Explorer·기존 ML 계산을 Feast Registry 가용성에 묶지
   않으며 일반 Worker의 시작도 Registry Health에 조건부로 종속시키지 않는다.
9. **Feast 외부 커밋·폐기:** 등록 정의의 이름을 요청과 Source 판본으로 결정하고,
   Entity·View·Service의 의미적 지문을 Registry의 실제 상태와 비교한다.
   같은 정의의 재시도는 기존 등록을 사용하고, 내용이 다른 충돌은 덮어쓰지 않는다.
   Worker 중단 뒤 부분 등록은 확인·재조정한다. 신규 Retrieval Job·폐기 Job은
   동일 등록 Job 행 잠금 아래에서 접수하고 폐기 접수 즉시 신규 조회를 차단한다.
   폐기 이전에 승인된 Queued Retrieval은 참조 보호 대상이며 정상 완료를 허용한다.
   실패·취소·성공 등록의 폐기는 별도
   SOS Job으로 요청한다. 다른 활성 등록이나 과거/진행 중 Retrieval이 참조하는
   정의는 Registry에 유지하되 해당 등록의 신규 조회는 막는다.
   참조가 없는 경우에만 정의와 연결된 Registry 객체를 실제 삭제한다.
   부분 폐기 후 재실행은 남은 객체만 확인하며 불일치하는 정의는 삭제하지 않는다.
10. **조회 Runtime 경계:** 플랫폼의 등록·조회·불변 Dataset 계약은 Feast의
    현행 DuckDB/Ibis Backend, DataFrame 물질화나 특정 파일 형식과 동일하지 않다.
    현재 실행 정책 및 Offline Store별 조회 구현은 별도 Runtime 경계에서 관리한다.
11. **전환 원칙:** 도입 범위 안에서 대체된 API·실행 경로·자체 Registry·타입·설정·
   문서·검증 참조를 같은 PR에서 제거한다. 미완료 기능을 완료로 설명하지 않는다.
   과거 Alembic migration은 변경하지 않으며 유효한 Dataset·Model·Scoring 이력은 유지한다.

## 구현 경계와 제한

- Feature 등록 Job은 SOS가 게시한 **Parquet Feature Dataset**에 고정된
  Entity·Feature View·Feature Service 정의를 Feast Registry에 등록하고, Job 결과에는
  등록 이름 및 원본 판본만 남긴다. 조회 Job은 성공한 등록 Job을 명시적으로 참조하며
  이미 등록된 Service를 재사용하고 Registry를 수정하지 않는다.
  별도 Entity 시점 Dataset과 Point-in-Time Join한 결과는 기존 SOS Worker의
  Attempt별 불변 Parquet Dataset으로 게시한다. Data Preparation과 독립 ML 학습은 유지한다.
- 기존 Iceberg SQL Catalog가 Feast Iceberg REST Catalog와 동일하지 않으므로
  직접 Iceberg FeatureSource로 간주하지 않는다. Iceberg Snapshot을 명시적으로
  고정해 게시한 Parquet를 사용하거나 검증된 별도 지원 경로를 선택한다.
- Online Serving/Online Materialization이 아직 구현되지 않았다는 사실을 유지한다.
  등록 폐기·Registry 객체 삭제·원본 Dataset/모델 결과의 보존은 서로 다른 수명주기다.
  조회 결과·Model Revision은 Source·Feature 정의 지문과 실제 입력 근거를 보존한다.
  자동 물리 삭제는 하지 않고 성공한 폐기 Job에서 참조를 확인한 경우에만 정리한다.
- 등록 Feature는 SOS 내부 Actor 검증을 통과해야 하며, Feast의 내부 관리 UI를
  일반 사용자에게 직접 노출하지 않는다.

## 검증과 영향

하나의 실제 사용자 경로에서 Source/Entity Dataset 접근 → Feast 정의 등록 →
역사적 시점 조회 → SOS 불변 Dataset 게시 → 기존 Model 학습/Scoring을 확인한다.
미래 정보 사용, 다른 Actor의 원본 참조, 시간·자료형 불일치, 실패·취소와 결과
게시 경계는 집중적으로 검증한다. 대형 전용 검증 체계를 새로 만들지 않는다.
