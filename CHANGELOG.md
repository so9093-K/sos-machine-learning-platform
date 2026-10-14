# Changelog

## Unreleased

### Changed

- Feature Preview는 전체 입력을 기준으로 변환한 뒤 최대 30행을 표시하며, 100,000행 초과 입력은 비동기 Transform Job으로 안내한다.
- Feature Builder의 단계 수정·순서 변경·Lag·Rolling 값 조정, 편집 상태 보존·미리보기 일치 검증, 접근 가능한 파일 업로드와 Job 이력 탐색을 개선했다.
- 실행 중 Iceberg 적재는 외부 커밋 결과 불확실성을 피하기 위해 취소를 거부하고, 데이터 스키마·캐시 무결성 및 Job 조회를 개선했다.

### Added

- API 응답 모델/OpenAPI TypeScript 타입 생성과 정합성 검사, 그룹별 시계열 피처 및 선택형 합성 예제 데이터.
- Data Workspace: CSV·Parquet 업로드, Iceberg 스냅샷 연결, Polars EDA·피처 변환, Worker 기반 실행·취소 및 결과 재탐색, React UI와 Scalar 문서.
- PostgreSQL 메타데이터, Alembic 스키마 관리, S3 호환 객체 저장소에 데이터·결과 보관.
- `make up/down/restart/status/logs/doctor/check/help` 개발 명령과 9600번대 포트·서비스 준비 상태 진단.
