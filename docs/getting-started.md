# 시작하기

SOS의 로컬 개발 환경을 실행하고 데이터 탐색과 준비된 모델의 활용을 시작하는 안내다. 모델과 Feature는 개발 환경에서 만들고 배포 과정이 SOS에 공급하며, SOS 웹은 그 기능을 이해하고 활용하는 곳이다. 각 목적은 독립적으로 시작할 수 있다.

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

출력된 `SOS_AUTH_TOKEN_HASHES=...` 한 줄을 `.env`에 추가한다. **Bearer Token 원문은 `.env`나 Git에 넣지 말고 별도로 안전하게 보관**한다. 토큰 지문 매핑이 없으면 API는 기동하지 않는다. 다른 계정은 같은 형식으로 지문을 추가하며, 역할은 `viewer`·`researcher`·`operator`·`administrator`와 서비스 계정 전용 `automation`이다.

## 실행과 접속

```bash
make up       # 개발 서비스 기동·DB migration·Ready 확인
make status   # 컨테이너 상태 확인
make doctor   # 설정·준비 상태 진단
```

- Web UI: `http://localhost:9601`
- API: `http://localhost:9602`
- API Reference: `http://localhost:9602/docs`

Web UI의 인증 화면에 보관한 Bearer Token을 입력한다. 토큰은 브라우저 메모리에만 보관되므로 다시 접속하면 재인증이 필요하다. 포트와 실행 구성은 [Compose](../compose.yaml)와 [Makefile](../Makefile)을 따른다.

저장소 루트 `.env`의 포트·자원 설정은 Compose가 해당 서비스에 전달한다. Web을 호스트에서 실행할 때도 루트 `.env`를 읽는다. Python API·CLI를 호스트에서 직접 실행하면 `.env`를 자동으로 읽지 않으므로 필요한 연결 설정을 실행 환경에 전달한다. 설정 변경은 해당 컨테이너를 재생성한 뒤 적용된다.

## 첫 Dataset 탐색

1. 홈의 **데이터 탐색 시작** 또는 메뉴의 **데이터 → 데이터 탐색**을 연다. **데이터 추가·연결 → 파일 가져오기**로 CSV·Parquet·TSV/TXT를 등록한다. CSV는 UTF-8·쉼표 구분·첫 행 헤더 형식이어야 하며, Excel에서는 'CSV UTF-8(쉼표로 분리)'로 저장한다. TSV/TXT는 UTF-8 탭 구분 데이터이며 등록 시 헤더 유무를 지정한다.
2. 등록된 Dataset의 구조와 판본을 확인한다. 필요하면 **기본 분석 실행**을 요청하거나 **데이터 조회**·**탐색**에서 일부 데이터를 살펴본다. 업로드만으로 분석 Job이 실행되지는 않는다.
3. 전체 범위의 집계나 데이터 가공이 필요하면 해당 Job 또는 **데이터 준비**를 별도로 실행한다.

미리보기와 부분 데이터 분석은 전체 Dataset의 분포를 보장하지 않는다. 파일 등록 성공은 모든 행의 품질 검증 완료를 뜻하지 않는다.

## 준비된 모델 활용

1. 홈의 **모델 목록** 또는 메뉴의 **ML 분석 → 모델 활용**에서 제공 중인 모델과 목적·적용 대상·한계 설명을 본다. **모델 상세**에서 입력 조건(필수 Feature 이름·자료형)과 생성·평가 근거를 확인하고 **데이터에 적용**으로 이동한다. Playground 주제가 있는 모델은 그 주제의 Playground로 이동한다.
2. Playground 또는 적용 화면에서 Dataset을 선택해 입력 조건을 확인한 뒤 분석을 시작하거나, **값 직접 입력**으로 소량 점수 계산을 한다. 입력 구조가 모델과 다른 Dataset은 접수 전에 원인 컬럼과 함께 거부된다.
3. 점수 분포·점수순 관측값·선택적 설명을 살펴보고, 필요하면 결과 화면 상단의 **평가·판정 정책·점수 설명**을 이어서 실행한다. 이전 실행은 **작업 이력 → 모델 적용 작업**에서 찾는다.

메뉴의 **ML 분석 → 비지도 학습 → 이상 탐지 → 활동 이상 탐지**는 활용 방법을 보여주고, 같은 화면에서 입력 조건(44개 숫자형 활동 Feature, 관측 시각 컬럼은 선택)에 맞는 제공 모델을 자신의 Dataset·직접 입력에 적용해 결과와 API 연동 방법까지 확인하게 한다. 이상 점수는 사고 확정·정확도·확률이 아니며, 원시 로그에서 Feature를 자동 생성하지 않는다.

## 종료와 문제 해결

```bash
make logs      # API·Worker·Web 로그 확인
make doctor    # 현재 준비 상태 확인
make restart   # 애플리케이션 컨테이너 재시작
make down      # 컨테이너 종료, 데이터 볼륨 보존
```

**`docker compose down --volumes`는 데이터를 삭제하므로 일반적인 종료 절차에 사용하지 않는다.** 실행 중 Job 상태가 불확실하거나 접수 후 화면 갱신만 실패했다면 중복 제출 전에 기존 Job·Attempt 상태를 확인한다. 오류 코드·요청 ID에 따른 확인과 복구는 [Diagnostics](diagnostics.md)를 따른다.

이 Compose 구성은 로컬 개발 환경이다. 외부 공개용 TLS·OIDC, 비신뢰 코드 샌드박스, 강한 물리적 자원 격리는 제공하지 않는다. 자원·격리는 [Runtimes](runtimes.md), 저장소 권한과 Snapshot 보존은 [Contracts](contracts.md#iceberg-테이블-쓰기-권한)를 따른다.

호스트에서 검증을 실행하려면 Python 3.12·uv·Node.js 24를 준비하고 `make validate`와 `cd web && npm run test:navigation`을 사용한다. 범위는 [Testing & CI](testing.md)를 따른다.

## 모델 공급 (개발·배포)

모델 개발·학습 설정·실험은 프로젝트 코드와 MLflow 등 개발 환경에서 수행하고, 준비된 모델은 배포 과정에서 SOS에 공급한다. 공급에는 `kind: service` Actor에만 부여할 수 있는 `automation` 역할의 토큰을 사용한다. 이 역할은 학습(`model.train`)·모델 연결·관리와 제공(`model.manage`)·Feature 정의 변경(`feature.manage`) 권한을 가진다.

모델 연결은 다음 두 경로 중 하나를 선택한다. Dagster에서 이미 연결한 MLflow 버전을 공급 스크립트로 다시 연결하면 별도의 연결 Job과 SOS Model Revision이 만들어질 수 있다.

| 연결 경로 | Dagster 설정 | 연결 이후 |
| --- | --- | --- |
| Dagster에서 SOS 연결까지 실행 | `connect_to_sos: true` (기본값) | 결과의 SOS 판본을 그대로 제공·배포 |
| 공급 스크립트로 SOS 연결 | `connect_to_sos: false` | MLflow 이름·고정 버전을 CLI에 전달 |

### Dagster에서 SOS 연결까지 실행

Dagster의 `activity_anomaly_production` Job이 학습 데이터를 시간순 Train·Calibration·Test로 나누어 학습·평가하고, 모델 패키지(`sos_model.json`·`estimator.skops`)를 MLflow 모델 버전으로 등록한 뒤 SOS에 연결을 요청한다. 학습 파일은 `dagster/inputs`(또는 `SOS_PIPELINE_INPUT_DIR`)에 두고 Run 설정의 `data_path`에 `/data/inputs/<파일>`로 지정한다. 파일에는 UTC 오프셋이 있는 `ts`와 활동 Feature가 필요하다. SOS 연결에는 공급 계정 토큰의 해시를 `SOS_AUTH_TOKEN_HASHES`에 `kind: service`, `roles: [automation]`으로 등록하고, 원문은 `SOS_SUPPLY_TOKEN`으로 실행 환경에 주입한 뒤 Compose를 기동한다. 공급 토큰은 Dagster Code Location에만 전달되며 API·Web 컨테이너에는 전달되지 않는다.

```yaml
ops:
  train_and_evaluate:
    config:
      data_path: /data/inputs/activity.parquet
      registered_model: activity-anomaly-if
      connect_to_sos: true
```

성공한 `connect_to_sos` 단계의 `sos_model_revision` Materialization에서 `model_revision_id`를 확인한다. SOS의 `/models/<model_revision_id>`를 열어 모델 상세에서 제공 설명·모델 제공·선택적 온라인 배포를 진행한다. 이 판본을 제공·배포하려고 `--mlflow-model`로 다시 연결하지 않는다. 연결 성공은 사용자 제공이나 온라인 배포 완료를 의미하지 않는다.

다른 생산 Pipeline도 `ml_model_package.package_isolation_forest`로 같은 패키지를 만들어 MLflow 버전의 아티팩트 위치에 두면 연결할 수 있다. 생산 환경의 Python minor·numpy·scikit-learn 버전은 SOS와 같아야 한다.

### 공급 스크립트

MLflow 버전을 CLI로 연결할 예정이면 위 Dagster Run 설정을 `connect_to_sos: false`로 바꾸어 MLflow 등록까지만 실행한다. 공급 스크립트는 다른 경로에서 이미 연결한 판본을 찾아 재사용하지 않는다. `--state-file` 재사용은 같은 CLI 제출의 재개만 보장한다.

```bash
# 아직 SOS에 연결하지 않은 MLflow 고정 버전을 연결·제공
SOS_SUPPLY_TOKEN=<automation 토큰> .venv/bin/python scripts/supply_model.py \
  --state-file .sos/supply/activity-v3.json \
  --mlflow-model activity-anomaly-if --mlflow-version 3 --description-file <제공 설명.txt> \
  [--endpoint activity-anomaly]

# Dataset으로 SOS에서 학습
SOS_SUPPLY_TOKEN=<automation 토큰> .venv/bin/python scripts/supply_model.py \
  --state-file .sos/supply/activity.json \
  --dataset-file <학습 데이터.parquet> --description-file <제공 설명.txt> [--sample-rows 200000] \
  [--endpoint activity-anomaly]
```

스크립트는 MLflow 고정 버전을 다시 학습하지 않고 연결하거나, 학습 데이터를 등록해 활동 이상 탐지의 필수 Feature로 SOS Worker 학습을 실행한다. 이후 Registry 등록을 확인하고 설명과 함께 판본을 제공한다. `--state-file`은 하나의 공급 실행을 식별하며, 같은 파일로 재실행하면 기존 등록·학습에서 이어 간다. 새로운 입력·학습에는 새 상태 파일을 쓴다. 파일 등록 응답이 유실되면 자동 재업로드하지 않으므로, `GET /api/datasets`에서 등록 상태를 확인하고 같은 `--state-file`과 확인한 `--dataset-id`로 재개한다.

학습 원본은 접근 권한이 통제된 연구 환경에서 관리하며 공개 저장소나 기본 다운로드 데이터에 포함하지 않는다. 모델 공급 전에는 Feature별 결측의 의미와 사용할 입력, 정상 기준·학습 기간, 레이블 정의와 평가 조건을 연구에서 정한다. 필수 Feature가 모두 존재하거나 중앙값 대체가 가능하다는 사실은 연구적 유효성이나 탐지 성능을 입증하지 않는다.

Dataset 학습 경로는 선택한 Dataset 전체(또는 명시적 표본)와 Train 중앙값 결측 대체로 새 모델을 학습하며 별도 Calibration/Test 평가를 실행하지 않는다. 시간 분할·평가가 필요하면 생산 Pipeline으로 학습·평가한 버전을 연결한다.

제공 설명에는 모델의 목적·적용할 데이터·결과 해석 시 주의할 한계를 적는다. 제공·재제공은 `PUT`, 중단은 `DELETE /api/ml/models/{id}/offering`이며 중단은 새 적용만 막고 이미 만든 결과는 유지한다. 웹에서는 관리자 또는 모델을 소유한 자동화 계정이 **모델 상세**에서 제공·설명 수정·중단을 실행한다. 학습 데이터를 조용히 줄이지 않으므로 큰 데이터는 `--sample-rows`로 행 수를 명시한다.

`--endpoint`를 지정하면 공급한 판본을 그 이름의 온라인 서빙 Endpoint에 배포한다. 대상은 공급 계정이 소유한 같은 이름의 Endpoint이며, 처음 선택하거나 만든 Endpoint ID를 상태 파일에 고정한다. 관리자 토큰으로 실행해도 다른 계정의 Endpoint는 바꾸지 않는다. 이 계정의 Endpoint가 있으면 새 판본으로 전환을 요청하며, 서빙 Runtime이 새 판본의 준비를 확인할 때까지 기존 판본이 요청을 처리한다. 스크립트는 새 판본이 활성 판본이 되면 끝나고, 배포에 실패하면 사유를 출력하고 실패한다. 이후 상태 확인·되돌리기·중단은 웹의 **작업·운영 → 온라인 서빙** 또는 `/api/serving/endpoints` API로 한다. 추론 요청은 다음과 같다.

```bash
curl -X POST "$SOS_API/api/serving/endpoints/<Endpoint ID>/predict" -H "Authorization: Bearer $SOS_TOKEN" \
  -H "Content-Type: application/json" -d '{"inputs": [{"<Feature 이름>": <값>, ...}]}'
```

Endpoint를 삭제하려면 먼저 중단한다. Endpoint가 활성·전환 대상·직전 판본으로 참조하는 모델은 삭제할 수 없으므로, 모델을 삭제하기 전에 다른 판본으로 전환하거나 Endpoint를 중단·삭제한다.

제공 중인 모델은 먼저 제공을 중단해야 삭제할 수 있다. 모델을 소유한 자동화 계정이 `GET /api/ml/models/{id}/deletion-impact`로 영향을 확인한 뒤 `DELETE /api/ml/models/{id}`로 삭제한다. MLflow Version과 S3 모델 파일은 제거되고 과거 Job·Scoring·평가 근거는 보존된다.

## Feature 등록 (개발·배포)

Feast Feature Service의 등록·시점 조회·폐기는 `automation` 계정이 SOS API로 실행하며 웹 화면은 제공하지 않는다. 등록(`POST /api/features/services/jobs`)과 조회(`POST /api/features/retrieval/jobs`)는 별개 Job이고, 조회 결과 Dataset을 모델 학습 입력으로 사용한다. 폐기는 `POST /api/features/services/{registration_job_id}/retirement/jobs`로 요청한다. 요청 필드는 API Reference, 의미는 [Contracts](contracts.md#등록-feature)를 따른다.

## 로컬 운영 도구

아래 도구는 SOS Actor 권한을 적용하지 않는 로컬 운영자 도구이므로 **localhost 외부로 공개하거나 일반 사용자에게 전달하지 않는다.**

- **온라인 서빙 (SOS 웹):** 모델 관리 권한 계정의 **작업·운영 → 온라인 서빙**에서 Endpoint별 활성·전환 대상·직전 판본과 실제 실행 상태·배포 실패 사유를 확인하고 되돌리기·전환 취소·중단·재개·삭제를 실행한다. 같은 PC에서 접속한 운영 계정에는 판본마다 Ray Dashboard의 해당 애플리케이션 화면으로 가는 **복제본·로그** 링크가 보인다.
- **Ray Dashboard** (`http://localhost:9609`): 온라인 서빙 복제본 상태, 배포 실패 메시지 전체, 복제본 로그와 자원 사용량을 확인한다. 애플리케이션 이름은 `<Endpoint ID>--<모델 판본 ID>`다. 보는 용도로 쓰며, 배포·전환은 SOS로 요청한다. Ray에 직접 적용한 Serve Config는 컨트롤러가 다음 조정에서 SOS 의도로 되돌린다.
- **Dagster** (`http://localhost:9607`): 모델 생산 Job `activity_anomaly_production`을 실행하고 Run·단계·이벤트를 확인한다. Run마다 새 MLflow Run·모델 버전을 만든다.
- **MLflow** (`http://localhost:9606`): 생산 Pipeline의 Run은 Experiment `activity-anomaly-production`에 남는다. SOS 모델 그룹(`sos-if-*`) 버전의 `sos.job_id`·`sos.sha256` 태그는 SOS의 무결성 대조 값이므로 수정하거나 삭제하지 않는다. 연결한 버전은 `sos.source_model`·`sos.source_version`으로 원본을 가리킨다. 외부 서버는 `SOS_MLFLOW_TRACKING_URI`·`SOS_MLFLOW_TOKEN`으로 연결한다.
- **Feast UI** (`http://localhost:9608`): `sos_features` SQL Registry를 읽기 전용 DB 계정으로 탐색한다. 등록된 Feature가 없으면 빈 Catalog가 정상이다.

학습·연결 성공과 MLflow Registry 등록 성공은 구별한다. 등록 대기 중에는 SOS의 MLflow 전송 상태를 확인하며, Dagster Run 성공이나 MLflow Run `Finished`만으로 SOS에서 모델을 쓸 수 있다고 판단하지 않는다. 복구는 [Diagnostics](diagnostics.md)를 따른다.

API Reference의 Scalar JavaScript는 Docker 이미지에 포함된다. 호스트에서 Python API를 직접 실행하면 Node.js 24로 `bash scripts/build_scalar_assets.sh`를 먼저 실행한다.
