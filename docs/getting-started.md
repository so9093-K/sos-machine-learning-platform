# 시작하기

SOS의 로컬 개발 환경을 실행하고 데이터 탐색·모델 활용·연구를 시작하는 안내다. 각 목적은 독립적으로 시작할 수 있으며 모든 단계를 순서대로 거칠 필요는 없다.

## 제품명과 인스턴스 표시명

관리자는 Web 사이드바에서 **제품명 설정**과 **인스턴스 표시명 설정**을 각각 변경할 수 있습니다. 제품명은 로그인 전 화면에도 표시되며, 별도로 설정하지 않은 인스턴스의 기본 제품명은 **SOS**입니다. 표시명 변경은 내부 API·Worker·데이터 식별자를 바꾸지 않습니다.

## Feast 등록 Feature 조회

Feature Service 등록과 조회는 별개의 사용자 행동이다. **연구·실험 → 새 학습**의
**등록 Feature로 학습 Dataset 구성 · Feast**를 펼쳐 기존 Service를 선택하거나 새로 등록할 수 있다. 두 API 모두 Actor·Dataset 권한과
Job/Attempt 계약을 적용한다.

1. `POST /api/features/services/jobs`: Feature Source Dataset, 지속적 Entity Key,
   원본 UTC Event Timestamp와 숫자형 Feature 목록을 제출한다. 성공한 Job 결과에
   Feast `feature_service` 이름과 고정 Source 판본을 남긴다.
2. `GET /api/features/services/registrations?limit=100&cursor=...`: 본인 등록 Job
   이력을 기존 키셋 커서 계약으로 페이지 조회한다.
   이 이력은 현재 Feast Registry의 실시간 가용성 판정을 대신하지 않는다.
3. `POST /api/features/retrieval/jobs`: 등록 성공 Job ID·조회 시점 Dataset·UTC 시간
   컬럼을 제출한다. 동일 Feature Service를 조회마다 재사용하며 새 정의를 등록하지 않는다.
4. 조회 성공 Job의 결과 `dataset_id`를 기존 SOS 모델 학습에 사용한다.
5. `POST /api/features/services/{registration_job_id}/retirement/jobs`: 성공·실패·
   취소된 등록을 명시적으로 폐기한다. `registry_deleted`는 실제 Registry 객체
   제거 여부이며 `false`인 경우 다른 활성 등록·과거 조회의 참조가 유지된 것이다.
   폐기 중이거나 폐기 완료된 등록은 새 조회에 사용할 수 없고
   과거 Dataset·모델 근거는 유지한다. 실행 중인 폐기 Job은 외부 삭제 커밋
   정합성을 위해 취소할 수 없다. 부분 실패한 폐기는 새 폐기 Job으로
   재시도하여 실제 Registry 상태를 확인해야 한다.

등록 요청:
```json
{
  "feature_dataset_id": "<published-parquet-source-id>",
  "entity_column": "host_id",
  "timestamp_column": "event_timestamp",
  "features": ["file_changes_1h", "network_connections_1h"],
  "ttl_seconds": 2592000
}
```

조회 요청:
```json
{
  "registration_job_id": "<successful-feature-register-job-id>",
  "entity_dataset_id": "<entity-events-dataset-id>",
  "timestamp_column": "prediction_time"
}
```

현재 DuckDB Runtime의 기본 Entity 조회 상한은 100,000개이며
`SOS_FEAST_MAX_ENTITY_ROWS`로 조정한다. 초과 요청은 잘라내지 않고 실패한다.
동일 정의를 재등록하면 Registry의 기존 내용을 대조하고 안전하게 재사용하며
변경·부분 소실 상태는 무조건 덮어쓰지 않는다.
Online Serving과 현재 Iceberg SQL Catalog의 직접 조회는 지원하지 않는다.

## 실행 준비

Docker Compose와 GNU Make가 필요하다. 개발용 Compose는 Token 인증이 필수이므로, 저장소 루트에서 `.env.example`을 복사한 후 관리자 토큰을 생성한다. 아래 명령에는 Python 3가 필요하다.

```bash
test -e .env || cp .env.example .env
python3 - <<'PY'
import hashlib
import json
import secrets

token = secrets.token_urlsafe(48)
fingerprint = hashlib.sha256(token.encode()).hexdigest()
print("Bearer Token (별도 안전 보관):", token)
print("SOS_AUTH_TOKEN_HASHES=" + json.dumps({
    fingerprint: {"actor_id": "local-admin", "kind": "human", "roles": ["administrator"]}
}, separators=(",", ":")))
PY
```

출력된 `SOS_AUTH_TOKEN_HASHES=...` 한 줄을 `.env`에 추가한다. **Bearer Token 원문은 `.env`나 Git에 넣지 말고 별도로 안전하게 보관**한다. 브라우저 접속 시에는 그 원문을 사용한다. 토큰 지문 매핑이 없으면 API는 기동하지 않는다.

## 플랫폼 실행과 접속

```bash
make up       # API·Worker·Web·Feast UI·Dagster·MLflow 등 개발 서비스 상시 기동 및 Ready 확인
make status   # 컨테이너 상태 확인
make doctor   # 설정·준비 상태 진단
```

- Web UI: `http://localhost:9601`
- API: `http://localhost:9602`
- API Reference: `http://localhost:9602/docs`
- Feast 공식 Feature Catalog UI: `http://localhost:9608` (Entity·Feature View·Service·Source·Lineage 탐색, 운영자 전용)
- 로컬 MLflow 운영자 관리 UI: `http://localhost:9606` (연구 Run·Registered Model/Version 확인, 사용자별 권한 격리 없음)
- 학습 완료 후 등록 대기 중에는 SOS의 MLflow Run ID·재시도 상태를 확인하고, 로컬 운영자라면 Dagster Runs와 MLflow Registry를 대조한다. Dagster Code Location `Loaded` 또는 MLflow Run `Finished`만으로 전체 Pipeline의 완료를 판단하지 않는다.
- Dagster OSS 운영자 UI: `http://localhost:9607` (localhost 전용, 사용자 인증 기능 없음)
- Dagster에서 등록형 활동 이상 탐지 실행을 찾을 때는 Code Location `sos`, Job `activity_anomaly_workflow`를 확인하세요.
  운영 권한이 있으면 SOS 분석 화면의 기술 근거에서 해당 분석의 Dagster Run(태그 `sos/submission_id` = SOS 분석 ID)으로 바로 이동할 수 있습니다.
  Dagster의 Re-execute는 같은 SOS 단계 Job을 재사용하므로 재시도가 아닙니다. 다시 실행하려면 SOS에서 새 분석을 시작합니다.
- MLflow에서 한 분석의 Run은 이름 `sos-workflow-<분석 ID 앞 8자>-<Dagster 단계명>`과 태그 `sos.workflow_submission_id`로 묶입니다.
  모델 버전의 `sos.job_id`·`sos.sha256` 태그는 SOS의 무결성 대조 값이므로 MLflow UI에서 수정하거나 삭제하지 않습니다.
- Dagster는 SOS 사용자용 학습 화면이 아니라 **로컬 운영자의 Workflow Run·단계·이벤트 확인 도구**입니다.
  실행과 결과는 SOS 웹에서 확인하며, Dagster 접속은 같은 PC의 브라우저에서만 가능합니다.
  API 컨테이너의 내부 GraphQL 주소는 `http://dagster-webserver:3000/graphql`입니다.
- Feast UI는 SOS의 `sos_features` SQL Registry를 **읽기 전용 DB 계정**으로 탐색합니다.
  `make up` 때 기존 Registry를 준비하고 읽기 권한을 설정합니다. 처음 기동할 때 등록된 Feature가 없으면 빈 Catalog가 정상입니다.
  SOS에서 Feature Service 등록 Job이 완료된 후 페이지를 다시 열면 해당 Entity·View·Service와 연결 Source를 탐색할 수 있습니다.
  UI에는 SOS Actor 권한이 적용되지 않으므로 localhost 외부로 공개하거나 일반 사용자에게 전달하지 마세요.
  Feast UI가 Registry 변경 기능을 표시하더라도 읽기 전용 DB 권한 때문에 변경할 수 없습니다.
  UI가 표시하는 Feast 관계는 SOS의 조회 Job·모델 계보를 대신하지 않습니다.
  UI 포트와 DB 계정 암호는 각각 `SOS_FEAST_UI_PORT`, `SOS_FEAST_UI_DB_PASSWORD`로 조정할 수 있습니다.
- `make doctor`는 Dagster Run 검색 계약까지 검사하지 않습니다.
  별도의 읽기 전용 `make doctor-dagster`는 SOS가 실제 사용하는 제출 태그 Run 검색을 확인합니다.
  정상적인 미존재 제출 ID는 `None`을 출력합니다.

API Reference의 Scalar JavaScript는 Docker 이미지 빌드에 함께 포함되므로 실행 시 외부 CDN을 사용하지 않는다.
호스트에서 Python API를 직접 실행할 경우 Node.js 24와 npm으로
`bash scripts/build_scalar_assets.sh`를 먼저 실행한다. 이 명령은 잠금 파일로
공식 Scalar 번들을 설치하고 고정 SHA-256을 확인한 뒤 Python 패키지 내부에 복사한다.
번들을 갱신할 때에는 의존성과 해시를 함께 검증해야 한다.

Web UI의 인증 화면에 앞에서 보관한 Bearer Token을 입력한다. 토큰은 브라우저 메모리에만 보관되므로 다시 접속하면 재인증이 필요하다. 접속 주소와 실행 구성은 [Compose](../compose.yaml) 및 [Makefile](../Makefile)을 따른다.

## 첫 Dataset 탐색

1. 홈의 **데이터 탐색 시작** 또는 왼쪽 **데이터 탐색**을 연다. **예제 데이터**에서 합성 데이터를 선택하거나 **파일 가져오기**·**파일 선택**으로 CSV·Parquet·TSV/TXT를 등록한다. CSV는 UTF-8 인코딩·쉼표 구분·첫 행 헤더 형식이어야 하며, Excel에서는 'CSV UTF-8(쉼표로 분리)'로 저장한다. TSV/TXT는 자유 서술 텍스트가 아닌 UTF-8 탭 구분 데이터이며, 등록 시 헤더 유무를 지정한다. 예제 목록을 여는 것만으로는 등록되지 않으며, **이 예제로 탐색 시작**을 선택하면 일반 Dataset으로 등록된다.
2. 등록된 Dataset을 선택하고 **데이터 탐색**에서 구조와 판본을 확인한다. 필요하면 **기본 분석 실행**을 명시적으로 요청하거나 **데이터 조회**·**탐색**에서 일부 데이터를 살펴본다. 업로드만으로 분석 Job이 자동 실행되지는 않는다.
3. 전체 범위의 집계나 데이터 가공이 필요하면 해당 Job 또는 **데이터 준비**를 별도로 실행한다. Data Explorer에서 탐색만 하고 끝내도 된다.

미리보기와 부분 데이터 분석은 전체 Dataset의 분포를 보장하지 않는다. 파일 등록 성공은 모든 행의 품질 검증 완료를 뜻하지 않으며, 이후 조회·분석·Job에서 오류가 발견될 수 있다. 입력 형식·계산 범위와 결과의 의미는 [Contracts](contracts.md)와 화면·API의 현재 안내를 따른다.

## 기존 모델 활용

1. 홈의 **모델 선택** 또는 왼쪽 **모델 활용**을 열어 모델 그룹과 판본을 선택한다. **모델 상세**에서 학습 데이터와 입력 조건을 확인하고 **데이터에 적용**으로 이동한다. 새 학습은 필요하지 않다.
2. **Dataset 분석**에서 사용할 Dataset과 입력 조건을 확인한 뒤 **이 Dataset 분석 시작**을 선택한다. 지원 모델에서는 **값 직접 입력**으로 별도 소량 점수 계산을 할 수도 있다. 화면 이동이나 파일 등록만으로 점수 계산을 실행하지 않는다.
3. 완료된 점수 분포와 점수순 관측값을 확인하고, 필요하면 관측값의 입력·설명을 살펴본다. 이전 실행은 **작업 이력 → ML 실행 작업**에서 찾는다.

## 연구·실험과 새 학습

1. 홈 또는 왼쪽 **연구·실험**을 연다. **연구 묶음**에서 Experiment를 선택하고 같은 종류의 Run을 최대 두 개 비교한다. 기존 연구를 이어가려면 해당 묶음을 선택한 뒤 **새 학습 준비 → 새 학습**으로 이동한다. 선택한 연구 이름이 학습 화면의 **연구 Experiment** 초기값에 들어가며 수정할 수도 있다. 선택 없이 시작하면 **기본 실험**을 사용한다.
2. 학습 화면에서 등록된 Dataset, 학습할 숫자형 Feature, 등록할 모델 그룹을 선택한다. 기존 `/training` 주소로 직접 시작할 수도 있고, Data Explorer의 분석 Job을 먼저 실행할 필요는 없다.
3. 학습 입력을 전체 Dataset(`all`, 기본값) 또는 행 수와 Seed를 지정한 부분집합(`sample`)으로 선택하고 Isolation Forest 설정을 확인한다. 입력 부분집합의 행 수와 모델의 트리별 표본 수는 서로 다른 설정이다.
4. **학습 Job 제출** 후 **학습 Job 상태**에서 실제 상태와 오류를 확인한다. 이전 실행은 **작업 이력 → ML 실행 작업**에서 확인한다. 성공하면 **불변 모델 근거 보기**에서 Dataset 판본·입력 범위·학습 점수 요약을 확인하고, Registry에 등록된 모델을 활용할 수 있다. 학습 성공과 Registry 등록 완료는 구별한다.

학습 입력은 고정된 50,000행·32 Feature 한도로 제한하지 않지만, 요청한 데이터와 실행 환경의 자원 예산이 맞지 않으면 **입력을 조용히 줄이지 않고 실패**한다. 정확한 입력 조건과 설정 범위는 학습 화면 및 [API Reference](http://localhost:9602/docs)가 정본이다.

학습 점수는 독립 검증 성능이 아니다. 필요하면 Scoring·판정 정책·평가·설명을 별도로 실행한다. 지원되는 Permutation SHAP은 기준 점수에 대한 Feature 기여도를, 기준값 치환은 입력 하나를 바꾼 점수 반응을 나타낸다. 둘 다 사건 원인이나 인과효과를 뜻하지 않는다. 온라인 Serving·프로덕션 배포는 아직 제공하지 않는다.

## 활동 이상 탐지와 Workflow

1. 왼쪽 **분석 주제 → 활동 이상 탐지**를 열면 모델 적용 방법을 살펴볼 수 있다. **모델 적용**은 호환되는 기존 모델을 사용하는 활동 이상 탐지 전용 경로다.
2. 새 시간 분할 학습·기준 설정·Test 평가를 함께 실행하려면 **연구·실험 → 활동 이상 탐지 Workflow**를 연다. **예제로 모델 학습 시작**은 합성 Dataset 등록·입력 검사 후 Workflow를 명시적으로 제출한다. **내 Dataset으로 시작**에서는 자신의 입력을 선택해 조건을 확인한 뒤 실행한다. 예제 목록을 보는 것만으로 데이터를 등록하거나 작업을 실행하지 않는다.
3. 대기·학습·판정 기준 설정·Test 적용 및 등록 대기·오류·취소 상태를 구별해 확인한다. 학습 성공이 곧 전체 Workflow 완료를 의미하지는 않는다.
4. 완료 후 학습 범위·Calibration 기준·Test 검토 건수와 분포를 확인하고 이상 후보의 실제 입력과 선택적 설명을 조사한다. 내부 행 번호는 원본 활동 ID가 아니며 이상 점수는 사고 확정·정확도·확률이 아니다.
5. 이전 결과는 **작업 이력 → Workflow 분석**이나 홈의 **이어서 이용할 분석**에서 연다. 이력 목록의 최근 최대 30건은 전체 이력 수가 아니다. 결과 상세 URL로도 직접 접근할 수 있고, 결과에서 데이터 탐색으로 이동하면 원래 분석·후보·분석 구간으로 복귀할 수 있다.

현재 Workflow는 44개 숫자형 활동 Feature와 UTC 시간 컬럼이 필요하며 원시 로그에서 Feature를 자동 생성하지 않는다. Workflow는 기본 모델 그룹·기본 실험을 사용한다. 연구 화면에서 고른 Experiment를 이어 학습하려면 독립 **새 학습**을 사용한다. 기존 모델을 다른 Dataset에 적용하는 Scoring은 새 학습과 별개다.

## 종료와 문제 해결

```bash
make logs      # API·Worker·Web 로그 확인
make doctor    # 현재 준비 상태 확인
make restart   # 애플리케이션 컨테이너 재시작
make down      # 컨테이너 종료, 데이터 볼륨 보존
```

`make down`과 `make restart`는 데이터 볼륨을 삭제하지 않는다. **`docker compose down --volumes`는 데이터를 삭제하므로 일반적인 종료 절차에 사용하지 않는다.** 실행 중 Job 상태가 불확실하거나 접수 후 화면 갱신만 실패했다면, 중복 제출 전에 기존 Job·Attempt 상태를 확인한다.

이 Compose 구성은 Token 인증을 요구하는 로컬 개발 환경이다. 외부 공개용 TLS·OIDC나 비신뢰 코드 샌드박스, 강한 물리적 자원 격리가 자동으로 제공되지는 않는다. 저장소·Iceberg의 권한과 Snapshot 보존은 [Contracts](contracts.md#iceberg-테이블-쓰기-권한), 자원·격리는 [Runtimes](runtimes.md), 오류·복구는 [Diagnostics](diagnostics.md)를 따른다. **저장소 상태 진단은 객체 삭제 기능이 아니다.**

호스트에서 현재 구성된 Python 검증을 실행하려면 프로젝트의 Python 환경과 uv를 준비하고 `make validate`를 사용한다. 이 명령은 [Testing & CI](testing.md)의 현재 Python Suite를 실행하며 **전체 제품의 검증 완료를 뜻하지 않는다.**

## MLflow 실험·모델 관리

로컬 Compose는 MLflow 관리 서버를 실행합니다. SOS 웹의 **연구·실험**에서 Experiment·Run을 조회·비교하고, **모델 활용**에서 등록된 모델 그룹과 판본을 선택합니다. 그룹 이름·설명과 생성·보관 설정은 연구 화면의 **학습 모델 그룹·등록 설정**, 판본 보관·삭제는 모델 목록의 **버전 관리·식별자**에서 확인합니다. Experiment·Run·Registered Model/Version은 MLflow가 관리하고, SOS는 Dataset·Job/Attempt·Scoring·Evaluation 및 불변 모델 실행 근거를 소유합니다.

학습 성공과 MLflow Registry 등록 성공은 구별합니다. 관리 연결이 실패하면 학습 성공은 유지되지만 새 모델의 Scoring 및 Pipeline 후속 단계는 Registry 등록을 기다립니다. 기존 `SOS_MLFLOW_EXPERIMENT_ID`는 사용하지 않습니다. 독립 학습에서 입력한 연구 Experiment 이름으로 MLflow 내 Actor별 연구 묶음을 만들며, Pipeline처럼 연구 이름을 지정하지 않은 실행은 기본 실험에 기록합니다.

외부 MLflow 서버를 사용하는 경우 `SOS_MLFLOW_TRACKING_URI`를 변경하고 필요한 인증은 `SOS_MLFLOW_TOKEN`에 설정할 수 있습니다. 기본 MLflow UI는 로컬 운영자 도구이며 사용자별 격리를 제공하지 않으므로 **외부 네트워크에 공개하지 마세요**. SOS API는 Actor별로 실행과 모델의 실제 소유권을 재검증합니다.

개발 데이터 초기화는 해당 Compose 프로젝트/볼륨이 재생성 가능한 개발 자산임을 확인한 범위에서만 수행합니다.

## Dagster OSS Workflow 운영

등록형 활동 이상 탐지 Workflow의 실행·단계 의존성과 Asset Graph는 Dagster OSS에서 관리한다. 일반 사용자의 분석 제출·조회·취소는 반드시 SOS API를 경유해야 한다. Dagster UI와 GraphQL을 외부 네트워크에 공개하거나 사용자 권한을 위임해선 안 된다. 기존 독립 ML API 및 Worker의 Job/Attempt·무결성 계약은 유지된다.

Migration 0024는 기존 자체 Pipeline 실행 그룹 테이블을 제거하기 전에 연결된 Job ID와 실행 단계의 관계를 감사 이력에 기록한다. 이전 Pipeline 그룹별 사용자 화면은 종료되지만 실제 Job·Model·Scoring·Evaluation 데이터는 보존된다. 개발 환경이라고 확인되지 않은 볼륨을 무조건 삭제해서는 안 된다.

모델 삭제는 SOS 모델 상세와 Registry 관리 화면에서 영향 정보를 확인한 뒤 실행합니다. 삭제한 모델의 MLflow Version과 실제 S3 모델 파일은 제거되며 과거 Job·Scoring·평가 근거는 보존됩니다. 버전 관리·객체 잠금·외부 백업이 있는 저장소는 관리 정책과 실제 삭제 결과를 별도로 확인해야 합니다.
