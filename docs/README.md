# 문서 안내

이 저장소의 설계 문서는 **해당 영역의 정본(Source of Truth)이자 미래 구현을 정의하는 청사진**이다. 구현 여부가 정본의 효력을 결정하지 않는다. 설계 기준과 현재 제공되는 기능은 구분한다.

| 정본 | 책임 |
| --- | --- |
| [Platform Identity](vision/PLATFORM_IDENTITY.md) | 제품 정체성·가치·개발 방향과 기준 이미지 |
| [Experience](experience.md) | 사람·외부 시스템·AI 에이전트의 목표와 경험 |
| [Architecture](architecture.md) | 플랫폼과 실행 인프라의 논리적 책임·경계 |
| [Contracts](contracts.md) | 공통 개념·컴포넌트 계약·호환성 원칙 |
| [Runtimes](runtimes.md) | 실행 환경 격리·식별·재현성·호환성 |
| [Workflows](workflows.md) | 작업 실행·상태·재시도·결과 연결의 의미 |
| [ADR](adr/README.md) | 장기적인 기술·아키텍처 결정의 근거 |

**권위의 경계:** 설계의 목적·제약·상호작용·호환성 원칙은 각 정본에서 정의한다. 실제 API 필드, 타입, 설정 기본값은 코드·스키마·자동 생성 명세를 원본으로 삼는다. 충돌이 발견되면 해당 책임을 확인하고 함께 수정하며, 문서나 구현 어느 쪽도 조용히 무시하지 않는다.

`getting-started.md`는 실제 실행 가능한 사용 절차가 생겼을 때, `development.md`와 `operations.md`는 기존 규칙이나 문서로 설명할 수 없는 고유한 절차가 생겼을 때 작성한다. 사용법을 설계 원칙으로 가장하거나 비어 있는 문서를 미리 만들지 않는다. 중요한 설계 변경은 필요할 때 ADR로 근거를 보존한다.
