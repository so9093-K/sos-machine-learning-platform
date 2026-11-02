# Changelog

첫 릴리스 전이다. `Unreleased`는 PR별 경과가 아니라 첫 릴리스에 포함될 사용자·외부 시스템 관점의 기능을 영역별로 요약하며, 변경 시 해당 항목을 갱신하거나 제거한다. 세부 경과는 PR과 Git 이력에 남긴다.

## Unreleased

### 데이터

- CSV(UTF-8·쉼표·첫 행 헤더)·Parquet·헤더 유무를 지정한 탭 구분 TSV/TXT를 등록하고 원본 SHA-256과 해석 설정을 보존한다. 형식이 불명확한 파일은 이유와 함께 거절한다.
- Iceberg 테이블 적재와 고정 Snapshot 참조, Dataset별 Snapshot Tag 보존, Catalog Table UUID·토큰에 고정한 테이블 쓰기 소유권을 제공한다. 파티션 테이블도 행 수 제한 없이 배치로 적재해 한 Snapshot으로 커밋한다.
- Data Explorer에서 Dataset 개요·Understanding Profile(역할·탐색 후보)·컬럼 투영·필터·정렬 조회·DuckDB 탐색을 제공하고, 기본 분석과 전체 집계는 명시적 Job으로 실행한다. 결과마다 분석 범위·실제 분석 행 수·근사 여부를 구별한다.
- Data Preparation에서 Polars Feature 변환(그룹별 Lag·Rolling 등), 불투명 UUIDv4 관측치 ID 발급, Iceberg 적재를 독립 Job으로 실행한다. Lag·Rolling 미리보기는 입력 행 수와 관계없이 전체 변환과 같은 결과 앞부분을 보이며, 한 요청의 계산 시간 한도를 넘으면 변환 Job으로 안내한다.

### 모델 활용

- 개발·배포 과정이 공급한 모델을 제공 설명·입력 조건·생성 근거·평가 근거와 함께 탐색하고, 하나의 적용 화면에서 자신의 Dataset이나 직접 입력에 적용한다.
- Scoring은 접수 전에 Dataset의 Feature 이름·자료형을 모델과 대조해 불일치 컬럼을 알린다. 학습 시 고정한 시간 경계의 Calibration/Test 구간 선택, 원본 관측 시각·관측치 ID 보존을 지원한다.
- 결과에서 실제 점수 분포·점수순 관측값·입력 프로필을 제공한다. 새 학습 판본은 실제 적합 입력의 Feature 참고 분포를 전체 또는 비용이 제한된 표본으로 보존하고 분석 행 수를 표시하며, 기존 판본은 보존된 중앙값만 비교한다. 분포의 P50과 입력 복원의 학습 기준 중앙값을 구별한다. 학습 Dataset·Job·Attempt·저장 위치·해시·매개변수와 학습 규모·내부 선택·결측 정책·학습 점수 집계는 모델 소유자·관리자만 조회한다. 모델 점수 기여는 선택적으로 Permutation SHAP으로 확인한다.
- 값 직접 입력은 소수 활동 특징의 시각적 비교·수정과 명시적 재계산을 중심으로 입력·점수를 Job에 보존한다. 전체 입력은 상세에서 편집하며 수정한 활동은 프로필에서 확인한다. 학습 P10·P90 수치와 구간 밖 차이를 표시하고, Dataset 관측값을 가져와 원래 점수와 비교하거나 복원할 수 있다. 제공 모델은 다음 페이지까지 탐색한다.
- 판정 정책은 보정 결과와 별도로 판본 관리해 호환되는 다른 Scoring Result에 적용하고, 평가는 레이블 유무에 따라 지표를 구별한다.
- 비지도 학습 → 이상 탐지 → 활동 이상 탐지로 이어지는 소개 화면과 주제 Playground에서 활용 방법을 보고, 같은 화면에서 자신의 Dataset·직접 입력에 모델을 적용해 결과 해석과 API 연동 방법까지 확인한다. Dataset Scoring 명령은 새 요청 키를 생성하고 재시도에는 같은 키를 사용하도록 안내한다.
- 분석 주제는 입력 Feature와 출력 계약으로 정의되며(`GET /api/topics`), 알고리즘이나 모델 출처와 관계없이 계약이 맞는 모델을 적용·호출한다.
- 서빙 중인 모델은 `POST /api/serving/endpoints/{id}/predict`로 관측값을 바로 점수화한다. Playground는 온라인 API 사용 가능 여부를 보이고, 계산한 입력을 그대로 보내 같은 점수를 확인한다.

### 모델 공급과 운영

- 모델 생산(학습·평가·MLflow 등록)은 Dagster의 생산 Pipeline이 SOS DB 접근 없이 직접 실행하고, SOS는 MLflow 고정 모델 버전을 다시 학습하지 않고 Model Revision으로 연결한다(`/api/ml/models/imports` 제출·조회·취소). 연결은 모델 패키지(`sos_model.json`·`estimator.skops`)의 허용 타입 복원과 검증 점수 재현을 확인한 뒤 게시하며, Model Revision은 출처(`source_kind`·`source`)를 가진다. SOS Pipeline 제출 API·화면과 Workflow 제출 기록은 제거했다.
- 학습·모델 관리·Feature 정의 변경은 `kind: service` 전용 `automation` 역할과 관리자만 할 수 있다. `scripts/supply_model.py`는 MLflow 고정 버전 연결 또는 실제 Dataset 학습으로 판본 게시·제공·선택적 온라인 서빙을 연결하고 상태 파일로 중단 후 재개한다. 모델 적용은 Dataset 선택·등록을 기본으로 하고 값 직접 입력을 선택적으로 제공한다.
- 모델 제공·설명 수정·중단을 명시적으로 관리하며, 제공 중단은 새 적용만 막고 기존 결과를 유지한다. 제공 중인 판본은 중단 후 삭제할 수 있다.
- Isolation Forest 학습은 전체·명시적 표본 입력, UTC 시간 분할, Train 중앙값 결측 정책을 지원하고 불변 Model Revision을 남긴다.
- MLflow가 Experiment·Run과 생산 Pipeline의 원본 Registry 정본이다. SOS는 Outbox로 기록과 SOS 판본 등록을 재시도하되, 판본의 사용 여부(수명주기·보관)와 모델 그룹은 SOS가 정하므로 MLflow 장애가 Scoring·Playground·추론·서빙·제공을 막지 않는다. 판본·그룹 보관과 영향 확인 후 영구 삭제를 지원한다.
- Ray Serve 기반 온라인 서빙 Endpoint를 제공한다. 모델 상세에서 권한 있는 사용자가 최초 배포를 명시적으로 요청하거나 공급 자동화가 배포·판본 전환을 요청하고, 독립 컨트롤러가 새 판본의 준비를 확인한 뒤에만 전환하며 실패 시 기존 판본을 유지한다. 서빙 Runtime 자원을 넘는 배포·전환은 접수 단계에서 이유와 함께 거절하고, 실제 Config가 의도와 다르면 되돌린다. Dashboard의 배포 관측과 실제 추론 준비를 구별하고 관리 경로 장애가 정상 추론을 차단하지 않으며, 추론 응답의 판본·설정 지문·출력 계약이 일치할 때만 전달한다. 운영 화면에서 상태 확인·되돌리기·중단·삭제를 하고, 복제본 상태·로그는 로컬 운영 도구인 Ray Dashboard로 확인한다. `/api/ready`는 API 전체 장애와 Job 실행·온라인 서빙 기능의 장애를 구별한다.
- Feast가 등록 Feature 정의·Feature Service·시점 조회의 정본이다. 등록·조회·폐기는 별개 Job이며 로컬 운영자는 읽기 전용 Feast UI로 Registry를 탐색한다. 시점 조회는 Entity 행 수 한도 없이 Entity를 배치로 나누어 조회한다(`SOS_FEAST_ENTITY_BATCH_ROWS`는 물리 배치 크기).

### 플랫폼

- Token 인증과 역할(`viewer`·`researcher`·`operator`·`administrator`·`automation`), Dataset·Job·모델 소유권, PostgreSQL 감사 이벤트를 적용한다.
- Job·Attempt는 종류와 관계없이 공통 수명주기 API(`GET /api/jobs?kind=…`·`GET /api/jobs/{job_id}`·`POST /api/jobs/{job_id}/cancel`)로 조회·취소하고, 기능별 API는 제출만 맡는다. 멱등성 키, 취소, Worker 복구, 자원 수용 판단과 선택적 cgroup v2 메모리 격리, Attempt별 결과 게시 무결성을 따른다.
- Scoring 결과의 점수순 관측값 탐색은 결과마다 한 번 만든 불변 색인 조각을 읽어, 결과 크기와 관계없이 페이지마다 같은 비용으로 조회한다. 기존 결과의 색인은 업그레이드 때 Worker Job으로 만들고, 실패하면 같은 결과로 색인 생성을 다시 요청한다(`POST /api/ml/scoring/results/{id}/observation-index`).
- 데이터 크기에 따라 처리 경로나 계산 의미를 나누지 않는다. Scoring 순위 지표, 판정 정책·평가, 학습 점수 근거, 생산 Pipeline의 학습·평가는 점수·입력을 메모리에 모으지 않고 배치 순회와 디스크 Spill 외부 정렬로 정확히 계산한다.
- API 오류는 코드·재시도 정책·요청 ID를 포함한 구조화 응답으로 제공하며 웹 타입은 생성 OpenAPI를 사용한다.
- **API 호환성 변경 — 모델 조회:** `GET /api/ml/models/{revision_id}`와 `GET /api/ml/offered-models`의 `items[].model`은 소유자·관리자가 아닌 소비자에게 `dataset_id`, `dataset_version`, `job_id`, `attempt_id`, `location`, `sha256`, `params`를 반환하지 않는다. 필드는 `null`이 아니라 응답에서 제거되며, `training_score_summary`는 빈 객체이고 Runtime도 공개 정보로 제한된다. 기존 전체 응답을 전제로 한 필수 필드 검증·직접 접근은 수정이 필요하다. 배포 전에 OpenAPI의 공개·전체 응답을 반영해 클라이언트 타입을 갱신하고, `creation_evidence_visible`로 응답을 구분해 비공개 필드 의존을 제거해야 한다. 필드 부재는 모델 오류나 기록 부재를 뜻하지 않는다. API와 SOS 웹은 함께 갱신하며 소유자·관리자의 전체 응답은 유지한다.
- **API 호환성 변경 — 모델 그룹·보관:** MLflow 기반 `/api/ml/registry/groups*`와 `/api/ml/registry/models`를 제거했다. 모델 그룹은 `/api/ml/model-groups`, 판본 보관·복원은 `PUT`·`DELETE /api/ml/models/{id}/archive`로 관리하며 모델 응답의 `archived_at`·`group_archived_at`이 보관 상태다. `registry_version`은 MLflow 등록 기록이며 사용 조건이 아니다.
- **API 호환성 변경 — Job 조회·취소:** 기능별 Job 목록·상세·취소 경로(`/api/ml/{train,scoring,playground,policies,evaluations,explanations}/jobs`의 조회·`/{job_id}`·`/cancel`, `/api/ml/models/imports/{job_id}`, `/api/explorations/jobs`의 조회·상세·취소)를 제거했다. 공통 Job API를 사용하며 제출 경로와 Job 응답 필드는 같다.
- 저장소 보존 상태 진단과 감사 조회를 운영 권한에 제공하며 객체를 자동 삭제하지 않는다.
- `make up`으로 API·Worker·Web·Dagster·MLflow·Feast UI를 Docker Compose로 실행하고, Scalar API Reference를 외부 CDN 없이 제공한다. 루트 `.env`의 실행·탐색·캐시·업로드·객체 점검 설정을 필요한 API·Worker에 전달하며 호스트 Web도 같은 파일에서 포트를 읽는다. 설치별 제품 브랜드명과 인스턴스 표시명을 관리자 권한으로 설정한다.
