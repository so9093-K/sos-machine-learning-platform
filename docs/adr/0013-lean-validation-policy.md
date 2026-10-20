# 0013 — 검증 실행기·결과 판정 계층 간소화

상태: **Accepted**

## 배경

[ADR 0009](0009-test-engineering-portability.md)는 공통 Suite 실행기·JUnit/Manifest 증거 판정·Protected 테스트 등록부를 결정했고, [ADR 0011](0011-compose-native-validation.md)도 기존 Inventory와 증거 체계의 유지를 명시했다. 검증 결과를 다시 수집·대조하는 별도 관리 계층은 실제 테스트와 별개로 유지·진단 비용을 발생시킨다.

## 결정

- 테스트 실행과 성공·실패 판정은 pytest·Vitest·Playwright·Ruff 등 기존 Native Runner의 종료 코드에 맡긴다. 별도 Suite 실행기, 테스트 ID Inventory, Protected 등록부, JUnit·Manifest·Summary 필수 증거 및 재판정 계층을 제거한다.
- CI와 Makefile은 실행 환경 준비, 검증 도구 호출, 실패 전달을 담당한다. 개별 검사와 실행 시점은 [테스트 기준](../testing.md), [CI 설정](../../.github/workflows/ci.yml), [Makefile](../../Makefile)이 정본이다.
- 검증 자원의 고유 소유권·격리·정리 원칙은 변경하지 않는다. 기존 DB·S3·Compose 자원 및 Iceberg Snapshot·Tag를 검증 편의로 삭제하지 않는다.

## 기존 결정과의 관계

- **ADR 0009 일부 대체:** 공통 검증 CLI, 테스트 수집·JUnit ID 대조, Manifest·Summary 재판정, Protected 분류·등록 및 그 보호 목록을 전제로 한 검증 유지 의무를 폐기한다. Native Runner를 테스트 실행의 정본으로 두는 원칙과 CI 독립성, `make check-fast`의 무인프라 의미, 자원 소유권 경계는 유지한다.
- **ADR 0011 일부 대체:** Native Test Inventory·Protected Contracts·JUnit·Manifest 증거를 유지한다는 조항만 폐기한다. 동일 Compose 실행 경계, 테스트별 독립 프로젝트·DB·S3·임시 Token, 소유 확인 후 정리 원칙은 유지한다.

두 기존 ADR은 해당 조항을 제외하고 유효하며 원문을 수정하지 않는다.
