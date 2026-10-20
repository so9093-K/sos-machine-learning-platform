# Architecture Decision Records

장기적인 영향이 있는 기술·아키텍처 결정만 기록한다. 사소한 변경은 PR에 기록한다.

- 파일: `0001-decision-name.md` 형식으로 순서대로 작성
- 내용: **배경 · 결정 · 근거 · 영향**
- 상태: `Proposed`, `Accepted`, `Superseded`
- 결정을 변경할 때는 기존 기록을 수정하지 않고 새로운 ADR로 대체

- [0001 — Dataset·Job 결과 게시의 저장소 간 일관성](0001-data-publication.md) · Accepted (결과 객체 즉시 보상 삭제 조항은 0002가 대체)
- [0002 — 객체 수명주기·장기 보존과 대조 정책](0002-object-lifecycle.md) · Accepted (0001의 일부 결정 대체)
- [0003 — Snapshot 보존과 인증·권한·감사 경계](0003-snapshot-identity-audit.md) · Accepted (0002와 연관된 별도 결정)
- [0004 — DuckDB Discovery와 Polars Feature 실행 책임](0004-duckdb-discovery.md) · Accepted

- [0005 — 비동기 Job 제출 식별과 외부 커밋 안전성](0005-job-submission-identity.md) · Accepted
- [0006 — 범용 Dataset 조회와 Lazy Preview·Iceberg 배치 쓰기](0006-generic-data-execution.md) · Accepted
- [0007 — 공통 실행 자원 수용 및 물리 격리 경계](0007-execution-admission.md) · Accepted
- [0008 — Iceberg 테이블 쓰기 소유권과 Catalog 정체성](0008-iceberg-table-write-ownership.md) · Accepted (관리자 채택 조항은 0014에서 대체)
- [0009 — 검증 실행의 CI 독립성과 테스트 자산 수명주기](0009-test-engineering-portability.md) · Superseded (0013에서 대체)
- [0010 — 컨테이너 실행 기반과 플랫폼 계약 분리](0010-container-execution-boundary.md) · Superseded
- [0011 — 서비스와 검증의 Compose 실행 경계 통합](0011-compose-native-validation.md) · Accepted (Inventory·Protected·JUnit·Manifest 유지 조항은 0013에서 대체)

- [0012 — ML 실험·이상 점수·외부 추적의 책임 경계](0012-ml-experiment-boundaries.md) · Proposed
- [0013 — 검증 실행·관리 결정 통합](0013-lean-validation-policy.md) · Accepted (0009 대체·0011의 Inventory 조항만 대체)
- [0014 — Iceberg 과도기 쓰기 소유권 채택 종료](0014-remove-iceberg-adoption.md) · Accepted
