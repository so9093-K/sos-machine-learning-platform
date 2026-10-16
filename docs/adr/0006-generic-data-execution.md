# 0006 — 범용 Dataset 조회·Lazy Preview와 Iceberg 배치 적재

- 상태: Accepted
- 범위: CSV·Parquet·고정 Iceberg Snapshot의 Dataset 조회, Feature Preview, 데이터 적재

## 배경

기존 Feature Preview는 10만 행, Iceberg 적재는 5만 행을 사용자 작업 전체의 허용 범위로 제한했다. 이는 라이브러리 한계가 아니라 동기 계산과 전체 메모리 물질화를 선택한 구현의 안전 한도였다. 일반 데이터의 데이터 크기에 따라 분석 기능 자체를 거부하지 않아야 한다.

## 결정

1. Dataset 조회 계약은 사용자가 지정하는 투영·타입이 검증된 비교·정렬·결과 상한으로 표현한다. 컬럼 식별자는 실제 스키마에서 확인하고 SQL 값은 바인딩한다. 외부 SQL·원본 경로를 받지 않는다. 기존 GET Preview는 후방 호환을 유지한다.
2. Row-local Feature 변환은 Lazy 실행 결과에 제한을 적용하고 입력 전체 행수로 거부하지 않는다. 그룹 Lag·Rolling은 시간 키 검증·전역 정렬을 필요로 하므로 미리보기 입력을 임의로 잘라 전체 변환 결과와 동일하다고 표현하지 않는다. 동기 예산 초과 시에는 원래 Transform Job 경로를 사용한다.
3. Iceberg 비파티션 적재는 재읽기 가능한 임시 Parquet으로 먼저 스테이징한 후 Arrow RecordBatchReader로 읽고 PyIceberg가 한 번의 Snapshot 커밋으로 게시한다. 외부 커밋 단계의 실패·중단은 기존 Attempt와 `sos.attempt-id` 기준으로 보수적으로 판정한다. 단계별 부분 Append를 허용하지 않는다.
4. PyIceberg의 RecordBatchReader 파티션 테이블 쓰기 미지원 범위에는 기존 작은 입력 호환 경로를 유지하고, 확장되기 전까지 명시적인 기술적 제약으로 기록한다.
5. Dataset 판본·원본 객체의 공유 보존·Snapshot Tag·Actor 권한·Job 멱등성 의미는 기존 ADR을 그대로 따른다.
6. 메모리·임시 디스크·CPU·실행 시간은 별도의 자원 예산으로 제한한다. Lazy/Streaming 사용만으로 전체 OS 자원 격리가 보장되었다고 주장하지 않는다.

## 근거와 영향

DuckDB의 Parquet Pushdown과 Polars의 Lazy·Streaming, PyIceberg 0.12의 RecordBatchReader 단일 Snapshot 쓰기를 사용한다. Preview 반환량과 원본 계산량, Job 접수와 외부 커밋을 분리한다. 검증에는 실제 PostgreSQL·S3·Iceberg와 브라우저를 포함하고, 실데이터 대신 결정적 합성 입력으로 재현한다.

참조: [0004](0004-duckdb-discovery.md), [0005](0005-job-submission-identity.md), [Contracts](../contracts.md), [Experience](../experience.md), [Runtimes](../runtimes.md).
