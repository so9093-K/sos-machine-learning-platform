# 문서 안내

각 문서는 한 가지 책임을 가진 설계 정본이다. **장기 목표, 지켜야 할 의미, 현재 구현**을 구분해 읽고 기록한다.

| 문서 | 다루는 것 | 다루지 않는 것 |
| --- | --- | --- |
| [Platform Identity](vision/PLATFORM_IDENTITY.md) | 제품 정체성·장기 가치·설계 철학·기준 이미지 | 현행 API·라이브러리·화면 설정 |
| [Experience](experience.md) | 이용자 목표·상호작용·오류 및 복구 경험 | UI 컴포넌트 구성·일시적인 표시 기본값 |
| [Architecture](architecture.md) | 구성요소의 논리적 책임·의존 방향 | 파일별 구현 설명·배포 명령 |
| [Contracts](contracts.md) | 공통 개념·경계 간 의미·호환성 | 구현별 계산법·정확한 필드와 타입 |
| [Runtimes](runtimes.md) | 실행 환경 식별·호환성·격리·자원 책임 | 환경변수 기본값·내부 알고리즘 |
| [Workflows](workflows.md) | Job·Attempt·취소·재시도·결과 확정의 의미 | SQL·객체 경로·세부 실행 절차 |
| [Testing & CI](testing.md) | 검증 선택·실패 판정·격리 원칙 | CI 단계와 테스트 목록의 복제 |
| [Diagnostics](diagnostics.md) | 오류·추적·장애 확인 및 복구 안내 | 새로운 제품·설계 원칙 |
| [ADR](adr/README.md) | 기존 장기 결정을 변경한 이유와 영향 | 임시 작업 기록·수시로 바뀌는 설정 |

**실제 API 필드·타입은 코드와 자동 생성 OpenAPI**, 환경설정·기본값은 구현 코드, 실행 명령은 [Getting Started](getting-started.md)와 [Makefile](../Makefile)이 정본이다. 사용자에게 의미 있는 변화는 [Changelog](../CHANGELOG.md)에 요약한다.

새 주제라고 문서를 늘리거나 가장 가까운 정본에 세부사항을 밀어 넣지 않는다. 먼저 기존 책임과 겹치는지 확인하고, 같은 의미는 한 곳에서 정의한 뒤 다른 문서에서는 참조한다. 문서와 구현이 충돌하면 어느 책임의 계약이 달라졌는지 확인해 함께 조정한다.

문서는 지금 유효한 계약과 절차만 설명한다. 더 이상 유효하지 않은 설명과 끝난 전환 절차는 그 변경과 함께 제거하고, 경과와 이유는 PR·Git 이력에 남긴다. Migration 파일은 기존 DB의 업그레이드 경로이므로 유지한다.
