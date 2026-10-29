# 오류·진단·상관관계 운영 기준

## 책임과 Source of Truth

- **오류 의미·정책:** `src/sos_platform/errors.py`의 Error Catalog가 정본이다. 코드와 의미는 안정적으로 유지하며 사용자용 문구는 수정할 수 있다.
- **실제 실행 결과:** PostgreSQL의 Job·Attempt 상태, 객체 게시 의도·Snapshot 보존 의도가 정본이다.
- **외부 커밋의 실체:** Iceberg Catalog의 Snapshot과 `sos.attempt-id`가 정본이다. DB의 Failed만으로 Append가 발생하지 않았다고 판단하지 않는다.
- **권한·중요 변경 결정:** PostgreSQL `audit_events`가 트랜잭션과 연결되는 정본이다. 이 기록은 외부 변경 불가능한 감사 저장소를 대체하지 않는다.
- **기술적 원인·시각:** 구조화된 JSON 로그는 진단 증거다. 로그나 Trace가 없더라도 Job/Attempt 상태는 유지되어야 한다.

- **Workflow 실행·단계 이력:** Dagster OSS Run과 이벤트가 정본이다. SOS는 Actor·Idempotency-Key·Dagster Run ID와 신뢰된 실행이 생성한 Job 연결을 유지하며 SOS Job/Attempt의 실제 계산 완료와 구분한다. 일반 제출 키만으로 Job의 Workflow 출처를 확정하지 않는다.

## 오류 응답과 호환성

API는 `detail`(안전한 오류 메시지 또는 입력 검증 필드 배열)과 `code`, `category`, `retry_policy`, `request_id`, `type`, `title`, `status`, `instance`를 제공한다. HTTP 상태와 `X-Request-ID`도 전달한다. `ProblemOut`이 OpenAPI 계약의 정본이며, 통신 타입은 생성된 TypeScript 타입을 사용한다.

- 서버 자원 설정이 잘못됐거나 Worker 예산이 하위 실행기의 논리 메모리를 포함하지 못하면 **사용자 입력 422가 아닌 서버 내부 500**이다. 오류 응답은 설정 원문·비밀값을 반환하지 않으며, 관리자는 실행 환경과 자원 정책을 확인해야 한다.
- `never`: 사용자 입력·권한 또는 상태를 수정해야 한다. 자동 재시도하지 않는다.
- `after_refresh`: 현재 상태를 재조회하고 재시도가 안전한지 판단한다. 쓰기 재실행을 자동 승인하는 의미가 아니다.
- `after_delay`: 명시적으로 멱등성을 보장하는 요청에만 지연 재시도할 수 있다.
- `manual_verification_required`: 외부 커밋 여부를 조사하기 전 반복 실행하지 않는다. 웹은 오류 코드·요청 ID와 함께 중복 제출 금지 안내를 표시한다.
- 인증 실패의 `WWW-Authenticate` 헤더, 404 리소스 은닉, 표준 `detail` 필드와 API-first OpenAPI 검증을 유지한다.

## Correlation과 로그 보안

- 단일 HTTP 요청의 Request ID는 서버가 생성하며, 외부 요청이 보낸 추적 식별자를 무검증으로 믿지 않는다.
- API의 비동기 작업 생성 시 Request ID를 Job에 저장하고 생성·종료·복구 감사에 연결한다. Attempt는 해당 Job의 일부이다.
- `job_id`, `attempt_id`, `error_code`, `failure_stage`는 단계와 원인 검색의 핵심 식별자다. Metrics Label에는 고유 ID를 사용하지 않는다.
- `observability.emit()`은 허용된 이벤트 속성만 구조화 로그로 내보낸다. 토큰·S3 Key·SQL·환경 변수·예외 원문·데이터 Payload·Stack Trace를 기록하지 않는다.
- API/Worker 실행에 로그 수집기나 외부 APM 서비스는 필수가 아니다. 로그 저장소를 상태·감사·커밋 판정의 원본으로 만들지 않는다.

## Runbooks — 반복 가능한 수동 진단

### 입력·권한 오류

1. 사용자가 제시한 `error_code`와 `request_id`를 확인한다.
2. `422`는 누락 필드·타입과 요청 자체를 확인한다. 사용자 데이터를 진단 로그에 붙이지 않는다.
3. `401/403`은 Actor 권한과 `audit_events`의 판단을 확인한다. 리소스 소유권을 숨기는 `404`를 존재 여부 확인 수단으로 사용하지 않는다.
4. 입력 수정·권한 요청은 사용자 행동이며 오류만으로 자동 반복 요청하지 않는다.

### PostgreSQL·S3 저장 실패

1. API Request ID 또는 Job ID에서 관련 Attempt 및 실패 단계(`processing`, `publication`)를 찾는다.
2. `make doctor`로 컨테이너의 Health 상태를 확인하고, `/api/ready` 응답으로 DB·S3·Worker 준비 상태를 대조한다.
3. DB 객체 게시 의도 `Pending`·`Failed`와 S3 실제 객체, Dataset 참조를 대조한다.
4. `candidate`는 삭제 허가가 아니다. Storage Health는 관측 도구이며 사용자 객체를 자동 삭제하지 않는다.
5. 원인 확정 전 재실행·공유 객체 삭제를 수행하지 않는다.

### Worker 중단·Iceberg 커밋 여부 불확실

1. Job ID·Attempt ID·`request_id`, 실패 단계 및 `job.recover` 감사 기록을 대조한다.
2. Iceberg의 `sos.attempt-id` Snapshot 속성으로 실제 Append 커밋을 검증한다.
3. Snapshot과 보존 Tag 상태를 별도로 확인한다. Dataset 등록 실패 후 Tag가 남아도 제거하지 않는다.
4. 커밋의 성공 또는 실패가 확인되지 않으면 재적재하지 않는다. `manual_verification_required`를 그대로 유지한다.
5. 일반 변환 Job도 Attempt별 결과 경로·미확정 S3 게시 의도를 확인한 후 재시도한다.

### 감사 기록 또는 관측 서비스 장애

1. DB 트랜잭션 내 필수 감사 쓰기 실패는 비즈니스 변경을 롤백해야 한다. 접근 거부는 감사 기록이 실패해도 허용으로 바뀌지 않는다.
2. 로그 출력 장애와 외부 수집기 장애는 데이터 커밋의 성공·실패를 임의로 바꾸지 않는다.
3. 장애 당시의 Request/Job/Attempt ID와 확인 불가 영역을 기록한다. 근거 없이 원인을 단정하지 않는다.

## 코드 변경과 검증

새 오류는 Error Catalog에 의미·메시지·재시도 정책을 등록하고 API·Web·Worker에서 사용한다. 검증이 필요한지는 [테스트·CI 기준](testing.md)에 따라 실제 결함 탐지 효과와 비용으로 판단하며, 특정 테스트 계층이나 목록을 자동 의무화하지 않는다. 긴급 버그 수정에서도 로그만 보고 데이터나 Tag를 삭제하지 않는다.

### 운영자 원인 조회와 커밋 경계

- `GET /api/v2/audit?request_id=<UUID>` 또는 `?job_id=<UUID>`는 `audit.read` 역할에서 과거 기록을 조회할 수 있다.
- `GET /api/v2/diagnostics/jobs/{job_id}`는 동일한 감사 권한을 필요로 하며, 작업·실행 시도의 실패 단계, 분류된 원인 및 예외 **클래스명**만 반환한다. 원문 오류·원본 데이터·비밀은 반환하지 않는다.
- `iceberg_prepare` 중단은 Append 미진입이 확인된 실패, `iceberg_commit` 진입 이후는 실제 커밋 결과가 불확실한 실패로 분류한다. Worker가 예고 없이 종료되면 단계 확인 자체가 불가능할 수 있어 모든 실행 중 Iceberg 복구에는 보수적으로 수동 Snapshot 검증을 적용한다.
- `failure_category=unclassified`는 원인 미확정을 의미한다. 임의의 예외 메시지를 자동으로 노출하거나 분류하지 않는다.

### 로그 크기·보관 정책

- `make logs`는 `docker compose logs --tail 80`로 최근 서비스 로그만 조회한다.
- Compose의 `json-file` 로그 드라이버는 서비스별 2 MiB × 3개 파일로 회전한다. 이 로그는 시스템 상태·감사·커밋의 정본이 아니며, Token·S3 Key·원본 데이터가 출력되지 않도록 구조화 로그 허용 목록을 적용한다.


### MLflow Run 시각 및 기록 계약

새 MLflow Run의 `start_time`과 종료된 Run의 `end_time`은 SOS Job의 실제 Attempt 시작·종료 시각을 UTC 기준 Unix 밀리초로 변환해 기록한다. Attempt가 없는 Job은 기록된 Job 접수·갱신 시각을 사용한다. 형식이나 타임존이 불명확한 시각은 임의로 보정하지 않는다. 기존 MLflow Run의 시작 시각은 Outbox 재시도에서 소급 수정하지 않으며 1970년으로 보이는 과거 Run을 일괄 변경하지 않는다.

### MLflow 첫 모델 등록·재시도

1. Pipeline의 Training Job이 `Succeeded`지만 `MLflow 기록·모델 등록 실패`로 대기한다면 학습을 다시 제출하지 않는다. 성공한 모델 아티팩트는 SOS가 유지하며, Registry 등록 완료 전 후속 Pipeline 단계는 진행하지 않는다.
2. `GET /api/ml/tracking/jobs/{job_id}`의 `state`, `attempts`, `last_error_code`를 조회하고, 필요하면 로컬 운영 UI(`http://localhost:9606`)에서 해당 Registered Model·Version을 확인한다.
3. `SOS-MLFLOW-HTTP-404-registered-models_get`은 처음 등록하려는 모델이 아직 존재하지 않는 경우에도 발생할 수 있다. MLflow 저장소를 초기화하지 말고 SOS의 Registered Model 조회·생성 순서를 확인한다.
4. 코드나 연결 문제를 수정한 뒤 기존 전송 Outbox의 `Retry`가 다시 시도하는지 확인한다. 중복 Model Version 등록을 막는 Job ID·SHA 검증은 유지한다.

### Dagster OSS 장애·복구

1. `make status`에서 `dagster-postgres`, `dagster-code`, `dagster-webserver`, `dagster-daemon` 상태를 확인한다.
2. Dagster Run ID와 SOS Workflow 제출 ID를 대조한다. GraphQL 장애 시 기존 SOS Job·Model·Scoring 결과의 성공을 실패로 변경하지 않는다.
3. Dagster 실행 접수 응답이 유실되었다면 동일 Actor와 Idempotency-Key로 재요청하고, Dagster Run 태그를 조회한 뒤 중복 실행 여부를 확인한다.
4. 취소 시 현재 SOS Job의 실제 중단 여부를 조회한다. Dagster 취소 신호만으로 외부 저장소 커밋이 취소됐다고 판단하지 않는다.
5. 로컬 운영 UI(`http://localhost:9607`)와 `make doctor-dagster`로 Workflow 및 실제 Run 검색 GraphQL 계약을 구별해 확인한다. 기본 GraphQL `__typename`의 200 응답만으로 조회·제출 성공을 가정하지 않는다.
6. API 구조화 로그 `dagster.request.failed`의 `request_id`, `operation`, `failure_type`, `status`로 HTTP 스키마 오류·연결 장애·응답 시간 초과를 구분한다. 원문 쿼리·데이터·토큰은 기록하지 않는다.
