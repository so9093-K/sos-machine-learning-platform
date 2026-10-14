# 시작하기

현재 제공되는 기능은 **로컬 개발용 Data Workspace**다. 공개 서비스용 인증·권한, 원격 Worker 조정, 프로덕션 Catalog는 아직 없다. 외부 네트워크에 그대로 노출하지 않는다.

## 준비

Python 3.12, uv, Node.js 24, npm이 필요하다.

```bash
uv python install
uv sync --locked --group dev
cd web && npm ci && cd ..
```

## 로컬 실행

서로 다른 터미널에서 다음을 실행한다.

```bash
uv run --locked uvicorn sos_platform.api:app --host 127.0.0.1 --port 8000
```

```bash
uv run --locked python -m sos_platform.worker
```

```bash
cd web
npm run dev
```

- Web UI: http://127.0.0.1:5173
- Scalar API: http://127.0.0.1:8000/docs
- OpenAPI: http://127.0.0.1:8000/openapi.json

데이터와 실행 기록은 기본적으로 프로젝트 내 `.sos/`에 보관한다. API 서버와 Worker는 동일한 `SOS_HOME` 경로를 사용해야 한다. Worker는 **이 저장소 루트당 하나의 프로세스만** 실행한다.

CSV·Parquet 업로드는 현재 최대 20 MiB로 제한된다. 업로드한 파일은 SHA-256을 이용해 구별되는 원본으로 보관한다. Iceberg는 로컬 SQLite Catalog를 사용하며, 파일 데이터를 테이블로 가져올 때 새 Snapshot ID가 등록된다. 과거 스냅샷의 보존은 원래 Iceberg 테이블 관리 정책에 달려 있다.

EDA 화면은 처음 1,000행을 기준으로 프로파일링하고, 차트는 미리보기 최대 100행을 사용한다. 전체 데이터 프로파일과 피처 변환은 Job을 제출한 다음 별도 Worker가 처리한다. Worker 종료 후 남은 Running 작업은 **단일 Worker 재시작 시** 실행 시도 이력을 Interrupted로 남기고 Queued 상태에서 다시 시작한다. 이 로컬 실행기는 분산 큐가 아니다.

## 검사

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
cd web && npm run build && npm run test
```

주요 설계 기준은 [Experience](experience.md), [Contracts](contracts.md), [Runtimes](runtimes.md), [Workflows](workflows.md)에 있다. ML 학습과 추론은 아직 구현되지 않았다.
