# Runtime

Runtime은 학습·평가·추론 등 **계산을 실제로 수행하는 환경**이다. 플랫폼의 제어 상태와 실행 환경은 논리적으로 분리하며, 같은 호스트나 컨테이너에서 시작해도 이 경계를 유지한다.

## 실행 요구와 실제 환경

- **Runtime Specification:** 작업이 요구하는 프레임워크·의존성·기능·자원·실행 권한이다. 특정 스케줄러나 컨테이너 제품을 전제하지 않는다.
- **Resolved Runtime:** 실제 실행에 사용된 코드·의존성·시스템 라이브러리·하드웨어 등 결과에 영향을 주는 환경의 식별 근거다.

학습한 모델의 전처리·입력 서명·직렬화 형식과 복원 환경의 호환성을 확인해야 한다. 파일이 저장되었다고 로드 가능하거나 안전한 것은 아니다. 현재 모델 복원 호환성은 Python minor 버전과 직렬화에 관여하는 numpy·scikit-learn의 정확한 버전으로 판단하며, Python 패치 차이만으로 기존 모델을 사용 불가로 만들지 않는다. 실행 이미지의 Python 기반은 digest로 고정해 다시 빌드해도 학습·복원 환경이 조용히 바뀌지 않게 하고, 호환되지 않는 모델은 입력 오류가 아닌 실행 환경 불일치로 안내한다. 난수 고정만으로 모든 실행 결과가 동일해지는 것도 아니다. 충돌하는 의존성이나 신뢰 수준이 다른 외부 코드를 하나의 무제한 실행 환경에 넣지 않는다.

## 자원 수용과 물리 격리

실행 요청은 **논리적 자원 예약 → 실제 실행 예산 → 격리·관측**을 구분한다. 현재 API와 Worker는 공유 PostgreSQL 예약을 통해 동시 실행을 조정한다. 요청 예산이 수용되지 않으면 실행을 시작하지 않으며, 동기 요청에는 용량 오류를 반환하고 Worker Job은 대기 상태로 남긴다. 논리 예약·DuckDB 메모리 설정·RSS 폴링은 OS 수준의 메모리·CPU·디스크 격리가 아니다.

Worker는 자식 실행의 시간·메모리·스테이징 사용량을 감시하고 초과 시 실패 상태를 기록한다. Linux cgroup v2 격리를 필수로 설정한 경우에는 위임·제한·정리 가능 여부를 **Job 인수 전에** 확인하고, 충족하지 못하면 실행을 시작하지 않는다. 기본 로컬 실행에서 물리 격리가 자동 제공되는 것으로 간주하지 않는다. 부모 비정상 종료·PostgreSQL 잠금 세션 상실 후 이전 실행이 결과를 잘못 확정할 위험을 자식 종료·소유권 재검증으로 줄인다. 이 보호가 세션 확인과 외부 커밋 사이의 모든 경합을 막는 분산 Fencing Token은 아니며, Iceberg 커밋 여부가 불확실하면 별도로 확인한다.

공유 예약은 여러 호스트의 실물 메모리 용량을 자동 보증하지 않는다. 다중 호스트 스케줄링·GPU 격리·임의 사용자 코드의 안전 실행은 실제 실행 인프라와 별도 검증이 필요하다.

## 데이터·외부 시스템 경계

Dataset 캐시는 재사용 가능한 로컬 복사본이며 원본 S3 객체·모델·Iceberg Snapshot의 보존 정책과 다르다. 사용 중인 캐시를 임의 축출하지 않으며 데이터 손상 시 원본으로부터 무결성을 확인한다.

API·초기화·Dagster는 기본 의존성을 사용하고 ML 계산용 numpy·joblib·scikit-learn·SHAP은 `ml` dependency group으로 설치한다. 학습 제출·Fork의 매개변수 검증은 SDK를 로딩하지 않는다. 개발 검사는 실제 ML 계산을 포함하므로 `dev` 그룹에 `ml`을 포함한다. 호스트 Worker 실행에는 `uv sync --locked --group ml`을 사용하고, 등록 Feature 실행에는 `--group feast`를 함께 선택한다.

MLflow는 Worker/API의 실험·모델 관리 시스템이다. 이름·버전·Alias의 정본은 MLflow이지만 SOS는 신뢰된 Worker가 발급한 모델 위치·SHA-256·Feature·Runtime·권한을 검증한 뒤 실행한다. 외부 Registry 등록만으로 임의 직렬화 파일을 신뢰하지 않는다. 인증정보는 서버 측에만 제공한다. 연결 상태의 의미는 [Workflows](workflows.md)를 따른다.

## Feast 전문 Runtime

등록 Feature의 Historical Retrieval을 지원하는 Worker 이미지에 Feast 0.67.0을 설치한다.
Feast SQL Registry는 SOS 메타데이터 DB와 분리된 PostgreSQL 서비스에 보관한다.
API는 Feast SDK를 설치하지 않고 Dataset·Actor 권한을 검증해 Job을 제출하며,
Worker는 등록·조회·명시적 폐기 작업에서만 Feast SDK를 호출하고 결과를
SOS 불변 Dataset으로 게시한다. 등록과 폐기는 같은 Registry 이름의 SOS DB
advisory lock을 사용해 외부 변경을 직렬화한다. Feast Registry가 불가용해도 일반 SOS Data·ML Job의 Worker 시작을
대기시키지 않는다.
현재 Offline 실행 모듈은 `feast_offline.py`의 DuckDB/Ibis 물질화·S3 호환
연결 방식이다. Entity 조회 상한은 `SOS_FEAST_MAX_ENTITY_ROWS` 실행 정책으로
조정하며, 초과 입력은 잘라내지 않는다. 새 Offline Store 구현은 이 경계에서만
교체하고 SOS의 Job·Dataset·모델 API를 재정의하지 않는다.
현재는 게시 Parquet Feature Source, UTC Event Timestamp, Entity 시점 Dataset만
지원한다. Online Serving과 Iceberg SQL Catalog 직접 Feature Source 조회는 지원하지
않는다. Feast 전문 Worker는 API와 동일한 uv.lock의 별도 `feast` dependency group으로
설치한다. 별도 `pip install` 덧씌우기나 숨은 의존성 해석을 수행하지 않는다.

공식 Feast 0.67.0 UI는 로컬 개발 Compose에서 독립 서비스로 상시 실행한다.
SOS Worker의 `sos_features` PostgreSQL Registry를 공유하고 별도 Registry를
만들지 않는다. UI 전용 `feast_ui` SQL 역할에는 기존·향후 Registry 테이블
SELECT만 허용하며, DDL·Feature 적용·폐기 권한을 주지 않는다. 최초 시작 및
기존 볼륨 재시작 시 짧은 초기화 Job이 Registry 스키마를 준비하고 조회 권한을
적용한다. UI 서버의 Registry REST API는 변경 경로도 노출하므로 SQL 권한과
127.0.0.1 포트 바인딩을 함께 유지한다. SOS Actor 권한이 자동 상속되지 않으므로
일반 사용자·외부 네트워크에 직접 공개하지 않는다. Feast UI는 관리용 정의
탐색이며 Dataset·Job·Model 사용자 동작은 기존 SOS API로 수행한다.

## 현재 구현의 범위

등록형 Workflow 정의·스케줄·상태의 Dagster OSS Runtime은 별도 Python 이미지와 Code Location·Daemon·Webserver·메타데이터 DB를 사용하되 SOS와 동일한 uv.lock의 Dagster dependency group에서 설치한다. PostgreSQL 드라이버는 현재 dagster-postgres의 upstream psycopg2 의존성과 맞추어 유지한다. Dagster가 독립 SOS Worker의 자원 격리·ML 계산·결과 게시 책임을 대신하지 않는다. Dagster Run 실패·취소 후 진행 중인 SOS Job 취소는 Dagster 상태 센서가 요청하며, 실제 중단 확정은 SOS Job/Attempt의 계약이다.

현재 프로젝트의 Python 3.12·잠금 의존성과 Compose 실행 형태는 **개발·배치 환경의 선택**이지 모든 모델 Runtime의 고정 명세가 아니다. 정확한 환경변수·예산 기본값 및 구현 세부사항은 `src/sos_platform/execution_budget.py`, `resource_admission.py`, `cgroup.py`, `worker.py`와 실행 설정이 정본이다. 설치와 설정 방법은 [Getting Started](getting-started.md)를 따른다. 실행 상태·복구는 [Workflows](workflows.md), 저장 결과의 의미는 [Contracts](contracts.md)를 따른다.
