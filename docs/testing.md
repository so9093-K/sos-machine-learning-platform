# 테스트·검증·CI 기준

검증의 목적은 사용자 경험과 데이터·모델·실행 계약을 보호하는 것이다. 테스트의 존재, 개수, 커버리지나 과거 CI 의무 자체를 유지 목표로 삼지 않는다.

## 검증의 유지·삭제

- 유지 여부는 **그 테스트만 발견하는 중요한 실패**, 중복, 운영·유지 비용으로 판단한다. 이유를 설명할 수 없으면 삭제한다. 삭제했다고 대체 테스트를 자동으로 추가하지 않는다.
- **삭제한 레거시 기능을 위해 테스트·호환 분기를 새로 만들거나 유지하지 않는다.** 기존 데이터의 안전한 이전은 필요한 일회성 마이그레이션에서 처리하고, 지속적으로 필요한 안전성은 현재의 일반 계약으로만 검증한다.
- 모델 계산·불변 판본·Job 게시와 복구는 Python 단위 또는 PostgreSQL/S3 통합에서 검증한다. 브라우저에서는 사용자 조작과 API 연결 등 UI에서만 확인할 수 있는 결과를 검사하고 동일한 ML 계산을 반복하지 않는다.
- 테스트 등록부·공통 실행 Manifest·범용 검증 실행기의 내부 형태를 유지하기 위한 회귀 테스트는 운영하지 않는다. 단, 격리 Compose의 타인 소유 자원 삭제 방지처럼 실제 안전 경계는 확인한다. pytest·Vitest·Playwright·Ruff의 **기본 종료 코드와 실행 로그**가 검증 결과다. 전체 테스트의 병합 전 실행이 필요하면 직접 선택한다.

## 로컬 실행

- `make check-fast`: Ruff, 인프라 없는 Python 단위, 프런트엔드 빌드·Vitest.
- `make check`: 위 검사에 더해 현재 구동 중인 로컬 PostgreSQL·S3 통합, OpenAPI 생성 타입, 배포 Wheel, Alembic, 격리 브라우저 전체 흐름 확인.
- 개별 실행: `uv run --locked pytest -q tests/test_*.py`, `uv run --locked pytest -q -m pr tests/integration`, `uv run --locked pytest -q tests/integration`, `cd web && npm test`, `uv run --locked python scripts/isolated_compose.py --spec all --grep '@pr'`.
- 포맷은 CI 실패를 기다리지 않고 `uv run --locked ruff check --fix .`와 `uv run --locked ruff format .`으로 로컬에서 수정한다.
- Python 테스트의 `pr` 표식과 Playwright 제목의 `@pr`은 별도 보호 등록부가 아니라 **일반 PR에서 자동 실행할 개별 검증**을 고르는 방법이다.

## CI 정책

| 이벤트 | 실제 실행 |
| --- | --- |
| Pull Request | Ruff·Python 단위·Frontend 빌드/Vitest·OpenAPI 생성 타입/호환성 + PostgreSQL/S3 기반 핵심 통합 `-m pr` + 격리 브라우저 `@pr` |
| main push | 빠른 검사 + 전체 Python 통합 + 전체 브라우저 |
| 수동 workflow_dispatch | 빠른 검사 + 전체 Python 통합 + 전체 브라우저 |

정기 자동 전체 회귀는 제거한다. 변경이 없는 날에는 Docker·ML·브라우저를 되풀이하지 않되, main push에서는 전체 통합과 전체 브라우저 검증을 자동 실행한다. `python-quality`라는 브랜치 보호 상태 체크는 유지하되 선택한 Job의 실제 성공만 요구한다. 이 이름은 별도 테스트 실행기나 Python 전용 품질 체계를 뜻하지 않는다.

CI는 Native Runner를 직접 실행한다. 별도의 `python-fast` 실행 체계나 `scripts/verify.py` Suite/Manifest 레이어를 두지 않는다. 고유 Compose 환경과 소유 리소스 정리가 필요한 브라우저 검증은 `scripts/isolated_compose.py`가 담당한다. `scripts/verify_openapi.py`는 PR의 API 후방 호환성 비교, `scripts/verify_wheel.py`은 설치한 배포본 검증만 담당한다.

## 안전 경계와 실패

- 실패·취소·0개 테스트를 성공으로 간주하지 않는다. 먼저 실제 실패 명령과 로그를 확인하고 제품 결함·테스트 결함·환경 문제를 구별한다.
- DB Fixture는 테스트마다 고유 DB·버킷을 만들고, 격리 브라우저는 고유 Compose 프로젝트·임시 인증 토큰을 사용한다. 기존 사용자 DB·버킷·Iceberg Snapshot/Tag는 자동 삭제하지 않는다.
- 강제 종료 후 소유 자원 정리는 `.sos-test/runs/<instance>/validation-owner.json`을 확인한 뒤 `python scripts/isolated_compose.py --recover-owned <instance>`로만 수행한다.
- 변경된 기능이 자동 선택 검증에 포함되지 않았다면 실제 위험에 해당하는 개별 테스트를 추가로 실행하고 결과를 리뷰에 기록한다. 불필요한 테스트를 야간 실행으로 옮겨 보존하지 않는다.
