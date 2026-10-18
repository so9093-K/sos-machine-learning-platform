# 0011 — 서비스와 검증의 Compose 실행 경계 통합

상태: **Accepted** · [ADR 0010](0010-container-execution-boundary.md)을 대체

## 결정

공개 `make up`과 브라우저 E2E의 서비스 실행은 동일한 `compose.yaml`의 `app` 프로필을 사용한다. Playwright·pytest는 네이티브 검증 도구로 유지하되, SOS API·Worker·Web을 호스트 PID로 기동하거나 개발 관리자 신원으로 우회하지 않는다.

브라우저 테스트는 실행마다 별도 Compose 프로젝트·DB·S3·임시 Token을 생성한다. 소유 기록과 강제 종료 후 복구는 `scripts/isolated_compose.py`에서 담당하고, 소유 증거가 있는 프로젝트 외 볼륨은 정리하지 않는다. 기존 Native Test Inventory·Protected Contracts·JUnit·Manifest 증거는 유지한다.

`make restart`는 변경된 인증 환경변수와 이미지를 반영하도록 컨테이너를 재생성한다. 새 Token 유효성과 이전 Token 거부를 실제 통합 검증에서 확인하며 로그는 Compose의 유한 크기 회전 정책을 따른다. `make check-fast`는 인프라를 요구하지 않는다.

## 근거와 영향

보호해야 할 것은 데이터·사용자·실행 계약이지 구형 호스트 CLI의 구현이 아니다. 호스트 실행 관리기, 로그 릴레이, E2E 래퍼와 내부 동작 테스트를 삭제한다. Iceberg 쓰기 소유권 복구, Worker 부모 SIGKILL 안전성, DB·S3·Snapshot 계약은 보존한다.

공개 TLS·OIDC, 비신뢰 코드 격리, 다중 호스트·GPU 자원 관리는 이번 변경 범위가 아니다.
