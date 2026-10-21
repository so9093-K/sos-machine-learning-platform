# Changelog

## Unreleased

- Isolation Forest의 신규 Sample 입력은 디스크 기반 중복 관측 집계와 결정적 Bottom-K를 사용해 전체 Feature 정렬 없이 생성합니다. 기존 Sampling 알고리즘과 Job/Fork의 실행 의미는 유지하며 신규 버전과 실제 입력 지문을 모델 이력에 보존합니다.

- Isolation Forest Training의 고정 행·Feature 한도를 제거하고 전체 사용·명시적 부분집합 정책과 실행 메모리 수용 판단을 분리합니다. 부분집합은 중복 관측 수를 유지하며 스캔 순서와 무관하게 선택하고, 입력 정책·선택 알고리즘 버전·Seed·입력 지문을 기록합니다. Scoring의 현행 처리 범위는 유지합니다.

- Policy·Evaluation·Explanation Job의 취소와 실패 상태 안내, Scoring 이전 이력 탐색을 연결하고, 학습 데이터 재점수화 결과를 독립 검증 성능으로 표시하지 않도록 보완했습니다.

- 선택적 MLflow REST Tracking을 Worker 전용 Outbox와 별도 상태로 추가하고, 실패 재시도·재시작 복구·외부 Run 연결 상태를 표시한다. 같은 모델 설정으로 Fork·Reproduce 하는 사용자 동작을 제공한다.

- Dataset 기반 Isolation Forest 비동기 학습과 불변 모델 판본, 독립 Scoring·Decision Policy·Evaluation·Explanation 결과 및 이력 조회, 학습 설정 비교를 추가했습니다. 학습 점수와 독립 평가 결과는 구별하며, 평가 지표의 데이터 선택 한계를 표시합니다.

- API·Worker 공통 Python 이미지, Web 개발 이미지와 Docker Compose 단일 구성(`app` 프로필)을 추가했습니다. 공개 `make up`은 컨테이너 실행으로 통합하고, 호스트 실행기와 로그 릴레이를 제거하고 브라우저 E2E도 고유 Compose 프로젝트에서 실행합니다. Token 인증을 요구하고 데이터 볼륨은 보존합니다.

- `make up`은 개발 기본값을 유지하고 API를 직접 실행할 때는 인증 모드를 명시해야 합니다. 실행 서버는 `sos_platform.api:create_app --factory`를 사용해 앱 import와 기동을 구별하며, OpenAPI 생성과 테스트 수집이 전역 인증 모드를 설정하지 않도록 분리했습니다. 기존 API가 실행 중인 상태에서 인증 모드·토큰 목록이 바뀌면 `make up`은 불일치를 알리고 `make restart`를 안내합니다. 기존 Token RBAC·Dataset/Job 소유권과 API 계약은 유지합니다.

- 헤더 유무를 확인하는 탭 구분 TSV/TXT 등록을 지원하고 원본 SHA-256과 Dataset별 해석 설정을 보존합니다. 파일 포맷 Reader를 공통 진입점에 모아 CSV·Parquet 기존 동작을 유지하면서 DuckDB·Polars·캐시의 확장자별 분기 중복을 줄였습니다. 헤더 없는 입력은 중립적인 `unnamed_*` 컬럼명으로 해석하며, 무의미한 자기 비교 자동 분석 예시는 생성하지 않습니다.

- Dataset 등록 직후 원본 개요를 우선 보여주고 기본 표본 프로파일은 사용자 요청 시 Job으로 실행하도록 했습니다. 해당 Job을 전역 Jobs 목록과 연결하고, 조회 조건의 미적용 상태·마지막 적용 결과·탭 왕복 편집 상태를 구별했습니다. Dataset 없이 Data Preparation 진입, 권한별 예제 등록 제어, 기존 Dataset 전환 시 작업 탭 유지를 보완했습니다.

- Data Explorer·Jobs 결과에 범위와 분석 근거의 요약·상세 보기를 통일하고, 제한 조회·Reservoir 표본·전체 대상 계산, 근사 통계 및 그룹 표시 제한을 구별했습니다. Data Preparation의 변환 결과 미리보기가 입력 표본 추출로 오해되지 않도록 표시를 정리했습니다.

- Data Explorer의 데이터 이해·조회·탐색을 독립 경험으로 정리하고, 기존 Feature 변환과 Iceberg 적재를 별도 Data Preparation 작업 영역에서 이용할 수 있게 했습니다. 기존 Dataset·Job·Transform API는 유지합니다.
- 원본 Dataset 규모와 프로파일 분석 범위를 함께 표시하고, 넓은 테이블의 기본 12개 컬럼 보기 및 전체 표시 전환, 반환 행과 전체 일치 건수 구분, 선택 전 무의미한 변화 차트 방지, 축 값 정렬이 아닌 조회 순서 표현을 추가했습니다. 명시적 조회 컬럼을 모두 표시하며 Overview·Preparation 미리보기 오류의 복구 경로와 운영 화면의 영역 표시를 보완했습니다.

- 파일 등록 HTTP 201이 도착하면 목록 갱신을 기다리지 않고 완료를 안내합니다. 이후 목록 조회 오류는 다른 사용자 작업의 알림이나 사용자가 닫은 알림을 다시 덮지 않습니다.
- 사전 출시용 OpenAPI Breaking 승인 파일 옵션을 제거했습니다. API 호환성 검사는 계속 모든 Breaking 변경을 거부합니다.

- Iceberg Job의 테이블명 예약을 PostgreSQL 멱등성 검사 및 Job 등록과 한 트랜잭션으로 묶어, 키 충돌이나 등록 실패로 쓰지 않을 테이블명이 남지 않도록 수정했습니다.


- Iceberg 쓰기 권한에 Catalog의 물리 Table UUID를 고정해 같은 이름·토큰으로 재생성된 테이블에 대한 잘못된 Append를 거부합니다. 예약 후 Catalog 생성과 DB 확정 사이의 중단은 동일 토큰과 UUID를 검증해 복구합니다.


- 쓰기 소유권이 없는 기존 Iceberg 테이블을 관리자 도구로 채택하는 과도기 경로를 제거했습니다. 기존 Dataset·Snapshot·Tag는 유지하고 쓰기는 계속 거부하며, 필요한 데이터는 새로운 소유 테이블명으로 별도 적재합니다.


- Iceberg 테이블에 대한 쓰기 권한을 Snapshot 참조 소유권에서 분리하고, API/Worker에서 테이블 소유자와 Catalog 소유 토큰을 검증합니다. 다른 사용자의 테이블 Append와 소유권 불명 레거시 테이블의 새 적재를 거부하며 기존 읽기·보존은 유지합니다.
- 파일 등록 HTTP 201 이후 Dataset 목록 조회가 실패하거나 최신 요청에 의해 대체돼도 등록 성공을 잘못 취소하거나 갱신 성공으로 표시하지 않도록 안내 상태를 구분합니다.


- Worker 재시작 시 완료된 Attempt의 잔여 cgroup도 소유권 확인 후 정리하고, 소유자가 없는 cgroup은 자동 삭제하지 않고 실행을 거부합니다. Job 상세 조회가 실패하면 펼친 패널에서 명시적으로 다시 시도할 수 있습니다.

- Linux cgroup v2의 위임된 `memory` 컨트롤러를 사용한 Worker 자식 메모리 격리 필수 모드(`SOS_WORKER_CGROUP_MODE=required`)를 추가했습니다. 사전 설정·쓰기 검사에 실패하면 Job을 Claim하지 않으며, 자식마다 `memory.max`·스왑 차단·OOM 그룹 종료를 설정합니다. Worker 재시작 시 이전 Attempt cgroup이 비어 있지 않으면 Job 복구를 거부합니다. 기본 모드는 `off`이며 CPU·디스크 격리나 다중 노드 자원 배치를 보장하지 않습니다.

- Iceberg 실행 중 Attempt별 임시 디스크 사용량 관측에 하위 디렉터리의 정규 파일과 Spill 파일을 포함하고, 다른 Attempt 및 symlink 대상은 제외합니다. 폴링 감시이며 실제 파일시스템 쿼터를 제공하지는 않습니다.

- Worker Job 최종 확정 시 실행 Attempt 식별자를 검증하고, PostgreSQL advisory lock 소유 세션에서 결과·Dataset·감사 트랜잭션을 확정하여 소유권 상실·이전 Attempt의 늦은 결과가 새 실행을 덮어쓰는 위험을 방어합니다.

- 실행 중 펼쳐 둔 Job/Attempt 상세가 최신 목록의 확정 상태보다 오래되면 상세를 재조회하고, 동일 목록 판본에 대한 반복 요청을 제한합니다.

- 사전 출시 단계에서 사용되지 않는 API 별칭과 무제한 Dataset 목록을 제거하고 Dataset 목록을 키셋 페이지 계약으로 단일화했습니다. 예제 중복 등록은 전체 목록 탐색 대신 판본·이름 조건 조회로 처리합니다.
- 감사·객체·Snapshot·Dataset·Job/Attempt 저장 책임을 모듈로 분리하고, Window 검증·Job 목록 관리·API 페이지 커서의 중복을 정리했습니다.
- Worker의 Attempt별 로컬 중간 결과 격리와 공통 논리 실행 메모리 수용 정책을 추가했습니다. 동일 DB의 활성 풀 설정 불일치를 거부하고 Linux에서 부모 강제 종료 시 자식도 종료되도록 보호합니다. 자원 예약은 물리 RAM 격리가 아니며, 운영 환경별 실제 부하 검증이 필요합니다.
- 불변 Dataset 캐시의 파일시스템 정체성 기반 SHA-256 검증 결과를 프로세스 내에서 제한적으로 재사용하고, 수정된 파일은 다시 검증합니다.

- Understanding 완료 직후 Profile 누락 상태가 표시되는 읽기 경합을 수정하고, 비동기 목록 보강 오류가 성공한 데이터 등록을 실패로 오해시키지 않도록 개선했습니다.

- 탐색 화면의 데이터 요약 렌더링 오류와 유효하지 않은 탐색 초안의 복구를 수정하고, Dataset 새로고침 시 이전 변환·행 조회 결과의 노출을 차단했습니다.

- Dataset Understanding의 탐색 후보를 사용자 확인·수정 가능한 탐색 조건으로 연결하고, 중복된 개요 내부 탐색 실행을 제거했습니다. 기존 Exploration API 및 전체 집계 Job 계약은 유지합니다.

- Data Explorer의 웜 차콜 디자인 토큰·명사형 탐색 구조를 적용하고, 브랜드·인스턴스·환경·계정 표시를 분리했습니다.
- 관리자 전용 인스턴스 표시명 설정과 변경 감사·재시작 후 영속성을 추가했습니다.
- 활성 탭 기준 Preview·EDA 요청 지연 및 결과 재사용, 반응형·키보드 인터랙션 회귀 검증을 추가했습니다.

- 처음 보는 CSV·Parquet Dataset을 위한 판본별 Understanding Profile, 역할 후보, 실행 가능한 탐색 추천 및 UI를 추가했습니다.

### Changed

- Dataset의 구조화된 컬럼 투영·필터·정렬 조회와 웹 컬럼 선택·탐색을 추가했다. 잘못된 비교 타입은 422로 처리하고 기존 Preview API를 보존한다.
- Feature Builder에서 컬럼 선택 단계와 복수 그룹 키를 설정할 수 있다.
- 비파티션 Iceberg 입력은 Parquet 스테이징·RecordBatchReader로 처리하여 5만 행 전체 메모리 제한 없이 하나의 Snapshot을 커밋한다. 기존 파티션 테이블은 소규모 호환 경로를 유지한다.

- Discovery의 DECIMAL·큰 정수 정밀도, 바이너리·시간 타입 JSON 표현 및 NaN 집계 정렬을 샘플·전체 Job에 걸쳐 일관화하고, NULL 그룹/전체 그룹 구분과 정확한 값 표시를 개선했다.
- Discovery의 NaN·무한대 Preview 및 JSON 집계 의미를 명확히 하고, 메타데이터 재시도·범주형 집계 선택·전체 Job 결과 표와 공통 차트 구현을 보완했다. 등록 성공과 후속 목록 조회 실패를 구분하고 사용자 복구·오류 안내를 개선했다.
- DuckDB를 Dataset Preview·Profile·Discovery 탐색의 실행기로 도입하고 Polars의 Feature·Transform 책임과 분리했다. 샘플 탐색·전체 분석 Job을 분리하고 기존 Job API 호환성을 보존했다.
- CSV·Parquet 파일을 메모리 전체 복사 없이 등록하고 업로드 진행·오류 안내 및 캐시 무결성 게시를 보강했다.
- 파일 업로드·인증 거부 감사의 동기 I/O를 비차단 처리하고 해당 동시성·브라우저 회귀 검증을 보강했다.
- API 오류 코드·Request ID·구조화된 `detail` 응답, Worker/Attempt 영속 진단과 구조화 로그를 연결하고 안전한 Iceberg 복구 안내를 제공한다.
- Iceberg Dataset 생성·적재와 기존 Snapshot의 보존 관리 권한을 구분하고 역할별 UI를 일치시켰다.
- Worker 복구 감사에 Attempt 식별자·복구 결과를 기록하고, 감사 실패 시 DB 상태 변경을 롤백하도록 검증했다.
- 통합 테스트가 준비 도중 실패해도 독립 DB·버킷이 남지 않도록 정리 수명주기를 보강했다.

- 통합 테스트는 로컬 전용 독립 PostgreSQL·UUID S3 버킷을 사용해 사용자 데이터와 분리하고 테스트가 생성한 객체만 정리한다.
- 미게시 Iceberg 보존 의도를 Storage Health에서 우선 진단하고, Worker Job·Dataset 게시 감사를 동일 DB 트랜잭션에 연결한다.
- Worker 취소 조회와 heartbeat 주기를 분리하고 S3 객체 관측 SQL을 배치화하며, CI 중복 검증과 버전 경고를 줄인다.
- 역할별 브라우저 Token 인증·CORS와 리소스 소유권 기반 404 거부 감사를 검증한다.

- Iceberg Append Snapshot을 Attempt 속성으로 식별하고 Dataset별 영구 Tag로 보호한다. 기존 미보호 Snapshot은 진단·수동 보존 전환을 지원한다.
- 로컬 개발과 토큰 인증 모드, Actor·Action·Resource별 접근 제어, Dataset·Job 소유권 및 PostgreSQL 감사 이벤트를 추가한다. 공개용 OIDC 로그인과 자동 삭제는 제공하지 않는다.

- 원본 파일과 Transform 결과의 S3 게시 의도를 PostgreSQL에 선기록하고, 성공 참조와 동일 트랜잭션에서 확정한다. 결과 주소는 Attempt별 불변 경로로 변경하되 기존 Dataset 판본은 유지한다.
- 객체 보존 기본값·변경 출처, 제한된 S3 객체 대조, 반복 관측과 Iceberg Snapshot 참조 진단을 제공한다. 자동 삭제는 제공하지 않는다.
- Alembic 0002 스키마를 추가하고 현재 HEAD 기준 데이터베이스 호환성 검사를 적용한다.

- 관리형 Compose 실행에서 외부 데이터베이스·S3 연결 대상 혼입을 사전 차단하고, S3 기본 포트를 환경 설정과 일치시켰다.
- 원본 Dataset의 콘텐츠 해시 공유와 Attempt별 결과 키를 구분해 S3·DB 게시 실패의 안전한 보존·복구 이력을 추가했다.
- 시계열 Lag·Rolling의 NULL·중복 시간키를 검증하고 선행 캐스트·필터 적용 후 정렬하며, 동일 Dataset 재조회에서 사용자의 편집·차트 선택을 보존한다.
- Job 목록을 정렬키 커서 기반 페이지 응답으로 제한하고 이전 이력 탐색 기능을 추가했다. UI의 변환 단계 타입은 생성 OpenAPI 계약을 사용한다.

- Feature Preview는 Row-local 변환에서 입력 행 수와 독립적으로 Lazy 결과 최대 30행을 반환한다. 그룹 Lag·Rolling은 전역 의미를 보존하기 위해 동기 예산 초과 시 전체 Transform Job으로 안내한다.
- Feature Builder의 단계 수정·순서 변경·Lag·Rolling 값 조정, 편집 상태 보존·미리보기 일치 검증, 접근 가능한 파일 업로드와 Job 이력 탐색을 개선했다.
- 실행 중 Iceberg 적재는 외부 커밋 결과 불확실성을 피하기 위해 취소를 거부하고, 데이터 스키마·캐시 무결성 및 Job 조회를 개선했다.

### Added

- API 응답 모델/OpenAPI TypeScript 타입 생성과 정합성 검사, 그룹별 시계열 피처 및 선택형 합성 예제 데이터.
- Data Workspace: CSV·Parquet 업로드, Iceberg 스냅샷 연결, Polars EDA·피처 변환, Worker 기반 실행·취소 및 결과 재탐색, React UI와 Scalar 문서.
- PostgreSQL 메타데이터, Alembic 스키마 관리, S3 호환 객체 저장소에 데이터·결과 보관.
- `make up/down/restart/status/logs/doctor/check/help` 개발 명령과 9600번대 포트·서비스 준비 상태 진단.
