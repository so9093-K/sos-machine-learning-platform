# 0013 — 검증 실행·관리 결정 통합

상태: **Accepted**

## 배경

[ADR 0009](0009-test-engineering-portability.md)의 공통 Suite 실행기·JUnit/Manifest 재판정·Protected 등록부와 [ADR 0011](0011-compose-native-validation.md)의 Inventory 유지 조항은 실제 검증과 별개의 관리 비용을 발생시켰다.

## 결정

- 테스트 정의·실행·성공 판정은 pytest·Vitest·Playwright·Ruff 등 Native Runner가 담당한다. 공통 Suite 실행기, 테스트 ID Inventory, Protected 등록부 및 필수 JUnit·Manifest·Summary 재판정 계층은 제거한다.
- CI와 Makefile은 실행 환경 준비·도구 호출·실패 전달에 집중하고, 검사 자체를 특정 CI 서비스에 종속시키지 않는다. `make check-fast`의 무인프라 실행과 `make check`의 전체 검증 의미를 유지한다. 검사 선택·실행 조건은 [테스트 기준](../testing.md), [CI 설정](../../.github/workflows/ci.yml), [Makefile](../../Makefile)을 따른다.
- 검증 환경의 독립성과 DB·S3·Compose 자원 소유권 확인을 유지한다. 기존 사용자 자원 및 Iceberg Snapshot·Tag는 자동 삭제하지 않는다.

## 기존 결정과의 관계

- **ADR 0009 전체 대체:** Native Runner와 CI의 책임 분리·실행 독립성·자원 안전은 위 결정으로 계승하고, 공통 실행기·증거 재판정·Protected 관리 체계는 폐기한다.
- **ADR 0011 일부 대체:** Inventory·Protected·JUnit·Manifest 유지 조항만 폐기한다. Compose 실행 경계와 독립 프로젝트·DB·S3·임시 Token 및 소유 자원 정리 원칙은 계속 유효하다.
