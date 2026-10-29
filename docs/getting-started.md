# 시작하기

SOS의 로컬 개발 환경을 실행하고 데이터 탐색과 준비된 모델의 이해·활용을 시작하는 안내다. 모델과 Feature는 개발 환경에서 만들고 배포 과정이 SOS에 공급하며, SOS 웹은 그 기능을 이해하고 활용하는 곳이다. 각 목적은 독립적으로 시작할 수 있으며 모든 단계를 순서대로 거칠 필요는 없다.

## 제품명과 인스턴스 표시명

관리자는 Web 사이드바에서 **제품명 설정**과 **인스턴스 표시명 설정**을 각각 변경할 수 있습니다. 제품명은 로그인 전 화면에도 표시되며, 별도로 설정하지 않은 인스턴스의 기본 제품명은 **SOS**입니다. 표시명 변경은 내부 API·Worker·데이터 식별자를 바꾸지 않습니다.

## Feast 등록 Feature (개발·자동화)

Feature Service 등록·조회·폐기는 개발·배포 자동화가 `automation` 역할의 서비스 계정으로 SOS API를 호출해 수행한다. SOS 웹은 이 작업 화면을 제공하지 않는다. 모든 API는 Actor·Dataset 권한과 Job/Attempt 계약을 적용한다.

1. `POST /api/features/services/jobs`: Feature Source Dataset, 지속적 Entity Key,
   원본 UTC Event Timestamp와 숫자형 Feature 목록을 제출한다. 성공한 Job 결과에
   Feast `feature_service` 이름과 고정 Source 판본을 남긴다.
2. `GET /api/features/services/registrations?limit=100&cursor=...`: 같은 Actor의 등록 Job
   이력을 기존 키셋 커서 계약으로 페이지 조회한다.
   이 이력은 현재 Feast Registry의 실시간 가용성 판정을 대신하지 않는다.
3. `POST /api/features/retrieval/jobs`: 등록 성공 Job ID·조회 시점 Dataset·UTC 시간
   컬럼을 제출한다. 동일 Feature Service를 조회마다 재사용하며 새 정의를 등록하지 않는다.
4. 조회 성공 Job의 결과 `dataset_id`를 모델 학습 입력으로 사용한다.
5. `POST /api/features/services/{registration_job_id}/retirement/jobs`: 성공·실패·
   취소된 등록을 명시적으로 폐기한다. `registry_deleted`는 실제 Registry 객체
   제거 여부이며 `false`인 경우 다른 활성 등록·과거 조회의 참조가 유지된 것이다.
   폐기 중이거나 폐기 완료된 등록은 새 조회에 사용할 수 없고
   과거 Dataset·모델 근거는 유지한다. 실행 중인 폐기 Job은 외부 삭제 커밋
   정합성을 위해 취소할 수 없다. 부분 실패한 폐기는 새 폐기 Job으로
   재시도하여 실제 Registry 상태를 확인해야 한다.

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
  자동화가 반환받은 SOS 제출 ID로 Dagster Run의 `sos/submission_id` 태그를 검색합니다.
  Dagster의 Re-execute는 같은 SOS 단계 Job을 재사용하므로 새 분석이 아닙니다. 새 실행은 학습 권한이 있는 계정이 SOS 웹의 **ACTIVITY & OPERATIONS → 등록 파이프라인 → 새 파이프라인 실행**에서 제출하거나, 자동화 계정이 `POST /api/pipelines/activity-anomaly-if/runs`에 새 멱등성 키로 제출합니다.
- MLflow에서 한 분석의 Run은 이름 `sos-workflow-<분석 ID 앞 8자>-<Dagster 단계명>`과 태그 `sos.workflow_submission_id`로 묶입니다.
  모델 버전의 `sos.job_id`·`sos.sha256` 태그는 SOS의 무결성 대조 값이므로 MLflow UI에서 수정하거나 삭제하지 않습니다.
- Dagster는 SOS 사용자용 학습 화면이 아니라 **로컬 운영자의 Workflow Run·단계·이벤트 확인 도구**입니다.
  학습 권한이 있는 계정은 SOS 웹의 **등록 파이프라인**에서 최근 실행과 단계별 주요 결과를 확인합니다. `/pipelines/runs/<SOS 제출 ID>`로 특정 실행을 직접 열 수 있으며, 이 링크의 결과 열람은 작업 조회 권한을 따릅니다. 새 실행에는 Feature와 함께 시간 분할용 UTC 시각 `ts`가 필요합니다. 입력 분할·학습·보정·Test 결과를 확인하고 기존 점수·정책·평가 상세와 이상 후보 조사로 이어갈 수 있습니다. 로컬 운영 권한이 있으면 해당 MLflow Run을 열 수 있습니다. Dagster 접속은 같은 PC의 브라우저에서만 가능합니다.
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

## 준비된 모델 활용

1. 홈의 **모델 목록** 또는 왼쪽 **모델 활용**을 열면 제공 중인 모델과 목적·적용 대상·한계 설명을 볼 수 있다. **모델 상세**에서 입력 조건(필수 Feature 이름·자료형)과 판본에 고정된 생성 근거를 확인하고 **데이터에 적용**으로 이동한다. 새 학습은 필요하지 않다.
2. **Dataset 분석**에서 사용할 Dataset과 입력 조건을 확인한 뒤 **이 Dataset 분석 시작**을 선택한다. 지원 모델에서는 **값 직접 입력**으로 별도 소량 점수 계산을 할 수도 있다. 화면 이동이나 파일 등록만으로 점수 계산을 실행하지 않는다.
3. 점수 계산 후에는 실제 분포·점수순 관측값·선택적 설명을 살펴보고, 필요하면 판정 정책·평가를 이어서 실행한다. 이전 실행은 **작업 이력 → 모델 적용 작업**에서 찾는다.

홈은 모델 제공 상태의 조회 중·제공 없음·조회 실패를 구별한다. 제공 중인 모델이 없으면 모델 활용 화면에도 준비된 모델이 없다고 표시한다. 데이터 탐색에서 모델 활용으로 이동하면 선택 Dataset을 이어받고 모델 입력의 Feature 이름·자료형을 비교한다. 입력 구조 일치는 업무 적합성이나 독립 평가 성능을 보장하지 않는다. 데이터 탐색은 그대로 사용할 수 있다.

## 활동 이상 탐지

왼쪽 **분석 주제 → 활동 이상 탐지**는 분석의 목적과 모델 적용 입력 조건(44개 숫자형 활동 Feature와 기록된 자료형, 관측 시각 컬럼은 선택)을 설명하고, 이 계약에 맞는 제공 모델을 보여준다. **데이터에 적용**을 선택하면 공통 모델 적용 화면에서 Dataset 또는 직접 입력에 적용한다. 점수순 관측값의 실제 입력·Train 참조 중앙값을 비교하고, SHAP 기여도·기준값 치환 민감도를 선택적으로 계산한다. 내부 행 번호는 원본 활동 ID가 아니며 이상 점수는 사고 확정·정확도·확률이 아니다. 원시 로그에서 Feature를 자동 생성하지 않는다.

파이프라인에서 만든 모델을 제공하려면 **실행 결과 → 학습 결과 → 모델 상세·제공 상태**를 엽니다. 관리자 또는 모델을 소유한 자동화 계정은 **모델 제공 등록**에서 목적·적용 대상·결과 해석과 한계(1~1,000자)를 작성하고 **모델 제공**을 명시적으로 실행합니다. 제공 상태가 확인되면 **제공 모델 목록**에서 사용자 화면을 확인합니다. 모델 상세에서 제공 설명 수정·중단·재제공도 가능합니다. 제공 중단은 새 적용만 막고 기존 결과는 유지합니다.

## 모델 공급 (개발·배포)

모델 개발·학습 설정·실험은 프로젝트 코드와 MLflow 등 개발 환경에서 수행하고, 준비된 모델은 배포 과정에서 SOS에 공급한다. 공급에는 `automation` 역할의 서비스 계정 토큰을 사용한다. `automation`은 `kind`가 `service`인 Actor에만 부여할 수 있으며, 학습(`model.train`)·모델 관리와 제공(`model.manage`)·Feature 정의 변경(`feature.manage`) 권한을 가진다. 사람 사용자 역할(`viewer`·`researcher`)에는 이 권한이 없다. 이전의 `model_builder` 역할은 제거됐으므로 해당 토큰 설정은 `automation` 서비스 계정으로 바꿔야 한다.

```bash
SOS_SUPPLY_TOKEN=<automation 토큰> .venv/bin/python scripts/supply_model.py \
  --state-file .sos/supply/activity.json \
  --dataset-file <학습 데이터.parquet> --description-file <제공 설명.txt> [--sample-rows 200000]
```

`--state-file`은 필수이며 하나의 공급 실행을 식별한다. Dataset ID·고정 학습 요청·멱등성 키·Job·Model ID를 기록하고, 같은 파일로 재실행하면 기존 등록·학습에서 이어 간다. 재개할 때는 입력 옵션을 생략할 수 있다. 이미 등록된 입력 파일이 바뀌어도 같은 상태 파일은 저장된 Dataset을 사용한다. 새로운 입력·학습에는 새 상태 파일을 명시한다. 토큰은 상태 파일에 기록하지 않으며 같은 파일을 사용하는 공급의 동시 실행은 차단한다.

파일 등록 응답이 유실되면 자동 재업로드하지 않는다. `GET /api/datasets`에서 해당 Actor의 등록 상태를 확인하고 같은 `--state-file`과 확인한 `--dataset-id`로 재개한다. 등록 여부를 확인하기 전에 새 상태 파일로 다시 실행하면 중복 Dataset이 생길 수 있다.

스크립트는 학습 데이터를 등록하고 활동 이상 탐지 분석 정의의 필수 Feature로 SOS Worker 학습을 실행한 뒤, Registry 등록을 확인하고 설명과 함께 판본을 제공한다. 제공 설명에는 모델의 목적·적용할 데이터·결과 해석 시 주의할 한계를 적는다. 제공 중단은 `DELETE /api/ml/models/{id}/offering`으로 하며 새 적용만 막고 사용자가 이미 만든 결과는 유지한다. 중단한 판본은 공급 스크립트 재실행으로 자동 재제공하지 않는다. 재제공은 `PUT /api/ml/models/{id}/offering`으로 명시적으로 요청한다.

학습 입력을 조용히 줄이지 않으며, 요청한 데이터와 실행 환경의 자원 예산이 맞지 않으면 실패한다. 큰 데이터는 `--sample-rows`로 행 수와 Seed를 명시한 부분집합을 사용한다. 학습 점수는 독립 검증 성능이 아니다. 지원되는 Permutation SHAP은 기준 점수에 대한 Feature 기여도를, 기준값 치환은 입력 하나를 바꾼 점수 반응을 나타낸다. 둘 다 사건 원인이나 인과효과를 뜻하지 않는다. 온라인 Serving·프로덕션 배포는 아직 제공하지 않는다.

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

로컬 Compose는 MLflow 관리 서버를 실행합니다. Experiment·Run 비교와 모델 그룹·판본 관리는 개발 환경과 공식 MLflow UI에서 수행합니다. SOS 웹은 제공 모델의 활용과 등록 Pipeline의 주요 결과·고정된 실행 근거를 연결합니다. Experiment·Run·Registered Model/Version은 MLflow가 관리하고, SOS는 Dataset·Job/Attempt·Scoring·Evaluation 및 불변 모델 실행 근거와 모델 제공 기록을 소유합니다.

학습 성공과 MLflow Registry 등록 성공은 구별합니다. 관리 연결이 실패하면 학습 성공은 유지되지만 새 모델의 Scoring 및 Workflow 후속 단계는 Registry 등록을 기다립니다. 기존 `SOS_MLFLOW_EXPERIMENT_ID`는 사용하지 않습니다. 학습 요청의 연구 Experiment 이름으로 MLflow 내 Actor별 연구 묶음을 만들며, 연구 이름을 지정하지 않은 실행은 기본 실험에 기록합니다.

외부 MLflow 서버를 사용하는 경우 `SOS_MLFLOW_TRACKING_URI`를 변경하고 필요한 인증은 `SOS_MLFLOW_TOKEN`에 설정할 수 있습니다. 기본 MLflow UI는 로컬 운영자 도구이며 사용자별 격리를 제공하지 않으므로 **외부 네트워크에 공개하지 마세요**. SOS API는 Actor별로 실행과 모델의 실제 소유권을 재검증합니다.

개발 데이터 초기화는 해당 Compose 프로젝트/볼륨이 재생성 가능한 개발 자산임을 확인한 범위에서만 수행합니다.

## Dagster OSS Workflow 운영

등록형 활동 이상 탐지 Workflow의 실행·단계 의존성과 Asset Graph는 Dagster OSS에서 관리한다. 학습 권한 계정의 웹 운영 화면과 자동화는 같은 API의 권한·멱등성 계약으로 제출·조회·취소하며, 웹은 주요 단계 결과와 기존 결과 상세·MLflow Run을 연결한다. Dagster UI와 GraphQL을 외부 네트워크에 공개하거나 사용자 권한을 위임해선 안 된다. 기존 독립 ML API 및 Worker의 Job/Attempt·무결성 계약은 유지된다.

Migration 0024는 기존 자체 Pipeline 실행 그룹 테이블을 제거하기 전에 연결된 Job ID와 실행 단계의 관계를 감사 이력에 기록한다. 이전 Pipeline 그룹별 사용자 화면은 종료되지만 실제 Job·Model·Scoring·Evaluation 데이터는 보존된다. 개발 환경이라고 확인되지 않은 볼륨을 무조건 삭제해서는 안 된다.

제공 중인 모델은 먼저 제공을 중단해야 삭제할 수 있습니다. 모델 삭제는 모델을 소유한 자동화 계정이 `GET /api/ml/models/{id}/deletion-impact`로 영향 정보를 확인한 뒤 `DELETE /api/ml/models/{id}`로 실행합니다. 삭제한 모델의 MLflow Version과 실제 S3 모델 파일은 제거되며 과거 Job·Scoring·평가 근거는 보존됩니다. 버전 관리·객체 잠금·외부 백업이 있는 저장소는 관리 정책과 실제 삭제 결과를 별도로 확인해야 합니다.
