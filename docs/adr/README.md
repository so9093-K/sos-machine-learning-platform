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
