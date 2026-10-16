# 테스트·검증·CI 운영 기준

이 문서는 SOS의 테스트·CI 체계 정본이다. 데이터 및 API의 의미는 Contracts, Workflows와 실제 구현이 정본이며, 이 문서는 중복 명세를 만들지 않는다.

## 기본 원칙

- 사용자·데이터·실행 불변조건을 정의한 뒤 그 계약을 제대로 검사할 수 있는 가장 저렴한 테스트 계층을 선택한다.
- 모든 테스트는 단독·순서 변경·반복 실행이 가능해야 한다. 다른 테스트에서 생성한 데이터를 요구하지 않는다.
- `make check`의 브라우저 쓰기 테스트는 UUID 별도 Compose 프로젝트와 DB·버킷·서비스에만 연결한다. 일반 개발 프로젝트/외부 저장소에 쓰기 E2E를 실행하지 않는다. 생성한 E2E 프로젝트만 명시적으로 종료·볼륨 정리한다.
- Fixture는 자신이 만든 DB·UUID 버킷만 생성·정리한다. 사용자 데이터를 삭제하거나 Iceberg Tag를 임의 정리하지 않는다.
- 필수 테스트의 실패를 Retry·Skip으로 숨기지 않는다. 최초 실패와 재현 정보를 보존한다.
- 검증 수준마다 책임을 정하고 불필요한 Mock과 복잡한 테스트 DSL을 피한다.

## 계층과 실행 정책

| 테스트 계층 | 위치 | 실행 |
| --- | --- | --- |
| 순수 로직·CLI·호환성 | tests/test_processing.py, tests/test_dev_cli.py, tests/test_api_compat.py, tests/test_errors.py, tests/test_diagnostics.py, tests/test_log_capture.py, tests/test_e2e_isolation.py | make check-fast; PR 필수 |
| 실제 DB·객체 저장소·Catalog | tests/integration/ | PR 필수 Python 작업 |
| 브라우저 사용자 흐름·RBAC | web/e2e/ | PR 필수 UI 작업 |
| Make 재시작·보존 | CI Make lifecycle | PR 필수; 자체 데이터를 생성해야 함 |
| 장시간 부하·복합 장애 | 별도 확장 검사 | 의미 있는 한도가 마련되면 정기 실행 |

make check-fast는 Ruff, 순수 Python 테스트, Vitest 및 웹 타입·빌드를 확인하며 서비스 기동을 요구하지 않는다.
make check는 기존 전체 검증 명령으로 실제 Ready 상태가 필요하다. Fast 통과를 전체 검증 성공으로 해석하지 않는다.

## 변경 불가능한 필수 불변조건과 담당 테스트

| 검증 내용 | 테스트 |
| --- | --- |
| 원본 파일의 판본·등록·마이그레이션 보존 | test_datasets.py |
| DuckDB 탐색·집계·권한과 파일 접근, NaN·무한대·DECIMAL·큰 정수·바이너리 Preview·전체 Job 직렬화와 그룹 상위 제한·구조화 Query | test_discovery.py |
| Job 상태·취소·게시 원자성·Attempt·커서 | test_jobs.py |
| Job 제출 키 중복·동시성·감사 롤백 | test_idempotency.py |
| 전역 Discovery 슬롯 경합과 회수 | test_discovery_admission.py |
| Worker 실행 시간 제한·실패 기록 | test_worker_budget.py |
| Dataset 키셋 페이지와 Snapshot 소유권 | test_dataset_pages.py, test_snapshot_scope.py |
| Iceberg Snapshot 고정·Tag 보존·중단 후 자동 재시도 금지·대용량 비파티션 단일 Snapshot | test_iceberg.py |
| 객체 쓰기 의도·S3 관측·삭제 없는 Storage Health | test_storage.py |
| 토큰·역할·리소스 소유권·거부 감사 | test_security.py, auth.spec.ts |
| Worker 중단·복구 감사·Attempt 식별 | test_recovery.py |
| 테스트 준비 단계 실패 시 DB/버킷 정리 | test_environment.py, conftest.py |
| 데이터→EDA→Feature→Job·브라우저 접근성, 재시도·문자형 집계·전체 결과 이해 | workspace.spec.ts |
| OpenAPI 삭제·필수 입력 추가·필드 변경 위험 | test_api_compat.py와 PR 비교 검사 |

## 탐색 후보와 상태 소유권 회귀 계약

- 탐색 대상 요약은 실제 컬럼·행 수 값을 표시하며 JSX 템플릿 문자열이나 코드 조각을 노출하지 않는다.
- Dataset 새로고침 중 이전 요청의 Feature 미리보기가 늦게 응답해도 재조회 세대가 다른 결과를 표시하지 않는다.
- 탐색 조건이 현재 스키마·연산과 불일치할 때는 복구 가능한 기본 조건으로 초기화할 수 있다.
- 네트워크 예외에는 내부 URL·브라우저 예외 원문을 노출하지 않고 안정적인 사용자 안내를 제공한다.

- 후보 선택은 계산 요청을 발생시키지 않으며, 실제 Explorer 조건을 채우고 사용자가 편집·실행해야 한다.
- 직접 작성한 조건과 프로파일에서 시작한 조건은 동일한 Exploration API로 실행한다. 입력 Dataset과 판본이 바뀌면 이전 후보를 재사용하지 않는다.
- Dataset Overview는 공통 조회 상태에서 공유하고, Preview/EDA Profile은 필요한 탭에서만 요청하며 응답의 Dataset·재조회 판본을 검증한다.
- 조건을 편집한 뒤 탭을 왕복해도 입력이 유지되고, 다른 Dataset을 선택하면 이전 조건을 적용하지 않는다.

## Data Explorer UI·인스턴스 설정 회귀 계약

- UI 디자인 토큰은 텍스트·보더·버튼·포커스·차트 색상의 의미를 통일하며, 키보드 탭 이동·반응형 폭에서 기본 탐색을 유지한다.
- 개요·데이터 조회·탐색·변환 탭, Dataset 업로드 후 기본 개요 진입, 비활성 탭 Preview/EDA 요청 제한 및 조회 재사용을 실제 브라우저로 확인한다.
- 인스턴스 표시명은 관리자 권한, 서버 영속성, 재시작 후 유지, 감사 기록, 비관리자 변경 거부를 DB·토큰 통합 테스트와 UI로 확인한다.
- 스타일 변경이 기존 Dataset·Job·Iceberg·Feature 계약과 역할별 접근 제어를 약화시키지 않도록 회귀한다.

## Dataset Understanding 회귀 계약

결정적인 CSV·Parquet Fixture로 미지의 컬럼명, NULL·NaN·무한대, 상수·고유값이 큰 컬럼, 단일 행·빈 행, 역할 후보의 미확정 상태와 실행 가능한 탐색 추천을 검증한다. Profile 판본·영속화·마이그레이션·취소·권한과 Job/Attempt 정합성 및 브라우저 첫 사용 흐름을 함께 검사한다. 표본 계산을 전체 통계로 오인하지 않게 하고 민감한 범주 값이 자동 결과에 포함되지 않도록 한다.

## CI의 품질 게이트

- python-tests: 잠금·Ruff·API 타입·PR base API 호환성·실제 인프라 통합 테스트·Alembic·Wheel.
- frontend: TypeScript/Vite 빌드 및 Vitest.
- ui-e2e: Make up/down/restart, Data Workspace 브라우저 시나리오, 토큰 역할 3개, 독립 데이터 보존 검사. 정확한 시나리오 개수와 성패는 각 CI 실행 기록을 기준으로 한다.
- python-quality: 위 세 작업이 모두 success일 때만 통과하는 보호된 Status Check; 이름은 변경하지 않는다.
- 오래된 PR HEAD만 취소하고 main 검증 결과는 각 실행 ID별로 보존한다.
- 정기(매일 새벽)·수동 실행에서는 핵심 Iceberg/Job 모듈을 순서를 바꿔 두 번 반복해 격리·재현성을 점검한다. 실패는 일반 CI 실패로 남으며 Retry에 의해 무시되지 않는다.
- API 호환성 검사는 이전 base 코드(의존성 잠금 변경 시 base 자체 환경)와 현재 코드에서 각각 생성한 OpenAPI를 비교한다. 요청과 응답의 호환성 방향을 분리하고 Nullable/배열/$ref를 검사한다. 보수적 가드이지 완전한 JSON Schema 판정기는 아니다.

## 실패 진단과 안정성 유지

1. 실패한 SHA·테스트명·단계·실행 시간·처음 실패한 입력을 기록하고 제품 결함/테스트 결함/환경 장애로 분류한다.
2. Pytest·Playwright의 실패 리포트와 Trace를 사용하되 토큰·자격 증명·실제 사용자 데이터를 아티팩트에 노출하지 않는다.
3. Playwright는 기본적으로 재시도 0, 테스트별 독립 Context, 실패 Trace 보존을 따른다. Token 테스트가 CI에서 건너뛰면 실패로 처리한다.
4. GitHub Actions 지난 30~50회 기준으로 전체 CI p50·p95, 단계별 실행 시간, 취소율, Flaky 건수와 재실행 비용을 검토한다. 별도 계측 인프라는 필요할 때만 도입한다.
5. 신규 도구나 Nightly 복합 검사는 결함 탐지 가치·실행 비용·오탐률·유지보수 비용을 비교한 뒤 추가한다.

## 오류·진단 회귀 계약

- Error Catalog 코드 중복·미등록·CLI 코드 정합성, HTTP 레거시 `detail`·추적 헤더·보안 비노출을 단위·통합으로 검사한다.
- Worker의 실패 코드·단계 및 Job/Attempt 원자성, Iceberg 커밋 불명 시 자동 재적재 금지, 이전 스키마 레코드 조회를 실제 DB에서 확인한다.
- 브라우저에서 오류 코드·Request ID와 안전한 다음 행동을 노출하되 Stack Trace나 비밀정보를 표시하지 않는다.
- 재시도로 실패를 숨기지 않고, DB·Catalog 판정이 어긋날 때 불확실성을 유지한다.

## PR 완료 조건

계약·검증 계층이 명확하고, 테스트 독립성·실패 안전 정리가 확인되고, 필수 검사와 API 호환성 평가를 통과해야 한다. 최종 HEAD의 CI를 확인하고 개발자가 직접 PR을 병합한다.
