# 0013 — 검증 실행기·결과 판정 계층 간소화

상태: **Proposed**

## 배경

[ADR 0009](0009-test-engineering-portability.md)는 테스트 수집·JUnit·Manifest·Protected 등록부를 포함한 공통 검증 실행기를 정했고, [ADR 0011](0011-compose-native-validation.md)도 관련 Inventory 유지를 전제했다. 실제 테스트가 아니라 그 실행 증거와 보호 목록을 다시 검증하는 계층이 생겨 유지 비용이 커졌다.

## 결정

- 별도 Suite 실행기·테스트 ID Inventory·Protected 등록부·JUnit/Manifest 재판정기를 제거한다. 테스트 성공·실패는 pytest·Vitest·Playwright·Ruff의 종료 코드로 판정한다.
- CI와 Makefile은 환경 준비·Runner 실행·실패 전달만 수행한다. **실행 시점·선택 조건·검사 목록은 ADR이 아니라** [테스트 기준](../testing.md)과 [CI 설정](../../.github/workflows/ci.yml)에서 관리한다.
- 검증 자원 격리는 유지한다. 소유권이 확인되지 않은 DB·버킷·Compose 자원과 기존 Snapshot·Tag를 삭제하지 않는다.

## 기존 결정과의 관계 및 영향

ADR 0009의 공통 실행기·증거 재판정·Protected 관리 결정을 대체하고, ADR 0011의 Inventory 유지 조항만 대체한다. ADR 0011의 Compose·토큰 격리 및 소유 자원 정리 경계는 유지한다. 제거한 보호 표식이 과거와 동일한 자동 검사 범위를 보장한다고 주장하지 않으며, 구체적인 검증 공백은 PR에서 확인한다.
