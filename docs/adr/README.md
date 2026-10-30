# Architecture Decision Records

장기적인 영향이 있는 기술·아키텍처 결정만 기록한다. 사소한 변경은 PR에 기록한다.

- 파일: `0001-decision-name.md` 형식으로 순서대로 작성
- 내용: **배경 · 결정 · 근거 · 영향**
- 상태: `Proposed`, `Accepted`, `Superseded`
- 결정을 변경할 때는 기존 기록의 본문을 수정하지 않고 새로운 ADR로 대체하며, 기존 기록에는 상태 줄에 대체 관계만 표시

- [0001 — Dataset·Job 결과 게시의 저장소 간 일관성](0001-data-publication.md) · Accepted (결과 객체 즉시 보상 삭제 조항은 0002가 대체)
- [0002 — 객체 수명주기·장기 보존과 대조 정책](0002-object-lifecycle.md) · Accepted (0001의 일부 결정 대체)
- [0003 — Snapshot 보존과 인증·권한·감사 경계](0003-snapshot-identity-audit.md) · Accepted (0002와 연관된 별도 결정)
- [0004 — DuckDB Discovery와 Polars Feature 실행 책임](0004-duckdb-discovery.md) · Accepted

- [0005 — 비동기 Job 제출 식별과 외부 커밋 안전성](0005-job-submission-identity.md) · Accepted
- [0006 — 범용 Dataset 조회와 Lazy Preview·Iceberg 배치 쓰기](0006-generic-data-execution.md) · Accepted
- [0007 — 공통 실행 자원 수용 및 물리 격리 경계](0007-execution-admission.md) · Accepted
- [0008 — Iceberg 테이블 쓰기 소유권과 Catalog 정체성](0008-iceberg-table-write-ownership.md) · Accepted (관리자 채택 조항은 0014가 대체)
- [0009 — 검증 실행의 CI 독립성과 테스트 자산 수명주기](0009-test-engineering-portability.md) · Superseded (0013이 대체)
- [0010 — 컨테이너 실행 기반과 플랫폼 계약 분리](0010-container-execution-boundary.md) · Superseded (0011이 대체)
- [0011 — 서비스와 검증의 Compose 실행 경계 통합](0011-compose-native-validation.md) · Accepted (0010 대체 · 테스트 관리 조항은 0013이 종료)

- [0012 — ML 실험·이상 점수·외부 추적의 책임 경계](0012-ml-experiment-boundaries.md) · Accepted (관리 정본·외부 Tracking 조항은 0015가 대체)
- [0013 — 레거시 검증 자동화 초기화](0013-validation-reset.md) · Accepted (0009 대체·0011 검증 관리 조항 종료)
- [0014 — Iceberg 과도기 쓰기 소유권 채택 종료](0014-remove-iceberg-adoption.md) · Accepted (0008의 관리자 채택 조항 대체)
- [0015 — MLflow 모델·실험 관리 정본 전환](0015-mlflow-management-ownership.md) · Accepted (0012의 관리 책임 제안 대체)
- [0016 — Dagster OSS Workflow 단일 정본 전환](0016-dagster-orchestration.md) · Accepted (자체 Pipeline 실행기 대체)
- [0017 — 모델 자산 그룹·보관·영구 삭제 계약](0017-model-lifecycle.md) · Accepted (ADR 0015의 단일 모델 그룹 기준 확장)

- [0018 — Feast Feature 관리·조회 정본 전환](0018-feast-feature-ownership.md) · Accepted
- [0019 — Ray Serve 기반 온라인 서빙](0019-ray-serve-online-serving.md) · Accepted (Dashboard 비공개 조항은 0020이 대체)
- [0020 — 온라인 서빙 운영 가시성과 실제 상태 정합성](0020-serving-operator-visibility.md) · Accepted (0019의 Dashboard 비공개 조항 대체)
- [0021 — 분석 주제와 등록 Pipeline의 분리](0021-analysis-topic-contract.md) · Accepted
