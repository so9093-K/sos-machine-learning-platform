# 0004 — DuckDB Discovery와 Polars Feature 실행 책임

- 상태: Accepted
- 범위: CSV·Parquet·Iceberg의 Preview, EDA, Profile, 탐색 집계

## 배경

데이터 발견·분포 이해와 Feature 변환을 하나의 Polars 처리 모듈에서 수행하면 실행 비용·정확도·자원 정책을 개별적으로 정의하기 어렵다. 처음 보는 데이터의 컬럼·집계를 UI와 외부 API가 공유할 수 있는 경계가 필요하다.

## 결정

1. DuckDB는 Preview·EDA·Profile·Discovery 집계의 **유일한 계산 구현**이다. Polars는 Feature Preview·Transform과 기존 Iceberg 적재 입력 변환을 담당한다.
2. Dataset·Job·Attempt의 정본은 PostgreSQL, 원본·결과 파일은 S3, Iceberg Snapshot·보존 참조는 Catalog에 둔다. DuckDB의 메모리 DB를 영속 정본으로 삼지 않는다.
3. 빠른 분석은 명시된 선두 샘플에서 실행한다. 전체 집계와 전체 Profile은 기존 Worker·Attempt 수명주기에서 실행하고, 결과에서 샘플 범위와 Dataset 판본을 구분한다.
4. 사용자 SQL·서버 파일 경로를 입력으로 받지 않는다. Dataset 소유권과 컬럼·집계 유효성을 확인하며 연결별 메모리·스레드·Spill 제한과 DuckDB 파일 접근·확장 로딩 제한을 적용한다. 엔진 메모리 설정만으로 OS 수준 격리를 보장하지는 않는다.
5. Iceberg는 지정한 Snapshot을 PyIceberg가 해석한 Arrow 배치로 DuckDB에 공급한다. 기존 Snapshot 쓰기·보존·불확실 커밋 처리는 변경하지 않는다.
6. 기존 Job 응답 열거형을 확장하지 않고 **전용 Discovery Job API**를 제공해 API 호환성을 지킨다. 저장·실행 및 취소는 기존 Job 인프라를 공유한다.
7. Preview에서는 비유한 실수를 명시적인 문자열로 표시해 실제 NULL과 구분한다. 계산 불능 숫자 집계는 유효한 JSON NULL로 반환하고, 샘플 집계와 전체 Worker 집계에서 동일한 계약을 사용한다.

## 근거와 영향

일반적인 스키마·분포·집계 탐색을 코드 수정 없이 구조화된 분석 계약으로 수행하면서 Feature 계산의 의미를 유지한다. 기존 Catalog와 객체 보존 계약은 그대로 사용한다.

실행 범위별 정확도, NULL·NaN·타입, Arrow/Iceberg Snapshot, 권한, API 하위 호환성, 자원 경계와 브라우저 경험은 통합 테스트에서 검증한다. 실제 대규모 처리 성능과 운영 환경의 RSS·임시 디스크 계측은 별도의 환경별 검증 대상이다.

참조: [Architecture](../architecture.md), [Contracts](../contracts.md), [Experience](../experience.md), [Runtimes](../runtimes.md).
