# 시작하기

SOS의 로컬 개발 환경을 실행하고 첫 Dataset 탐색과 ML 학습을 진행하는 안내다. 데이터 탐색과 ML 학습은 독립적으로 시작할 수 있으며, 모든 단계를 순서대로 거칠 필요는 없다.

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
make up       # API·Worker·Web·PostgreSQL·객체 저장소 기동 및 Ready 확인
make status   # 컨테이너 상태 확인
make doctor   # 설정·준비 상태 진단
```

- Web UI: `http://localhost:9601`
- API: `http://localhost:9602`
- API Reference: `http://localhost:9602/docs`
- 로컬 MLflow 운영자 관리 UI: `http://localhost:9606`

Web UI의 인증 화면에 앞에서 보관한 Bearer Token을 입력한다. 토큰은 브라우저 메모리에만 보관되므로 다시 접속하면 재인증이 필요하다. 접속 주소와 실행 구성은 [Compose](../compose.yaml) 및 [Makefile](../Makefile)을 따른다.

## 첫 Dataset 탐색

1. 왼쪽의 **예제 데이터 살펴보기**에서 합성 데이터를 선택하거나 **파일 가져오기**로 CSV·Parquet·TSV/TXT를 등록한다. TSV/TXT는 자유 서술 텍스트가 아닌 UTF-8 탭 구분 데이터이며, 등록 시 헤더 유무를 지정한다. 예제 목록을 여는 것만으로는 등록되지 않으며, **이 예제로 탐색 시작**을 선택하면 일반 Dataset으로 등록된다.
2. 등록된 Dataset을 선택하고 **데이터 탐색**에서 구조와 판본을 확인한다. 필요하면 **기본 분석 실행**을 명시적으로 요청하거나 **데이터 조회**·**탐색**에서 일부 데이터를 살펴본다. 업로드만으로 분석 Job이 자동 실행되지는 않는다.
3. 전체 범위의 집계나 데이터 가공이 필요하면 해당 Job 또는 **데이터 준비**를 별도로 실행한다. Data Explorer에서 탐색만 하고 끝내도 된다.

미리보기와 부분 데이터 분석은 전체 Dataset의 분포를 보장하지 않는다. 파일 등록 성공은 모든 행의 품질 검증 완료를 뜻하지 않으며, 이후 조회·분석·Job에서 오류가 발견될 수 있다. 입력 형식·계산 범위와 결과의 의미는 [Contracts](contracts.md)와 화면·API의 현재 안내를 따른다.

## 등록된 분석 Pipeline으로 시작

1. 홈의 **시간 기반 활동 이상 탐지** 또는 왼쪽 **활동 이상 탐지**를 연다. 기존 Dataset이 없다면 **예제 데이터 선택하고 입력 확인**을 명시적으로 눌러 44개 활동 Feature·UTC 시각이 들어 있는 합성 Dataset을 등록·검사할 수 있다. 예제 조회만으로 Dataset이 자동 등록되거나 분석이 실행되지 않는다.
2. 자신의 데이터로 시작하려면 Data Explorer에서 Dataset을 등록한 뒤 Pipeline에서 선택하고 **이 Dataset의 입력 조건 확인**을 누른다. 구조 검사만으로 실제 시간 분할이나 학습 성공이 보장되지는 않는다.
3. **이 데이터로 분석 실행하기**를 명시적으로 선택하면 Worker가 시간 분할을 검증하고 모델 학습·Calibration·정책·Test Scoring·평가를 실행한다. 진행 중 상태와 취소 요청을 확인할 수 있으며, 브라우저를 닫아도 Job은 계속된다.
4. 완료된 Run에서 **Training / Calibration / Test** 결과를 직접 살펴보고 Test 이상 후보·Feature·설명 기능을 이용할 수 있다. 생성 모델을 다른 호환 Dataset에 적용하는 독립 **Scoring**도 같은 Pipeline 화면에서 시작할 수 있으나, 이는 새로운 Pipeline 학습이나 기존 Test 판본 변경이 아니다. 후보의 내부 행 번호는 원본 관측치 ID가 아니며, 이상 점수는 사고·정답·확률을 의미하지 않는다.

현재 등록된 첫 Pipeline은 명시된 활동 Feature 이름과 UTC 시간 컬럼을 요구한다. 아무 Dataset에나 자동 적용되거나 사용자의 데이터를 다른 사용자에게 공유하지 않는다. 독립적인 데이터 탐색·학습·Scoring은 Pipeline을 거치지 않고도 사용할 수 있다.

## 첫 ML 학습

1. 홈 아래의 **독립 ML 실행·모델**을 열어 **학습** 영역으로 이동하거나 기존 `/training` 주소에서 등록된 Dataset과 학습할 숫자형 Feature를 선택한다. Data Explorer에서 분석 Job을 실행할 필요는 없다.
2. 학습 입력을 전체 Dataset(`all`, 기본값) 또는 행 수와 Seed를 지정한 부분집합(`sample`)으로 선택하고 Isolation Forest 설정을 확인한다. 입력 부분집합의 행 수와 모델의 트리별 표본 수는 서로 다른 설정이다.
3. **학습 Job 제출** 후 **ML 학습 이력**에서 실제 상태와 오류를 확인한다. 성공하면 해당 Job의 **Model Revision**, 사용한 Dataset 판본·학습 입력 근거 및 학습 점수 요약을 확인한다.

학습 입력은 고정된 50,000행·32 Feature 한도로 제한하지 않지만, 요청한 데이터와 실행 환경의 자원 예산이 맞지 않으면 **입력을 조용히 줄이지 않고 실패**한다. 정확한 입력 조건과 설정 범위는 ML Workbench 및 [API Reference](http://localhost:9602/docs)가 정본이다.

학습 점수는 독립 검증 성능이 아니다. 필요하면 Scoring·판정 정책·평가·설명을 별도 실행하여 근거를 확인한다. 현재 내장된 설명을 SHAP/LIME이나 인과적 기여도로 해석하지 않는다. 온라인 Serving·프로덕션 배포는 아직 제공하지 않는다.

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

로컬 Compose는 MLflow 관리 서버를 실행합니다. SOS 웹의 **모델 판본**에서 등록된 모델과 Run 비교를 확인할 수 있습니다. Experiment·Run·Registered Model/Version은 MLflow가 관리하고, SOS는 Dataset·Job/Attempt·Scoring·Evaluation 및 불변 모델 실행 근거를 소유합니다.

학습 성공과 MLflow Registry 등록 성공은 구별합니다. 관리 연결이 실패하면 학습 성공은 유지되지만 새 모델의 Scoring 및 Pipeline 후속 단계는 Registry 등록을 기다립니다. 기존 `SOS_MLFLOW_EXPERIMENT_ID`는 사용하지 않고 Actor별 Experiment를 생성합니다.

외부 MLflow 서버를 사용하는 경우 `SOS_MLFLOW_TRACKING_URI`를 변경하고 필요한 인증은 `SOS_MLFLOW_TOKEN`에 설정할 수 있습니다. 기본 MLflow UI는 로컬 운영자 도구이며 사용자별 격리를 제공하지 않으므로 **외부 네트워크에 공개하지 마세요**. SOS API는 Actor별로 실행과 모델의 실제 소유권을 재검증합니다.

개발 데이터 초기화는 해당 Compose 프로젝트/볼륨이 재생성 가능한 개발 자산임을 확인한 범위에서만 수행합니다.
