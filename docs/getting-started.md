# 시작하기

SOS는 기본적으로 전체 플랫폼을 Docker Compose로 실행한다. 이 실행 방식은 로컬에 국한된 플랫폼 계약이 아니다.

## 실행

Docker Compose와 GNU Make가 필요하다. `SOS_AUTH_TOKEN_HASHES`에 원본 토큰의 SHA-256 지문을 키로 사용하는 Actor JSON을 명시해야 한다. 실제 원본 Bearer Token은 별도로 안전하게 보관하며, `.env`는 커밋하지 않는다. Python 3.12·uv·Node.js 24·npm은 호스트 개발 도구로 사용한다.

개발용 관리자 Token과 지문 매핑은 다음처럼 한 번 생성할 수 있다. 출력된 **Bearer Token은 별도 보관**하고 매핑 JSON 한 줄만 커밋하지 않는 `.env`에 넣는다.

```bash
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

```bash
# .env 또는 환경변수에 설정: SOS_AUTH_TOKEN_HASHES={"<token-sha256>":{"actor_id":"local","kind":"human","roles":["administrator"]}}
make up          # Docker의 API·Worker·Web·PostgreSQL·S3 실행 및 Ready 확인
make status
make doctor
make validate     # 신규로 구성된 소규모 Python 검증(전체 제품 보증 아님)
make restart
make logs
make down        # 컨테이너만 종료, 데이터 볼륨 보존
```

- Web UI: `http://localhost:9601`
- API: `http://localhost:9602`
- API Reference: `http://localhost:9602/docs`
- PostgreSQL 개발 포트: `9603`
- S3 개발 포트: `9605`

`compose.yaml`이 실행 구성의 정본이다. 기본 `make up`은 `app` 프로필을 사용하며, CI의 인프라 전용 기동은 프로필 없이 PostgreSQL·SeaweedFS만 실행한다. `docker compose --profile app up --build -d --wait`를 사용하면 Make 없이 동일하게 기동할 수 있다. `SOS_AUTH_TOKEN_HASHES`를 생략하면 API가 시작을 거부한다.

- 개발 컨테이너는 Token 인증을 요구하며 Docker 네트워크를 관리자 신원으로 취급하지 않는다. 외부 공개용 TLS·OIDC·격리는 구현하지 않았다.
- 내부 연결은 `postgres:5432`, `storage:8333`, `api:9602`를 사용한다. 호스트 포트는 루프백으로만 공개한다.
- `make down`은 볼륨을 보존한다. `down --volumes`는 데이터 삭제를 포함하므로 일반 환경에서 사용하지 않는다.
- Worker의 강한 물리 메모리 격리나 비신뢰 코드 샌드박스는 Docker 기동만으로 보장되지 않는다.

## 구성과 안전

Compose가 PostgreSQL과 S3 호환 저장소를 관리한다. 기본 객체 저장소는 SeaweedFS이고 애플리케이션은 S3 API로 접근한다. Iceberg Catalog 메타데이터는 PostgreSQL에, 실제 데이터는 객체 저장소에 보관한다.

현재 Compose 기본 경로는 관리형 로컬 DB·객체 저장소를 초기화한다. 외부 관리형 연결과 호스트 실행은 공개 Make 경로의 옵션이 아니며, 별도 배포 어댑터의 책임이다. Token 인증이 설정되지 않으면 API는 기동을 거부하며 Docker 네트워크를 신뢰된 개발 관리자 연결로 간주하지 않는다.

`make down`과 `make restart`는 DB 볼륨·Iceberg 데이터·아티팩트를 삭제하지 않는다. API·Worker 캐시는 각자의 Compose 볼륨에서 관리되며 DB·객체 저장소 볼륨과 다른 수명주기를 갖는다. `SOS_HOME`은 API·Worker·초기화/검증 도구가 사용하는 로컬 캐시 및 임시 실행 파일 경로를 함께 변경하며, `--home`을 명시한 Worker에서는 CLI 인자가 우선한다.

CSV·Parquet·구조화된 TSV/TXT 업로드는 기본 256 MiB 이하이고 `SOS_MAX_UPLOAD_MIB`로 1~2048 MiB 범위를 설정할 수 있다. Multipart 본문은 파일 한도에 최대 64 KiB의 전송 헤더·필드 여유를 더해 제한하며 이 범위를 넘으면 파일 크기와 무관하게 거부될 수 있다. 여러 업로드를 동시에 진행하면 임시 디스크에 파서 스풀과 검증용 임시 파일이 함께 존재할 수 있으므로 운영 환경에서 여유 용량을 확보한다. HTTP `201`은 원본을 저장하고 Dataset 참조를 확정한 상태를 뜻하며, 목록 새로고침 실패는 등록 실패가 아니다. 파일 등록 시에는 기본 형식과 스키마를 검사하지만 전체 행의 품질·타입 일관성까지 보장하지 않으며, 이후 Preview·Profile·Job에서 추가 데이터 오류가 발견될 수 있다.

데이터 미리보기는 최대 100행을 반환하고, 기본 EDA 프로파일은 정렬 없는 제한 조회 최대 1,000행을 계산한다. Dataset Understanding의 무작위 표본 분석은 고정 Seed Reservoir 방식을 사용하며 전체 분포의 대표성을 보장하지 않는다. 변환 결과 미리보기(Feature Preview)는 입력을 먼저 표본 추출하지 않고 실제 Job과 같은 변환 순서를 적용한 결과를 최대 30행 보여준다. Row-local Feature Preview는 원본 전체 행 수 때문에 거부하지 않으며 Lazy 출력 제한을 적용한다. 전역 정렬·Lag·Rolling은 동기 입력 예산을 확인하고 초과하면 전체 Transform Job을 사용하도록 안내한다. 전체 프로파일·피처 처리·Iceberg 적재는 Job으로 실행한다. Snapshot ID를 기록했더라도 만료 정책으로 사라질 수 있으므로 유효성 확인과 보존 정책이 필요하다.

코드·환경·UX의 기준은 [Architecture](architecture.md), [Experience](experience.md), [Contracts](contracts.md), [Workflows](workflows.md), [Runtimes](runtimes.md)를 따른다. Isolation Forest 학습·Batch Scoring·정책·평가·설명·Fork를 지원한다. MLflow Tracking은 운영자가 명시적으로 연결하는 선택적 외부 기능이다. 온라인 Serving·프로덕션 배포는 제공하지 않는다.


## 첫 데이터 탐색

처음 접속할 때 **예제 데이터 살펴보기**에서 두 종류의 합성 데이터를 선택하거나 CSV·Parquet 파일, 탭 구분 TSV/TXT 파일을 등록할 수 있다. TSV/TXT는 등록 전에 첫 행이 헤더인지 선택하며, 없는 경우 컬럼명을 `unnamed_1`, `unnamed_2`부터 부여한다. TXT는 일반 자유 텍스트가 아니라 UTF-8 탭 구분 표여야 하며, 따옴표는 일반 문자로 취급하고 서버가 모든 행의 열 수를 검증한다. 예제를 선택하기 전에는 서버에 자동 등록되지 않으며, 선택 이후에는 일반 Dataset과 같은 API로 EDA·Feature·Job을 실행한다. Jobs는 현재 데이터셋을 선택하지 않아도 별도로 확인할 수 있다.

### Dataset Discovery — 부분 데이터에서 전체 분석까지

1. CSV·Parquet 파일을 업로드하거나 합성 예제를 등록한 뒤, 해당 Dataset의 **데이터 이해**에서 원본 규모·판본을 확인한다. 기본 통계·역할 후보를 확인하고 싶으면 **기본 분석 실행**을 눌러 별도 Job을 제출한다. 데이터 등록만으로 분석 Job이 자동 생성되지는 않는다. 분석 없이 **데이터 조회**, **탐색**, **데이터 준비**로 이동할 수 있다.
2. **데이터 발견**에서 행 수 확인 기준, 컬럼 타입·검색 결과를 확인한다. CSV의 전체 행 수는 이 단계에서 계산하지 않는다.
3. **분석 집계**에서 컬럼·집계 방식을 선택한다. 평균·최솟값·최댓값은 숫자형 컬럼에만 적용하고, 행 수와 NULL 수는 문자형을 포함한 모든 컬럼에서 조회할 수 있다. 그룹별 집계도 가능하다.
4. **부분 데이터 집계**는 정렬 없이 제한 조회한 데이터의 결과로 전체 분포를 대표하지 않는다. 전체 데이터를 대상으로 확인하려면 **전체 집계 Job 실행**을 선택한다. 전체 데이터 대상이어도 평균 등 개별 계산 결과는 근사값일 수 있다.
5. **전체 Jobs 보기**에서 실제 실행 상태와 집계 결과 표·원본 Dataset 판본·그룹 표시 제한을 확인한다. 작업 접수 성공 후 목록 새로고침이 실패한 경우에는 동일 Job을 반복 제출하지 말고 목록을 다시 확인한다.

Data Explorer는 데이터 이해·조회·탐색 자체로 마칠 수 있다. 데이터를 가공하거나 Iceberg에 추가하려면 전역 메뉴의 **데이터 준비(Data Preparation)**를 연다. 아직 Dataset이 없어도 데이터 준비 화면에 진입해 파일을 등록하거나 기존 Dataset을 선택할 수 있다. Preview 표는 기본적으로 처음 12개 컬럼을 보여주며 필요하면 전체 컬럼을 펼칠 수 있다. 탐색 차트는 값·가로축 컬럼을 사용자가 선택하며, 최대 100행 조회 일부를 전체 시계열로 오해하지 않아야 한다.

Preview는 수치형 NaN·양/음의 무한대를 각각 문자열 `NaN`·`Infinity`·`-Infinity`로 표시해 NULL과 구분한다. 바이너리 값은 `base64:` 접두 문자열(원본 dtype으로 식별), DECIMAL·안전 정수 범위 밖의 값은 정밀 십진 문자열, 시간 값은 ISO-8601 문자열로 표시한다. 집계 결과가 NaN·무한대일 경우 그룹 순위와 제한을 정하기 전에 JSON `null`로 치환하며, 샘플·전체 Job이 같은 계약을 사용한다. 평균(AVG)은 DuckDB의 실수형 근사 연산이고 DECIMAL 최솟값/최댓값은 정확한 문자열이다. 전체 Profile은 별도 Worker Job을 사용하며, DuckDB의 연결별 예산은 실제 프로세스의 메모리·시간 제한을 완전히 보장하지 않는다.

시계열 데이터의 Lag·Rolling에는 정렬 컬럼이 필요하다. 동일 그룹에서 정렬값이 중복되거나 NULL이면 결과 순서가 모호하므로 시계열 연산을 거부한다. 필요한 경우 입력에서 시간값을 정리한 뒤 작업을 제출한다. 다수의 설비가 섞여 있으면 그룹 컬럼도 선택해 각 설비의 과거 값만 참조하도록 한다. Rolling Mean에는 현재 행이 포함되므로 학습 목표 누수를 피하도록 시점을 확인한다.

API 응답 타입을 변경한 경우 Pydantic 모델을 변경하고 `cd web && npm run generate:types`를 실행한다. 이 명령은 FastAPI의 OpenAPI를 임시 파일로 추출해 `web/src/api.generated.ts`를 갱신한다. 생성 파일 변경은 코드 리뷰에서 확인하며 OpenAPI 자체를 별도 정본 파일로 보관하지 않는다. Scalar API 문서는 기존 `/docs`를 따른다.

Dataset 목록은 `GET /api/datasets?limit=20`의 `items`와 `next_cursor`를 사용하는 키셋 페이지 조회다. 무제한 목록 API나 별도 `/api/datasets/pages` 경로는 제공하지 않는다.

Job 목록은 `GET /api/jobs?limit=20`에서 최근 순으로 조회하며, 응답의 `items`와 `next_cursor`를 사용해 이전 페이지를 가져온다. `limit`는 1~100으로 제한된다. 목록은 `attempt_count`·`latest_attempt`만 제공하므로 전체 실행 시도는 개별 `GET /api/jobs/{job_id}`에서 조회한다.

비파티션 Iceberg 테이블은 Polars 스테이징·PyArrow 배치 Reader를 이용해 단일 Snapshot으로 적재한다. 기존 파티션 테이블만 현재 최대 50,000행·64 MiB 추정 메모리 제한의 호환 경로를 사용한다. 실행 시간·메모리·스테이징 디스크 예산은 별도로 적용하며, 제한 초과와 커밋 결과 불확실성을 구별한다.

## ML Workbench — 첫 Isolation Forest 학습

전역 메뉴의 **ML Workbench**는 Data Explorer 실행 여부와 무관하게 등록된 Dataset에서 시작한다. 다음 기능은 현재 구현된 범위이며, 전문적인 모델 평가나 운영 배포를 완료했다는 뜻은 아니다.

1. Dataset을 선택하고 숫자형 Feature를 1~32개 지정한다. 학습 입력은 2~50,000행이며, NULL·NaN·Infinity가 있거나 Float64로 정확히 변환할 수 없는 큰 정수값이 있으면 거부한다. 원본 데이터는 변경되지 않는다.
2. 트리 개수와 트리별 표본 수를 설정하고 **학습 Job 제출**을 선택한다. 제출 후에는 ML 학습 이력에서 실제 Queued·Running·Succeeded·Failed·Cancelled 상태와 Attempt 기록을 확인할 수 있다. 시간제한·메모리 제한·잘못된 Feature는 오류 코드로 식별한다.
3. 완료한 Job에서 불변 **Model Revision** ID, 입력 Dataset ID·판본, Feature 순서, 학습 설정, 런타임 버전 및 학습 점수 요약을 확인한다. 모델은 내부 Worker가 생성하고 S3에 보존한다. 사용자가 제공한 pickle/joblib를 서버에서 복원하는 기능은 없다.
4. 다른 성공한 학습 Job을 선택하면 두 모델의 설정·입력 판본을 비교할 수 있다. 서로 다른 Dataset 판본·Feature를 직접 비교할 수 없고, 입력이 같아도 학습 점수 평균이나 이상 후보 수는 정확도·모델 순위를 의미하지 않는다.

API 클라이언트는 `POST /api/ml/train/jobs`로 학습을 제출하고 `GET /api/ml/train/jobs`, `GET /api/ml/train/jobs/{job_id}`, `POST /api/ml/train/jobs/{job_id}/cancel`, `GET /api/ml/models/{revision_id}`로 상태·판본을 조회한다. Token 인증에서는 학습 제출에 `Idempotency-Key`가 필요하며, 각 요청에는 Job/모델 권한 및 소유권 검사가 적용된다. 지원 파라미터와 응답 필드는 `/docs`의 OpenAPI를 따른다.

Policy·Evaluation·Explanation은 독립 Job 이력을 보존하고 취소 권한이 있는 사용자는 진행 중인 작업의 취소를 요청할 수 있다. 취소 요청은 즉시 완료를 뜻하지 않으며 Job에서 최종 상태를 확인한다. Scoring 이력은 이전 페이지를 이어서 조회할 수 있다.

**판단 근거의 한계:** 학습 데이터의 참고 분위수는 최적 운영 임계값이 아니다. 다른 Dataset으로 실행한 Scoring에서는 모델을 재학습하지 않고 점수를 재사용하며 고정·분위수 정책, 이진 레이블 기반 평가, 개별 Feature 치환 민감도를 독립 결과로 저장한다. 같은 Scoring Dataset으로 고른 임계값에서 계산한 지표를 별도 검증셋의 성능이라고 주장하지 않는다. 설명은 SHAP/LIME이나 인과적 기여도가 아니다.

### 선택적 MLflow Tracking

MLflow 추적을 사용하지 않으면 별도 구성이 필요 없다. 사용할 경우 MLflow Tracking 서버를 별도로 운영하고 **기존 Experiment ID**를 확인한 뒤, 커밋하지 않는 `.env`에 다음을 설정한다.

```bash
SOS_MLFLOW_TRACKING_URI=https://tracking.example.org
SOS_MLFLOW_EXPERIMENT_ID=17
# 인증이 필요한 경우에만 지정한다. 값은 버전 관리하지 않는다.
SOS_MLFLOW_TOKEN=<외부 서버에서 발급한 토큰>
```

Worker는 환경 설정에 따라 전송 대기 기록을 Job 완료 트랜잭션 안에 생성하고, 별도로 MLflow REST API에 Run·설정·유한한 지표를 전송한다. 모델 바이너리와 원본 데이터는 전송하지 않는다. 외부 서버 연결에 실패하면 상태는 `Retry`가 되며 SOS Job·Model Revision은 성공으로 유지된다. 재시작 시 미완료 전송은 재조회한다. 최초 전송 대상의 서버·Experiment 연결 지문을 보존하며, 운영 설정이 바뀌면 이전 Run ID의 재사용을 차단하고 `SOS-MLFLOW-TARGET-CHANGED`를 기록한다. 이전 대상 복구 또는 명시적 전송 이관 판단이 필요하다. `GET /api/ml/tracking/jobs/{job_id}`에서 권한이 있는 사용자만 외부 전송 상태를 확인한다. 전송 중 네트워크 응답이 소실되면 Job ID로 외부 Run을 검색하지만, 외부 Search/Create가 원자적이지 않으므로 절대적인 exactly-once를 보장하지 않는다.

HTTP를 통한 전송은 기본적으로 루프백 서버에서만 허용한다. 별도로 격리된 관리형 네트워크에서만 `SOS_MLFLOW_ALLOW_INSECURE_HTTP=true`를 명시적으로 사용할 수 있으며 공개망에서는 HTTPS를 사용한다. 외부 MLflow 주소·토큰은 사용자 API로 입력받지 않고 API 서버에 토큰을 전달하지 않는다.

## 기존 Iceberg 테이블의 읽기·보존과 새 적재

`make up`은 관리형 DB에 Alembic 마이그레이션을 자동 적용한다. 기존 Dataset·Snapshot·Tag와 Iceberg 데이터 파일은 삭제하거나 다른 소유자로 이전하지 않는다.

SOS의 쓰기 토큰과 물리 Table UUID가 일치하지 않는 기존 테이블은 **읽기·Snapshot 보존만 허용**하고 Append를 거부한다. 소유자를 추측하거나 관리자 명령으로 테이블을 채택하는 경로는 제공하지 않는다.

해당 Dataset에 대한 읽기·실행 권한이 있고 추가 적재가 필요하다면, 기존 Dataset을 입력으로 **사용되지 않은 새 테이블명**에 Iceberg 적재 Job을 제출한다. 새 테이블은 원자적으로 소유권을 예약하고 고유 토큰·물리 UUID를 기록하며, 기존 테이블과 Snapshot 참조는 변하지 않는다. 기존 테이블의 이름을 재사용하거나 소유권 미확정 레코드를 자동 정리하지 않는다.

## 객체 게시·보존 정책과 저장소 진단

Data Workspace 사이드바의 **저장소 상태 보기**에서 `datasets/`, `results/`의 S3 객체를 페이지 단위로 읽기 전용으로 대조할 수 있다. 대조는 관측 이력을 PostgreSQL에 기록하지만, S3의 객체를 삭제하지 않는다. `iceberg/`는 범용 대조에서 제외되며 별도 **Snapshot 검사**로 Dataset의 고정 Snapshot 존재를 확인한다. 진단은 해당 요청 시 실행되며 백그라운드 정리 스케줄은 없다.

안전 기본값은 `SOS_OBJECT_GRACE_DAYS=7`, `SOS_OBJECT_OBSERVATION_HOURS=24`, `SOS_OBJECT_MIN_OBSERVATIONS=2`, `SOS_OBJECT_PAGE_SIZE=100`이다. 환경변수로 조정할 수 있으나 각각 최소 7일, 최소 24시간, 최소 2회, 한 페이지 1~200개 범위로 제한된다. `GET /api/storage/policy`는 실제 적용값과 환경변수/기본값 출처를 제공한다. 자동 삭제를 활성화하는 옵션은 없다.

`GET /api/storage/inspection`은 한 번에 한 접두사의 한 페이지만 조회하고 `next_cursor`를 반환한다. `protected`는 참조 또는 실행으로 보호 중, `waiting`은 유예·관측 대기, `unknown`은 소유권·게시 상태가 불명확하여 삭제 금지, `candidate`는 복수 관측된 실패 객체에 대한 검토 신호이다. **candidate도 삭제 허가가 아니다.** S3 또는 DB가 장애 상태이면 추측해 결과를 확정하지 않는다. 새 Transform 결과는 `results/{job_id}/{attempt_id}.parquet`에 저장되며 기존 결과 참조를 변경하지 않는다.

## Token 인증과 Snapshot 보존

공개 `make up`은 API를 Token 인증 모드로 실행한다. `SOS_AUTH_TOKEN_HASHES`에 SHA-256 지문(64자리 소문자 16진수)을 키로 하고 `{"actor_id":"...","kind":"human"|"service","roles":["viewer"|"researcher"|"operator"|"administrator"]}`를 값으로 하는 JSON을 명시한다. 원본 Token은 별도로 보관하며 HTTP 요청은 `Authorization: Bearer <token>`으로 인증한다. 브라우저는 현재 화면 메모리에서만 Token을 보관한다. 누락·잘못된 Token 설정은 API가 기동을 거부한다. 개발 컨테이너를 외부 공개 TLS·OIDC 서비스로 사용하지 않는다.

Iceberg 테이블에 Dataset을 연결할 때는 데이터 생성 권한과 해당 Dataset 참조를 통한 읽기 접근이 필요하다. Iceberg 적재 Job을 제출할 때는 실행 권한뿐 아니라 별도 테이블 쓰기 소유권도 필요하며, API 제출과 Worker의 Catalog 커밋 직전 모두 검증한다. 신규 테이블은 PostgreSQL에 소유권·고유 토큰을 예약하고 Catalog 생성 시 동일 토큰을 기록한다. 이 권한은 Snapshot 참조 Dataset의 소유권으로 자동 부여되지 않는다. 기존 테이블 중 이 소유 토큰이 없는 대상은 읽기·보존을 유지하되 자동 인수하지 않고 새 적재를 거부한다. 테이블 이름이 소실되거나 재생성됐어도 자동 쓰기를 허용하지 않는다. 소유권이 확인되지 않은 기존 테이블은 수동 채택·자동 인수·양도를 제공하지 않으며, 별도 신규 테이블로 데이터를 적재할 수 있다. 기존 Dataset의 보존 Tag를 수동으로 재확인하는 동작은 운영 보존 권한을 사용한다. 신규 Iceberg Dataset은 Snapshot의 SOS Tag를 만들고 재검증한 후에만 등록한다. 예전 Dataset은 Storage Health에서 `읽기 가능 · 보존 미보장`으로 표시될 수 있으며, 운영자 권한으로 해당 Dataset의 Tag를 생성할 수 있다. 데이터가 없는 Tag·Catalog 장애에서는 보존을 확정하지 않으며 기존 데이터 파일과 Tag를 자동 삭제하지 않는다. 보안 감사 조회는 `GET /api/v2/audit`에서 운영 권한으로 가능하다. 기존 소유권 없는 Dataset은 별도의 권한 확인 없이 다른 사용자에게 공개되지 않는다.
