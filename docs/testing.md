# 테스트·검증·CI 운영 기준

이 문서는 SOS의 테스트·CI 체계 정본이다. 데이터 및 API의 의미는 Contracts, Workflows와 실제 구현이 정본이며, 이 문서는 중복 명세를 만들지 않는다.

검증 실행의 CI 독립성·공통 결과·테스트 수명주기의 책임 경계는 [ADR 0009](adr/0009-test-engineering-portability.md)에 기록한다. 실제 지원·검증 범위는 아래 실행 정책과 CI 결과를 기준으로 확인한다.

## 기본 원칙

- 사용자·데이터·실행 불변조건을 정의한 뒤 그 계약을 제대로 검사할 수 있는 가장 저렴한 테스트 계층을 선택한다.
- 모든 테스트는 단독·순서 변경·반복 실행이 가능해야 한다. 다른 테스트에서 생성한 데이터를 요구하지 않는다.
- 검증 강도는 실행 시간의 길이가 아니라 자원 한계, 동시 경합, 실패 주입 지점, 상태 불변조건과 정리·복구 결과로 판단한다. TTL·Timeout 등 시간 자체가 계약인 경우에만 필요한 시간 범위를 명시해 검증한다.
- `make check`의 브라우저 쓰기 테스트는 UUID 별도 Compose 프로젝트와 DB·버킷·서비스에만 연결한다. 일반 개발 프로젝트/외부 저장소에 쓰기 E2E를 실행하지 않는다. 생성한 E2E 프로젝트만 명시적으로 종료·볼륨 정리한다.
- SIGKILL·호스트 종료 등으로 정리가 중단된 경우, `.sos-test/runs/<sos-e2e-인스턴스>/validation-owner.json`의 소유 기록을 먼저 확인하고 `python scripts/isolated_compose.py --recover-owned <sos-e2e-인스턴스>`를 실행한다. 복구는 기록된 프로젝트에 한정되며, 서비스 종료·Compose·테스트 고유 이미지 정리에 실패하면 소유 기록을 보존하고 다른 데이터·볼륨·공유 이미지를 임의 삭제하지 않는다.
- Fixture는 자신이 만든 DB·UUID 버킷만 생성·정리한다. 사용자 데이터를 삭제하거나 Iceberg Tag를 임의 정리하지 않는다.
- 필수 테스트의 실패를 Retry·Skip으로 숨기지 않는다. 최초 실패와 재현 정보를 보존한다.
- 검증 수준마다 책임을 정하고 불필요한 Mock과 복잡한 테스트 DSL을 피한다.

## 계층과 실행 정책

| 테스트 계층 | 위치 | 실행 |
| --- | --- | --- |
| 순수 로직·CLI·호환성 | tests/test_processing.py, tests/test_api_compat.py, tests/test_pagination.py, tests/test_execution_budget.py, tests/test_resource_admission.py, tests/test_errors.py, tests/test_diagnostics.py, tests/test_e2e_isolation.py | make check-fast; PR 필수 |
| 실제 DB·객체 저장소·Catalog | tests/integration/ | PR 필수 Python 작업 |
| 브라우저 사용자 흐름·RBAC | web/e2e/ | PR 필수 UI 작업 |
| 전체 컨테이너 재시작·보존 | CI container-runtime | PR 필수; 자체 데이터를 생성해야 함 |
| 자원 한계·경합·복합 장애 | 해당 불변조건의 단위·통합·장애 주입 테스트 | 소유권·실패 시점·복구 결과와 자원 예산을 기준으로 필요한 범위에서 실행 |

make check-fast는 Ruff, 순수 Python 테스트(개발 설정 기본값·문서 ADR 인덱스 검사 포함), Vitest 및 웹 타입·빌드를 확인하며 서비스 기동을 요구하지 않는다.
make check는 실제 컨테이너 Ready 상태가 필요하다. 공통 Suite를 통해 Ruff·단위·통합·Frontend·생성 OpenAPI 타입·Wheel·Alembic·격리 Workspace/권한 E2E·컨테이너 영속성을 포함하며, PR base 비교는 별도로 --base-ref를 전달해야 한다. Fast 통과를 전체 검증 성공으로 해석하지 않는다.

## 변경 불가능한 필수 불변조건과 담당 테스트

| 검증 내용 | 테스트 |
| --- | --- |
| 원본 파일의 판본·등록·마이그레이션 보존 | test_datasets.py |
| 파일 포맷 Reader 등록·안전한 캐시 허용 목록·CSV/Parquet 호환·DuckDB/Polars 해석 정합성 | test_file_formats.py |
| DuckDB 탐색·집계·권한과 파일 접근, NaN·무한대·DECIMAL·큰 정수·바이너리 Preview·전체 Job 직렬화와 그룹 상위 제한·구조화 Query | test_discovery.py |
| Job 상태·취소·게시 원자성·Attempt·커서 | test_jobs.py |
| Job 제출 키 중복·동시성·감사 롤백 | test_idempotency.py |
| 전역 Discovery 슬롯 경합과 회수 | test_discovery_admission.py |
| Worker·Discovery 공통 메모리 수용과 예약 회수, 설정의 포함 관계·서버 오류 분리 | test_resource_admission.py (unit·integration) |
| Worker 시간·메모리 제한, 필수 cgroup 위임 실패 시 Claim 거부·자식 격리 계약 | test_worker_budget.py, test_cgroup.py |
| Dataset 키셋 페이지와 Snapshot 읽기 소유권 | test_dataset_pages.py, test_snapshot_scope.py |
| Iceberg 쓰기 Owner Token·Table UUID 재생성 차단·예약 중단 복구·멱등성 이름 선점 차단·API/Worker 재검증·기존 소유권 불명 테이블 쓰기 거부 | test_iceberg_write_scope.py |
| Iceberg Snapshot 고정·Tag 보존·외부 커밋/Tag ACK 유실, 독립 Writer 동시 Commit 경합·중단 후 자동 재시도 금지·대용량 비파티션 단일 Snapshot | test_iceberg.py |
| 객체 쓰기 의도·S3 관측·삭제 없는 Storage Health | test_storage.py |
| 토큰·역할·리소스 소유권·거부 감사 | test_security.py, auth.spec.ts |
| Worker 중단·복구 감사·Attempt 식별 및 실제 Job 부모 SIGKILL 후 자식 종료·복구 | test_recovery.py, worker_kill_helper.py |
| 테스트 준비 단계 실패 시 DB/버킷 정리 | test_environment.py, conftest.py |
| 데이터→EDA→Feature→Job·브라우저 접근성, 재시도·문자형 집계·전체 결과 이해 | workspace.spec.ts |
| OpenAPI 삭제·필수 입력 추가·필드 변경 위험 | test_api_compat.py와 PR 비교 검사 |

## Understanding 완료 응답의 읽기 일관성

- Worker의 완료 트랜잭션과 API GET이 교차해도 `job_status=Succeeded`와 `profile=null`을 동시에 확정 결과로 반환하지 않는다.
- UI는 결손 완료 상태를 제한적으로 재조회하고, 지속되는 결손은 명시적인 복구 경로로 표시한다.
- 등록 성공 이후 백그라운드 Dataset 목록 보강 실패는 원래 등록 작업의 실패로 표시하지 않는다.
- Jobs 자동 폴링이 존재할 때 테스트는 정확한 요청 수가 아니라 최신 응답·상태의 우선순위를 검증한다.

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
- Explorer의 데이터 이해·데이터 조회·탐색 탭과 별도 Data Preparation 작업 영역, Dataset 업로드 후 기본 이해 진입, 비활성 탭 Preview/EDA 요청 제한 및 조회 재사용을 실제 브라우저로 확인한다. 두 영역을 오가도 같은 Dataset의 변환 초안이 유지되고 다른 Dataset의 결과가 섞이지 않아야 한다. Preparation에서 새 Dataset을 등록해도 자동으로 Explorer로 이동시키지 않는다. 데이터가 없는 첫 진입에서도 Preparation 메뉴는 열려 있어야 한다. 새 Dataset 등록만으로 Understanding Job을 생성하지 않으며, 명시적 실행 후에는 전체 Jobs에서 상태를 확인한다.
- Understanding 계산 완료 전후 원본 Dataset 규모·파일 크기(해당 시)·판본을 일관되게 유지하고, 실제 분석 행 수·표본 방법을 독립적으로 표시한다. `not_computed`를 CSV 고유 상태로 단정하지 않는다. 넓은 데이터 조회는 기본 12개 컬럼 표시와 전체 표시를 전환할 수 있고, 명시적 컬럼 투영에서는 모든 선택 컬럼을 표시한다. 반환 행 수를 전체 일치 건수로 표현하지 않는다. 조회 조건을 수정해도 마지막 적용 결과는 명확히 구분해 보존하고, 탭 왕복 후에도 편집 중인 조건을 유지하며, 다른 Dataset에 재사용하지 않는다. 읽기 전용 권한에는 예제 등록 동작을 실행 가능하게 보여주지 않는다. 값·축을 선택하기 전에 행 순서 차트를 임의로 그리지 않는다. 데이터 이해의 Overview 조회 실패와 Preparation의 원본 Preview 실패는 정상 프로파일 여부와 무관하게 재시도할 수 있다. 운영 화면에는 해당 영역의 헤더가 표시된다.
- 인스턴스 표시명은 관리자 권한, 서버 영속성, 재시작 후 유지, 감사 기록, 비관리자 변경 거부를 DB·토큰 통합 테스트와 UI로 확인한다.
- 스타일 변경이 기존 Dataset·Job·Iceberg·Feature 계약과 역할별 접근 제어를 약화시키지 않도록 회귀한다.

## Dataset Understanding 회귀 계약

기존 통합·브라우저 검증을 우선 활용해 부분/전체 분석 범위·정렬 없는 제한 조회·Reservoir 방식·실제 분석 행 수·근사 고유값·그룹 표시 제한의 의미가 결과에서 유지되는지 확인한다. 단순 표시 문구마다 신규 E2E를 만들지 않는다. 결정적인 CSV·Parquet Fixture로 미지의 컬럼명, NULL·NaN·무한대, 상수·고유값이 큰 컬럼, 단일 행·빈 행, 역할 후보의 미확정 상태와 실행 가능한 탐색 추천을 검증한다. Profile 판본·영속화·마이그레이션·취소·권한과 Job/Attempt 정합성 및 브라우저 첫 사용 흐름을 함께 검사한다. 표본 계산을 전체 통계로 오인하지 않게 하고 민감한 범주 값이 자동 결과에 포함되지 않도록 한다.

## CI의 품질 게이트

- python-tests: 잠금·Markdown/ADR 인덱스·Ruff·API 타입·PR base API 호환성·전체 Python 테스트(로컬 check-fast 명령 계약 포함)·실제 인프라 통합 테스트·Alembic·Wheel. 로컬 check-fast 전체 명령은 다른 필수 CI 단계와 중복 실행하지 않는다.
- frontend: TypeScript/Vite 빌드 및 Vitest.
- ui-e2e: 고유 Docker Compose 프로젝트에서 실행되는 Data Workspace 브라우저 시나리오, 토큰 역할 3개 및 독립 테스트 자원 정리 검사. 정확한 시나리오 개수와 성패는 각 CI 실행 기록을 기준으로 한다.
- container-runtime: 고유 Compose 프로젝트에서 공개 Make up/status/doctor/restart/down, Token 역할/소유권, Web 프록시, Job·Iceberg, 재기동 후 데이터·Snapshot 보존 검증. 공통 Suite의 Manifest/Summary를 CI 증거로 보존한다.
- python-quality: Python·Frontend·UI E2E 및 container-runtime이 모두 success일 때만 통과하는 보호된 Status Check; 이름은 변경하지 않는다.
- 오래된 PR HEAD만 취소하고 main 검증 결과는 각 실행 ID별로 보존한다.
- 정기(매일 새벽)·수동 실행에서는 핵심 Iceberg/Job 모듈을 순서를 바꿔 두 번 반복해 격리·재현성을 점검한다. 실패는 일반 CI 실패로 남으며 Retry에 의해 무시되지 않는다.
- PR의 OpenAPI 후방 호환성 검사는 기준 브랜치와 변경 브랜치를 직접 비교하며, 과거 PR 전용 Breaking 승인 예외를 적용하지 않는다.
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

## 독립적인 연산 기준선

`uv run --locked python scripts/benchmark_processing.py --rows 150000 --groups 32 --scenario window`와 `--scenario row_local`을 각각 독립 프로세스로 실행해 같은 합성 입력 생성 규칙에서 **실제 SOS Polars Transform**의 경과 시간·출력 크기·Linux RSS 관측치를 JSON으로 확인한다. 숫자 비교 시 환경·CPU·메모리·Polars 버전·시나리오·행/그룹 수를 함께 기록한다. `VmHWM`은 프로세스 생애 고수위이므로 단일 연산에만 귀속되는 엄격한 Peak RSS가 아니며, 파일 I/O·Iceberg 커밋·여러 동시 실행·cgroup 제한도 검증하지 않는다. 운영 환경의 성능 보장으로 사용하지 않는다.

## 검증 실행 경계

`scripts/verify.py`의 `list`, `run <suite> --output-dir <경로>`, `inventory --output <경로>`, `reconcile --inventory <경로> --pytest-report ... --vitest-report ... --playwright-report ...`는 로컬과 CI가 공통으로 호출하는 인터페이스다. PR 기준 API 호환성 검사에는 `--base-ref`를 지정한다. `make check-fast`는 인프라 없이, `make check`는 필요한 인프라를 준비한 상태에서 전체 검증을 수행한다.

공통 검증 메커니즘은 Native Runner 실행 결과·JUnit·Manifest/Summary·누락 또는 미실행 판정을 담당한다. Runner별 수집 명령·실행 경로·테스트 출처 경로는 `scripts/inventory_scope.json`에서 관리하고, 실제 SOS 도메인 시나리오는 기존 테스트와 격리 실행기가 소유한다. 다른 프로젝트에서는 Runner 설정과 Suite 구성, 보호 계약만 변경하도록 경계를 유지한다. 범용 테스트 프레임워크나 별도 서비스는 만들지 않는다.

JUnit 리포트 손상·누락, 실행 0건, 필수 Skip과 실패는 성공 처리하지 않는다. 실행 증거에 토큰·환경변수 값을 기록하지 않으며 실패 로그·Trace도 비밀정보 비노출 정책을 따른다. GitHub 보호 게이트 `python-quality`는 Python·Frontend·E2E 작업과 전체 JUnit Test ID 대조의 성공을 요구한다.

## 테스트 자산 수명주기

**Standard by Default, Protected by Exception:** 보호 계약에 연결되지 않은 모든 테스트는 별도 등록 없이 `standard`다. 데이터 보존·권한·소유권·상태 확정·복구 등 특별한 변경 통제가 필요한 계약에 한해 `tests/contracts.json`에 `id`, `reason`, `selectors`를 등록한다. 위험도 점수·전수 품질 분류·테스트 관리 DB는 운영하지 않는다.

- `python scripts/verify.py inventory --output <경로>`는 pytest·Vitest·Playwright의 실제 테스트 ID를 수집하고, `schema_version: 1` JSON에 `policy`(`standard` 또는 `protected`)와 `contract_ids`를 기록한다. 수집 명령 및 테스트 경로는 `scripts/inventory_scope.json`이 정의한다.
- 모든 테스트는 정책과 무관하게 CI에서 실행하고 Native 목록과 JUnit Test ID를 일대일로 대조한다. 누락·추가·중복은 실패한다.
- Protected 선택자는 파일 전체가 아닌 **구체적인 테스트 이름**을 지정한다. 동일 파일의 다른 테스트는 기본 Standard이며, 선택자가 실제 Native 테스트를 가리키지 못하면 CI가 실패한다.
- Protected 테스트를 수정하는 것 자체에 별도 승인 절차는 없다. 삭제·통합·보호 Assertion 제거 시 보호 계약과 실패 경로의 대체 검증 근거를 일반 PR 리뷰에서 확인한다. Git 이력과 코드 리뷰가 변경 근거의 정본이므로 테스트 ID 이력 추론이나 별도 승인 워크플로를 운영하지 않는다.

추가 CI 제공자는 실제 필요가 생겼을 때 어댑터를 구성하고 독립 실행 및 실패 경로를 검증한다. 제공자 독립적인 설계 자체가 특정 CI 서비스의 실제 지원을 의미하지는 않는다.
