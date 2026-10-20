# 0013 — 검증 실행 책임을 네이티브 도구로 단순화

상태: **Proposed** · [ADR 0009](0009-test-engineering-portability.md)의 공통 검증 실행기·JUnit/Manifest 결과 재판정·Protected 등록 결정과 [ADR 0011](0011-compose-native-validation.md)의 Native Inventory 유지 조항을 변경하는 제안이다. ADR 0011의 Compose 격리·소유 자원 보존 결정은 유지한다.

## 배경

별도 검증 실행기와 테스트 ID·Inventory·Protected·JUnit 대조는 실제 제품 실패를 검출하는 테스트보다 실행기와 메타데이터 자체를 유지·검증하는 비용을 키웠다. 테스트가 성공했는지 판단하는 별도 계층까지 만들어 Native Runner의 결과를 중복 판정했다.

## 결정

- pytest·Vitest·Playwright·Ruff가 테스트 실행과 실패 판정의 정본이다. 검증을 위해 공통 Suite 실행기, 전용 ID 인벤토리·보호 등록부, JUnit·Manifest 결과 재판정기를 운영하지 않는다.
- CI와 Makefile은 환경 준비·실행 명령·실패 종료 코드 전달만 담당한다. 구체적인 CI 선택 조건과 검사 목록은 [테스트·검증 기준](../testing.md) 및 [워크플로](../../.github/workflows/ci.yml)가 정본이다. 매 변경의 실행 범위를 이 ADR로 고정하지 않는다.
- 통합/브라우저 격리는 고유 소유 자원만 생성·정리한다. 타인의 DB·버킷·Compose 프로젝트와 기존 Snapshot·Tag를 삭제하지 않는 경계는 실행기 단순화와 관계없이 유지한다.

## 영향

검증 메타도구와 이를 위한 자기 검증을 제거해 유지 비용을 줄인다. 어떤 테스트가 어느 결함을 검출하는지는 실제 테스트 내용과 실행 결과로 판단한다. 별도 보호 목록을 제거했으므로 기존에 지정된 모든 테스트의 자동 실행을 보장하지는 않는다.
