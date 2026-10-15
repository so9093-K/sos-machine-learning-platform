# 시작하기

SOS의 Data Workspace를 실제 API·Worker·데이터 서비스와 함께 실행한다. **개발 환경에서 검증하지만 플랫폼 아키텍처는 로컬 전용이 아니다.**

## 실행

Python 3.12, uv, Node.js 24, npm, Docker Compose, GNU Make가 필요하다. 필요하면 `.env.example`을 바탕으로 `.env`를 작성한다. 비밀정보가 포함된 `.env`는 커밋하지 않는다.

```bash
make           # 사용 가능한 명령 보기
make up        # 환경 확인 · 스키마 확인/초기화 · 서비스 시작 · Readiness 확인
make status    # 서비스별 상태
make doctor    # 읽기 전용 환경 진단
make check     # Ruff · pytest · UI 빌드/테스트 · Playwright
make restart   # DB 재초기화 없이 애플리케이션 재시작
make logs      # API · Worker · Web 로그
make down      # 관리 대상만 종료하고 데이터 보존
```

- Web UI: `http://localhost:9601`
- Common API: `http://localhost:9602`
- Scalar API Reference: `http://localhost:9602/docs`
- PostgreSQL 호스트 포트: `9603`
- S3 호환 스토리지 호스트 포트: `9605`

`python3 scripts/dev.py status --format json`으로 JSON 진단을 받을 수 있다. `python3 scripts/dev.py doctor --deep`은 API 및 의존성 준비 상태도 확인한다. `make check`는 실행 중인 서비스가 Ready인 상태에서 사용한다.

## 구성과 안전

기본 개발 환경에서는 Compose가 PostgreSQL과 S3 호환 저장소를 관리한다. 현재 기본 객체 저장소는 공식 SeaweedFS 이미지를 사용하되, 애플리케이션은 S3 API를 사용하므로 다른 호환 서비스와 관리형 스토리지로 교체할 수 있다. Iceberg SQL Catalog 메타데이터는 PostgreSQL에, 데이터 파일은 객체 저장소에 보관한다.

`SOS_INFRA_MODE=external`에서는 CLI가 DB·객체 저장소를 시작·종료·마이그레이션하지 않는다. `make up`은 외부 서비스의 스키마 호환성과 객체 저장소 접근을 읽기 전용으로 확인한다. 외부 서비스에 연결하려면 `SOS_DATABASE_URL`과 `SOS_S3_ENDPOINT`·자격 증명 등을 제공한다. 개발 프로세스는 기본적으로 루프백 주소에서만 HTTP를 수신하며, 현재 인증·권한이나 공개 서비스 배포 구성을 제공하지 않는다.

`make down`과 `make restart`는 DB 볼륨·Iceberg 데이터·아티팩트를 삭제하지 않는다. 실행용 로컬 캐시와 로그는 `.sos/`와 `.sos-dev/`에서 별도로 관리되며 DB·객체 저장소 볼륨과 다른 수명주기를 갖는다. `SOS_HOME`은 API·Worker·초기화/검증 도구가 사용하는 로컬 캐시 및 임시 실행 파일 경로를 함께 변경하며, `--home`을 명시한 Worker에서는 CLI 인자가 우선한다.

CSV·Parquet 업로드는 20 MiB 이하이며, 데이터 미리보기는 최대 100행, 기본 프로파일은 최대 1,000행 기준이다. Feature Preview는 입력을 먼저 임의 절단하지 않고 전체 입력에서 실제 Job과 같은 순서로 변환한 뒤 출력 30행을 보여준다. 동기 요청의 과도한 자원 점유를 막기 위해 입력이 100,000행을 넘으면 Preview를 거절하고 전체 Transform Job을 사용하도록 안내한다. 전체 프로파일·피처 처리·Iceberg 적재는 Job으로 실행한다. Snapshot ID를 기록했더라도 만료 정책으로 사라질 수 있으므로 유효성 확인과 보존 정책이 필요하다.

코드·환경·UX의 기준은 [Architecture](architecture.md), [Experience](experience.md), [Contracts](contracts.md), [Workflows](workflows.md), [Runtimes](runtimes.md)를 따른다. ML 모델 학습·평가·추론은 아직 이번 기능에 포함되지 않는다.

## 첫 데이터 탐색

처음 접속할 때 **예제 데이터 살펴보기**에서 두 종류의 합성 데이터를 선택하거나 CSV·Parquet 파일을 등록할 수 있다. 예제를 선택하기 전에는 서버에 자동 등록되지 않으며, 선택 이후에는 일반 Dataset과 같은 API로 EDA·Feature·Job을 실행한다. Jobs는 현재 데이터셋을 선택하지 않아도 별도로 확인할 수 있다.

시계열 데이터의 Lag·Rolling에는 정렬 컬럼이 필요하다. 다수의 설비가 섞여 있으면 그룹 컬럼도 선택해 각 설비의 과거 값만 참조하도록 한다. Rolling Mean에는 현재 행이 포함되므로 학습 목표 누수를 피하도록 시점을 확인한다.

API 응답 타입을 변경한 경우 Pydantic 모델을 변경하고 `cd web && npm run generate:types`를 실행한다. 이 명령은 FastAPI의 OpenAPI를 임시 파일로 추출해 `web/src/api.generated.ts`를 갱신한다. CI에서 생성 파일의 차이를 검사하며 OpenAPI 자체를 별도 정본 파일로 보관하지 않는다. Scalar API 문서는 기존 `/docs`를 따른다.

단일 Iceberg 적재는 현재 최대 50,000행과 64 MiB 추정 메모리 제한을 적용한다. 초과하면 Job이 실패하며 원본 파일·이전 Snapshot은 유지한다. 대용량 배치 적재는 별도의 자원·원자성 정책이 필요하다.
