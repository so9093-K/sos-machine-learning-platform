# 시작하기

SOS의 Data Workspace를 실제 API·Worker·데이터 서비스와 함께 실행한다. **개발 환경에서 검증하지만 플랫폼 아키텍처는 로컬 전용이 아니다.**

## 실행

Python 3.12, uv, Node.js 24, npm, Docker Compose, GNU Make가 필요하다. 필요하면 `.env.example`을 바탕으로 `.env`를 작성한다. 비밀정보가 포함된 `.env`는 커밋하지 않는다.

```bash
make           # 사용 가능한 명령 보기
make up        # 환경 확인 · 스키마 확인/초기화 · 서비스 시작 · Readiness 확인
make status    # 서비스별 상태
make doctor    # 읽기 전용 환경 진단
make check     # Ruff · pytest · UI 빌드/테스트 · 격리된 별도 Playwright (make up 필요)
make check-fast # 인프라 없이 Ruff · Python 순수 로직·CLI · UI 테스트·빌드
make restart   # DB 재초기화 없이 애플리케이션 재시작
make logs      # API · Worker · Web 로그
make down      # 관리 대상만 종료하고 데이터 보존
```

- Web UI: `http://localhost:9601`
- Common API: `http://localhost:9602`
- Scalar API Reference: `http://localhost:9602/docs`
- PostgreSQL 호스트 포트: `9603`
- S3 호환 스토리지 호스트 포트: `9605`

`python3 scripts/dev.py status --format json`으로 JSON 진단을 받을 수 있다. `python3 scripts/dev.py doctor --deep`은 API 및 의존성 준비 상태도 확인한다. `make check`는 실행 중인 서비스가 Ready인 상태에서 사용한다. 브라우저 쓰기 테스트는 개발 서비스가 아닌 UUID 식별자를 가진 별도 PostgreSQL/S3 Compose 프로젝트·API·Worker·Web에서 실행하며, 검사로 만든 테스트 프로젝트의 볼륨만 종료 시 정리한다. 일반 개발 프로젝트의 데이터·볼륨·Snapshot Tag는 수정하거나 삭제하지 않는다.

## 구성과 안전

기본 개발 환경에서는 Compose가 PostgreSQL과 S3 호환 저장소를 관리한다. 현재 기본 객체 저장소는 공식 SeaweedFS 이미지를 사용하되, 애플리케이션은 S3 API를 사용하므로 다른 호환 서비스와 관리형 스토리지로 교체할 수 있다. Iceberg SQL Catalog 메타데이터는 PostgreSQL에, 데이터 파일은 객체 저장소에 보관한다.

`SOS_INFRA_MODE=managed`에서는 로컬 Compose 포트와 일치하지 않는 DB·S3 연결 대상을 감지하면 마이그레이션·스토리지 변경 전에 기동을 거부한다. `SOS_DATABASE_URL`로 외부 DB를 지정하려면 `external` 모드를 사용한다.

`SOS_INFRA_MODE=external`에서는 CLI가 DB·객체 저장소를 시작·종료·마이그레이션하지 않는다. `make up`은 외부 서비스의 스키마 호환성과 객체 저장소 접근을 읽기 전용으로 확인한다. 외부 서비스에 연결하려면 `SOS_DATABASE_URL`과 `SOS_S3_ENDPOINT`·자격 증명 등을 제공한다. 개발 프로세스는 기본적으로 루프백 주소에서만 HTTP를 수신한다. 로컬 개발 신원과 사설 토큰 인증·권한 검사가 구현되어 있지만, OIDC·TLS·역방향 프록시를 포함한 외부 공개 서비스 배포 구성은 제공하지 않는다. 개발 모드에서는 프록시 전달 헤더가 있는 요청도 거부하지만, 프록시가 헤더를 제거하면 원격 사용자를 완전히 식별할 수 없으므로 네트워크 경계 자체를 유지해야 한다. 루프백에 바인딩된 개발용 서비스를 외부 프록시로 공개하면 개발 관리자 신원이 우회적으로 노출될 수 있으므로 금지한다.

`make down`과 `make restart`는 DB 볼륨·Iceberg 데이터·아티팩트를 삭제하지 않는다. 실행용 로컬 캐시와 로그는 `.sos/`와 `.sos-dev/`에서 별도로 관리되며 DB·객체 저장소 볼륨과 다른 수명주기를 갖는다. `SOS_HOME`은 API·Worker·초기화/검증 도구가 사용하는 로컬 캐시 및 임시 실행 파일 경로를 함께 변경하며, `--home`을 명시한 Worker에서는 CLI 인자가 우선한다.

CSV·Parquet 업로드는 기본 256 MiB 이하이고 `SOS_MAX_UPLOAD_MIB`로 1~2048 MiB 범위를 설정할 수 있다. Multipart 본문은 파일 한도에 최대 64 KiB의 전송 헤더·필드 여유를 더해 제한하며 이 범위를 넘으면 파일 크기와 무관하게 거부될 수 있다. 여러 업로드를 동시에 진행하면 임시 디스크에 파서 스풀과 검증용 임시 파일이 함께 존재할 수 있으므로 운영 환경에서 여유 용량을 확보한다. HTTP `201`은 원본을 저장하고 Dataset 참조를 확정한 상태를 뜻하며, 목록 새로고침 실패는 등록 실패가 아니다. 파일 등록 시에는 기본 형식과 스키마를 검사하지만 전체 행의 품질·타입 일관성까지 보장하지 않으며, 이후 Preview·Profile·Job에서 추가 데이터 오류가 발견될 수 있다.

데이터 미리보기는 최대 100행, 기본 프로파일은 최대 1,000행 기준이다. Feature Preview는 입력을 먼저 임의 절단하지 않고 전체 입력에서 실제 Job과 같은 순서로 변환한 뒤 출력 30행을 보여준다. Row-local Feature Preview는 원본 전체 행 수 때문에 거부하지 않으며 Lazy 출력 제한을 적용한다. 전역 정렬·Lag·Rolling은 동기 입력 예산을 확인하고 초과하면 전체 Transform Job을 사용하도록 안내한다. 전체 프로파일·피처 처리·Iceberg 적재는 Job으로 실행한다. Snapshot ID를 기록했더라도 만료 정책으로 사라질 수 있으므로 유효성 확인과 보존 정책이 필요하다.

코드·환경·UX의 기준은 [Architecture](architecture.md), [Experience](experience.md), [Contracts](contracts.md), [Workflows](workflows.md), [Runtimes](runtimes.md)를 따른다. ML 모델 학습·평가·추론은 아직 이번 기능에 포함되지 않는다.

## 첫 데이터 탐색

처음 접속할 때 **예제 데이터 살펴보기**에서 두 종류의 합성 데이터를 선택하거나 CSV·Parquet 파일을 등록할 수 있다. 예제를 선택하기 전에는 서버에 자동 등록되지 않으며, 선택 이후에는 일반 Dataset과 같은 API로 EDA·Feature·Job을 실행한다. Jobs는 현재 데이터셋을 선택하지 않아도 별도로 확인할 수 있다.

### Dataset Discovery — 샘플에서 전체 분석까지

1. CSV·Parquet 파일을 업로드하거나 합성 예제를 등록한 뒤, 해당 Dataset의 **데이터 이해**에서 원본 규모·판본·프로파일 범위를 확인하고 필요에 따라 **데이터 조회** 또는 **탐색** 탭을 연다.
2. **데이터 발견**에서 행 수 확인 기준, 컬럼 타입·검색 결과를 확인한다. CSV의 전체 행 수는 이 단계에서 계산하지 않는다.
3. **분석 집계**에서 컬럼·집계 방식을 선택한다. 평균·최솟값·최댓값은 숫자형 컬럼에만 적용하고, 행 수와 NULL 수는 문자형을 포함한 모든 컬럼에서 조회할 수 있다. 그룹별 집계도 가능하다.
4. **샘플 집계**는 제한된 선두 데이터의 결과로 전체 분포를 대표하지 않는다. 정확한 전체 결과가 필요하면 **전체 집계 Job 실행**을 선택한다.
5. **전체 Jobs 보기**에서 실제 실행 상태와 집계 결과 표·원본 Dataset 판본·그룹 표시 제한을 확인한다. 작업 접수 성공 후 목록 새로고침이 실패한 경우에는 동일 Job을 반복 제출하지 말고 목록을 다시 확인한다.

Data Explorer는 데이터 이해·조회·탐색 자체로 마칠 수 있다. 데이터를 가공하거나 Iceberg에 추가하려면 Dataset을 선택한 상태에서 전역 메뉴의 **데이터 준비(Data Preparation)**를 연다. Preview 표는 기본적으로 처음 12개 컬럼을 보여주며 필요하면 전체 컬럼을 펼칠 수 있다. 탐색 차트는 값·가로축 컬럼을 사용자가 선택하며, 최대 100행 조회 일부를 전체 시계열로 오해하지 않아야 한다.

Preview는 수치형 NaN·양/음의 무한대를 각각 문자열 `NaN`·`Infinity`·`-Infinity`로 표시해 NULL과 구분한다. 바이너리 값은 `base64:` 접두 문자열(원본 dtype으로 식별), DECIMAL·안전 정수 범위 밖의 값은 정밀 십진 문자열, 시간 값은 ISO-8601 문자열로 표시한다. 집계 결과가 NaN·무한대일 경우 그룹 순위와 제한을 정하기 전에 JSON `null`로 치환하며, 샘플·전체 Job이 같은 계약을 사용한다. 평균(AVG)은 DuckDB의 실수형 근사 연산이고 DECIMAL 최솟값/최댓값은 정확한 문자열이다. 전체 Profile은 별도 Worker Job을 사용하며, DuckDB의 연결별 예산은 실제 프로세스의 메모리·시간 제한을 완전히 보장하지 않는다.

시계열 데이터의 Lag·Rolling에는 정렬 컬럼이 필요하다. 동일 그룹에서 정렬값이 중복되거나 NULL이면 결과 순서가 모호하므로 시계열 연산을 거부한다. 필요한 경우 입력에서 시간값을 정리한 뒤 작업을 제출한다. 다수의 설비가 섞여 있으면 그룹 컬럼도 선택해 각 설비의 과거 값만 참조하도록 한다. Rolling Mean에는 현재 행이 포함되므로 학습 목표 누수를 피하도록 시점을 확인한다.

API 응답 타입을 변경한 경우 Pydantic 모델을 변경하고 `cd web && npm run generate:types`를 실행한다. 이 명령은 FastAPI의 OpenAPI를 임시 파일로 추출해 `web/src/api.generated.ts`를 갱신한다. CI에서 생성 파일의 차이를 검사하며 OpenAPI 자체를 별도 정본 파일로 보관하지 않는다. Scalar API 문서는 기존 `/docs`를 따른다.

Dataset 목록은 `GET /api/datasets?limit=20`의 `items`와 `next_cursor`를 사용하는 키셋 페이지 조회다. 무제한 목록 API나 별도 `/api/datasets/pages` 경로는 제공하지 않는다.

Job 목록은 `GET /api/jobs?limit=20`에서 최근 순으로 조회하며, 응답의 `items`와 `next_cursor`를 사용해 이전 페이지를 가져온다. `limit`는 1~100으로 제한된다. 목록은 `attempt_count`·`latest_attempt`만 제공하므로 전체 실행 시도는 개별 `GET /api/jobs/{job_id}`에서 조회한다.

비파티션 Iceberg 테이블은 Polars 스테이징·PyArrow 배치 Reader를 이용해 단일 Snapshot으로 적재한다. 기존 파티션 테이블만 현재 최대 50,000행·64 MiB 추정 메모리 제한의 호환 경로를 사용한다. 실행 시간·메모리·스테이징 디스크 예산은 별도로 적용하며, 제한 초과와 커밋 결과 불확실성을 구별한다.

## 기존 Iceberg 테이블의 안전한 쓰기 복구

`make up`은 **관리형** DB에 Alembic 마이그레이션을 자동 적용한다. 따라서
업데이트할 때 별도의 `alembic upgrade head` 명령을 실행할 필요가 없다.
마이그레이션은 기존 Dataset·Snapshot·Tag를 삭제하거나 소유자를 추측해 이전하지 않는다.
기존 테이블은 읽을 수 있어도, 이전 버전에서 생성되어 새 소유 토큰이 없는 경우
추가 적재가 거부될 수 있다.

로컬 **관리형** 환경에서 기존 테이블을 계속 수정하려면 다음의 관리 명령을 사용한다.
새 소유권을 저장하기 전에 항상 읽기 전용 진단을 수행한다.

```bash
python3 scripts/dev.py iceberg-ownership --table sensor_data
```

진단 결과가 `candidate`이고 Dataset 참조가 단일 명시적 소유자일 때만
출력된 `table_uuid`와 `current_snapshot_id`를 확인한다.
해당 테이블에 대한 기존 Iceberg Dataset의 ID와 소유자까지 확인한 후
그 값을 그대로 명시해 채택한다.

```bash
python3 scripts/dev.py iceberg-ownership --table sensor_data \
  --owner local-developer --dataset-id <기존-Iceberg-Dataset-ID> \
  --expect-table-uuid <진단-table_uuid> --expect-snapshot <진단-current_snapshot_id> --apply
```

이 절차는 데이터 변경이 아니라 테이블 Metadata의 쓰기 소유권 토큰과 감사 상태만
갱신한다. Dataset 소유자가 복수·불명확하거나 참조/테이블 자체가 없거나
예상 Snapshot·Table UUID가 달라졌다면 채택을 거부한다. 외부 데이터베이스는
로컬 CLI가 수정하지 않으며 배포 관리자가 별도 검토해야 한다.
Catalog 커밋이나 감사 확정 중 오류가 발생하면 자동 재시도하지 말고
같은 진단을 다시 수행해 상태를 확인한다. 보류 상태는 원래
`--owner`·`--dataset-id`·식별자를 검증한 명시적 실행으로만 이어갈 수 있다.

## 객체 게시·보존 정책과 저장소 진단

Data Workspace 사이드바의 **저장소 상태 보기**에서 `datasets/`, `results/`의 S3 객체를 페이지 단위로 읽기 전용으로 대조할 수 있다. 대조는 관측 이력을 PostgreSQL에 기록하지만, S3의 객체를 삭제하지 않는다. `iceberg/`는 범용 대조에서 제외되며 별도 **Snapshot 검사**로 Dataset의 고정 Snapshot 존재를 확인한다. 진단은 해당 요청 시 실행되며 백그라운드 정리 스케줄은 없다.

안전 기본값은 `SOS_OBJECT_GRACE_DAYS=7`, `SOS_OBJECT_OBSERVATION_HOURS=24`, `SOS_OBJECT_MIN_OBSERVATIONS=2`, `SOS_OBJECT_PAGE_SIZE=100`이다. 환경변수로 조정할 수 있으나 각각 최소 7일, 최소 24시간, 최소 2회, 한 페이지 1~200개 범위로 제한된다. `GET /api/storage/policy`는 실제 적용값과 환경변수/기본값 출처를 제공한다. 자동 삭제를 활성화하는 옵션은 없다.

`GET /api/storage/inspection`은 한 번에 한 접두사의 한 페이지만 조회하고 `next_cursor`를 반환한다. `protected`는 참조 또는 실행으로 보호 중, `waiting`은 유예·관측 대기, `unknown`은 소유권·게시 상태가 불명확하여 삭제 금지, `candidate`는 복수 관측된 실패 객체에 대한 검토 신호이다. **candidate도 삭제 허가가 아니다.** S3 또는 DB가 장애 상태이면 추측해 결과를 확정하지 않는다. 새 Transform 결과는 `results/{job_id}/{attempt_id}.parquet`에 저장되며 기존 결과 참조를 변경하지 않는다.

## 실험적 권한 경계와 Snapshot 보존

현재 로컬 개발 실행은 `SOS_AUTH_MODE=development`이고 개발자 신원이 자동 적용된다. 이 모드의 API는 루프백과 테스트 클라이언트 요청만 허용하며 **역방향 프록시를 통해 외부에 노출해서는 안 된다**. 인증 필수 테스트·사설 API 연동은 `SOS_AUTH_MODE=token`으로 변경하고 `SOS_AUTH_TOKEN_HASHES`에 JSON 객체로 고엔트로피 토큰의 SHA-256 해시(64자리 소문자 16진수)를 키, `{"actor_id":"...", "kind":"human"|"service", "roles":["viewer"|"researcher"|"operator"|"administrator"]}`를 값으로 설정한다. 원본 토큰은 해당 환경변수에 저장하지 않는다. API 요청은 `Authorization: Bearer <token>`을 사용하며, 브라우저에서는 현재 화면 메모리에만 보관한다. 토큰 모드에서 유효한 설정이 없다면 서비스 시작을 거부한다. 외부 공개는 아직 지원하지 않으며 TLS와 OIDC 로그인·계정 생명주기를 포함한 별도 운영 보안 구성이 필요하다.

Iceberg 테이블에 Dataset을 연결할 때는 데이터 생성 권한과 해당 Dataset 참조를 통한 읽기 접근이 필요하다. Iceberg 적재 Job을 제출할 때는 실행 권한뿐 아니라 별도 테이블 쓰기 소유권도 필요하며, API 제출과 Worker의 Catalog 커밋 직전 모두 검증한다. 신규 테이블은 PostgreSQL에 소유권·고유 토큰을 예약하고 Catalog 생성 시 동일 토큰을 기록한다. 이 권한은 Snapshot 참조 Dataset의 소유권으로 자동 부여되지 않는다. 기존 테이블 중 이 소유 토큰이 없는 대상은 읽기·보존을 유지하되 자동 인수하지 않고 새 적재를 거부한다. 테이블 이름이 소실되거나 재생성됐어도 자동 쓰기를 허용하지 않는다. 관리형 로컬 환경에서는 소유권이 단일 Actor로 확인된 기존 테이블에 한해 명시적 진단·채택 CLI를 제공하며, 외부 DB와 모호한 테이블에 대한 자동 인수·양도는 제공하지 않는다. 기존 Dataset의 보존 Tag를 수동으로 재확인하는 동작은 운영 보존 권한을 사용한다. 신규 Iceberg Dataset은 Snapshot의 SOS Tag를 만들고 재검증한 후에만 등록한다. 예전 Dataset은 Storage Health에서 `읽기 가능 · 보존 미보장`으로 표시될 수 있으며, 운영자 권한으로 해당 Dataset의 Tag를 생성할 수 있다. 데이터가 없는 Tag·Catalog 장애에서는 보존을 확정하지 않으며 기존 데이터 파일과 Tag를 자동 삭제하지 않는다. 보안 감사 조회는 `GET /api/v2/audit`에서 운영 권한으로 가능하다. 기존 소유권이 없는 Dataset은 관리자가 명시적으로 마이그레이션하기 전까지 일반 사용자에게 노출되지 않는다.
