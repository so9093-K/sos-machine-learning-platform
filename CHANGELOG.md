# Changelog

첫 릴리스 전이다. `Unreleased`는 PR별 경과가 아니라 첫 릴리스에 포함될 사용자·외부 시스템 관점의 기능을 영역별로 요약하며, 변경 시 해당 항목을 갱신하거나 제거한다. 세부 경과는 PR과 Git 이력에 남긴다.

## Unreleased

### 데이터

- CSV(UTF-8·쉼표·첫 행 헤더)·Parquet·헤더 유무를 지정한 탭 구분 TSV/TXT를 등록하고 원본 SHA-256과 해석 설정을 보존한다. 형식이 불명확한 파일은 이유와 함께 거절한다.
- Iceberg 테이블 적재와 고정 Snapshot 참조, Dataset별 Snapshot Tag 보존, Catalog Table UUID·토큰에 고정한 테이블 쓰기 소유권을 제공한다.
- Data Explorer에서 Dataset 개요·Understanding Profile(역할·탐색 후보)·컬럼 투영·필터·정렬 조회·DuckDB 탐색을 제공하고, 기본 분석과 전체 집계는 명시적 Job으로 실행한다. 결과마다 분석 범위·실제 분석 행 수·근사 여부를 구별한다.
- Data Preparation에서 Polars Feature 변환(그룹별 Lag·Rolling 등), 불투명 UUIDv4 관측치 ID 발급, Iceberg 적재를 독립 Job으로 실행한다.
- 합성 예제는 사용자가 선택할 때만 일반 Dataset으로 등록한다.

### 모델 활용

- 개발·배포 과정이 공급한 모델을 제공 설명·입력 조건·생성 근거·평가 근거와 함께 탐색하고, 하나의 적용 화면에서 자신의 Dataset이나 직접 입력에 적용한다.
- Scoring은 접수 전에 Dataset의 Feature 이름·자료형을 모델과 대조해 불일치 컬럼을 알린다. 학습 시 고정한 시간 경계의 Calibration/Test 구간 선택, 원본 관측 시각·관측치 ID 보존을 지원한다.
- 결과에서 실제 점수 분포·점수순 관측값·Train 참조 중앙값 비교를 제공하고, Permutation SHAP과 Feature 치환 민감도를 독립 설명 Job으로 계산한다.
- 단일 입력 Playground는 Dataset 등록 없이 입력과 점수를 Job으로 보존하며, Dataset 관측값을 초깃값으로 가져와 다시 실험할 수 있다.
- 판정 정책은 보정 결과와 별도로 판본 관리해 호환되는 다른 Scoring Result에 적용하고, 평가는 레이블 유무에 따라 지표를 구별한다.
- 분석 주제 '활동 이상 탐지'는 44개 활동 Feature 계약과 결과 해석을 안내하고 계약이 맞는 제공 모델로 연결한다.

### 모델 공급과 운영

- 학습·모델 관리·Feature 정의 변경은 `kind: service` 전용 `automation` 역할과 관리자만 할 수 있다. `scripts/supply_model.py`는 상태 파일로 중단 후 재개하며 학습 후 설명과 함께 모델을 제공한다.
- 모델 제공·설명 수정·중단을 명시적으로 관리하며, 제공 중단은 새 적용만 막고 기존 결과를 유지한다. 제공 중인 판본은 중단 후 삭제할 수 있다.
- Isolation Forest 학습은 전체·명시적 표본 입력, UTC 시간 분할, Train 중앙값 결측 정책을 지원하고 불변 Model Revision을 남긴다.
- MLflow가 Experiment·Run·Registered Model/Version의 관리 정본이다. SOS는 Outbox로 기록·등록을 재시도하며, Registry 등록이 확인된 모델만 Scoring에 사용한다. 모델 그룹·보관과 영향 확인 후 영구 삭제를 지원한다.
- 등록형 활동 이상 탐지 Pipeline은 Dagster OSS로 실행하고, 학습 권한 계정이 SOS 웹에서 단계별 결과를 조사한다. 각 단계 Job은 신뢰된 Workflow 출처를 기록한다.
- Feast가 등록 Feature 정의·Feature Service·시점 조회의 정본이다. 등록·조회·폐기는 별개 Job이며 로컬 운영자는 읽기 전용 Feast UI로 Registry를 탐색한다.

### 플랫폼

- Token 인증과 역할(`viewer`·`researcher`·`operator`·`administrator`·`automation`), Dataset·Job·모델 소유권, PostgreSQL 감사 이벤트를 적용한다.
- Job·Attempt는 멱등성 키, 취소, Worker 복구, 자원 수용 판단과 선택적 cgroup v2 메모리 격리, Attempt별 결과 게시 무결성을 따른다.
- API 오류는 코드·재시도 정책·요청 ID를 포함한 구조화 응답으로 제공하며 웹 타입은 생성 OpenAPI를 사용한다.
- 저장소 보존 상태 진단과 감사 조회를 운영 권한에 제공하며 객체를 자동 삭제하지 않는다.
- `make up`으로 API·Worker·Web·Dagster·MLflow·Feast UI를 Docker Compose로 실행하고, Scalar API Reference를 외부 CDN 없이 제공한다. 설치별 제품 브랜드명과 인스턴스 표시명을 관리자 권한으로 설정한다.
