# Changelog

## Unreleased

### Changed

- Iceberg Append Snapshot을 Attempt 속성으로 식별하고 Dataset별 영구 Tag로 보호한다. 기존 미보호 Snapshot은 진단·수동 보존 전환을 지원한다.
- 로컬 개발과 토큰 인증 모드, Actor·Action·Resource별 접근 제어, Dataset·Job 소유권 및 PostgreSQL 감사 이벤트를 추가한다. 공개용 OIDC 로그인과 자동 삭제는 제공하지 않는다.

- 원본 파일과 Transform 결과의 S3 게시 의도를 PostgreSQL에 선기록하고, 성공 참조와 동일 트랜잭션에서 확정한다. 결과 주소는 Attempt별 불변 경로로 변경하되 기존 Dataset 판본은 유지한다.
- 객체 보존 기본값·변경 출처, 제한된 S3 객체 대조, 반복 관측과 Iceberg Snapshot 참조 진단을 제공한다. 자동 삭제는 제공하지 않는다.
- Alembic 0002 스키마를 추가하고 현재 HEAD 기준 데이터베이스 호환성 검사를 적용한다.

- 관리형 Compose 실행에서 외부 데이터베이스·S3 연결 대상 혼입을 사전 차단하고, S3 기본 포트를 환경 설정과 일치시켰다.
- 원본 Dataset의 콘텐츠 해시 공유와 Attempt별 결과 키를 구분해 S3·DB 게시 실패의 안전한 보존·복구 이력을 추가했다.
- 시계열 Lag·Rolling의 NULL·중복 시간키를 검증하고 선행 캐스트·필터 적용 후 정렬하며, 동일 Dataset 재조회에서 사용자의 편집·차트 선택을 보존한다.
- Job 목록을 정렬키 커서 기반 페이지 응답으로 제한하고 이전 이력 탐색 기능을 추가했다. UI의 변환 단계 타입은 생성 OpenAPI 계약을 사용한다.

- Feature Preview는 전체 입력을 기준으로 변환한 뒤 최대 30행을 표시하며, 100,000행 초과 입력은 비동기 Transform Job으로 안내한다.
- Feature Builder의 단계 수정·순서 변경·Lag·Rolling 값 조정, 편집 상태 보존·미리보기 일치 검증, 접근 가능한 파일 업로드와 Job 이력 탐색을 개선했다.
- 실행 중 Iceberg 적재는 외부 커밋 결과 불확실성을 피하기 위해 취소를 거부하고, 데이터 스키마·캐시 무결성 및 Job 조회를 개선했다.

### Added

- API 응답 모델/OpenAPI TypeScript 타입 생성과 정합성 검사, 그룹별 시계열 피처 및 선택형 합성 예제 데이터.
- Data Workspace: CSV·Parquet 업로드, Iceberg 스냅샷 연결, Polars EDA·피처 변환, Worker 기반 실행·취소 및 결과 재탐색, React UI와 Scalar 문서.
- PostgreSQL 메타데이터, Alembic 스키마 관리, S3 호환 객체 저장소에 데이터·결과 보관.
- `make up/down/restart/status/logs/doctor/check/help` 개발 명령과 9600번대 포트·서비스 준비 상태 진단.
