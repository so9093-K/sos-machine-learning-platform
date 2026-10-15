# 오류·진단·상관관계 운영 기준

## 책임과 Source of Truth

- **오류 의미·정책:** `src/sos_platform/errors.py`의 Error Catalog가 정본이다. 코드와 의미는 안정적으로 유지하며 사용자용 문구는 수정할 수 있다.
- **실제 실행 결과:** PostgreSQL의 Job·Attempt 상태, 객체 게시 의도·Snapshot 보존 의도가 정본이다.
- **외부 커밋의 실체:** Iceberg Catalog의 Snapshot과 `sos.attempt-id`가 정본이다. DB의 Failed만으로 Append가 발생하지 않았다고 판단하지 않는다.
- **권한·중요 변경 결정:** PostgreSQL `audit_events`가 트랜잭션과 연결되는 정본이다. 이 기록은 외부 변경 불가능한 감사 저장소를 대체하지 않는다.
- **기술적 원인·시각:** 구조화된 JSON 로그는 진단 증거다. 로그나 Trace가 없더라도 Job/Attempt 상태는 유지되어야 한다.

## 오류 응답과 호환성

API는 기존 `detail`(문자열·필드 오류 배열·객체)을 유지하면서 `code`, `category`, `retry_policy`, `request_id`, `type`, `title`, `status`, `instance`를 제공한다. HTTP 상태와 `X-Request-ID`도 전달한다. `ProblemOut`이 OpenAPI 계약의 정본이며, 통신 타입은 생성된 TypeScript 타입을 사용한다.

- `never`: 사용자 입력·권한 또는 상태를 수정해야 한다. 자동 재시도하지 않는다.
- `after_refresh`: 현재 상태를 재조회하고 재시도가 안전한지 판단한다. 쓰기 재실행을 자동 승인하는 의미가 아니다.
- `after_delay`: 명시적으로 멱등성을 보장하는 요청에만 지연 재시도할 수 있다.
- `manual_verification_required`: 외부 커밋 여부를 조사하기 전 반복 실행하지 않는다.
- 인증 실패의 `WWW-Authenticate` 헤더, 404 리소스 은닉, 이전 `detail` 필드와 API-first OpenAPI 호환성 검사를 유지한다.

## Correlation과 로그 보안

- 단일 HTTP 요청의 Request ID는 서버가 생성하며, 외부 요청이 보낸 추적 식별자를 무검증으로 믿지 않는다.
- API의 비동기 작업 생성 시 Request ID를 Job에 저장하고 생성·종료·복구 감사에 연결한다. Attempt는 해당 Job의 일부이다.
- `job_id`, `attempt_id`, `error_code`, `failure_stage`는 단계와 원인 검색의 핵심 식별자다. Metrics Label에는 고유 ID를 사용하지 않는다.
- `observability.emit()`은 허용된 이벤트 속성만 구조화 로그로 내보낸다. 토큰·S3 Key·SQL·환경 변수·예외 원문·데이터 Payload·Stack Trace를 기록하지 않는다.
- 개발 인스턴스 로그는 `make logs`에서 민감값을 추가 마스킹한다. 원본 로그·Trace·CI 아티팩트에 실데이터나 비밀정보가 있는지 주기적으로 검증한다.
- API/Worker 실행에 로그 수집기나 외부 APM 서비스는 필수가 아니다. 로그 저장소를 상태·감사·커밋 판정의 원본으로 만들지 않는다.

## Runbooks — 반복 가능한 수동 진단

### 입력·권한 오류

1. 사용자가 제시한 `error_code`와 `request_id`를 확인한다.
2. `422`는 누락 필드·타입과 요청 자체를 확인한다. 사용자 데이터를 진단 로그에 붙이지 않는다.
3. `401/403`은 Actor 권한과 `audit_events`의 판단을 확인한다. 리소스 소유권을 숨기는 `404`를 존재 여부 확인 수단으로 사용하지 않는다.
4. 입력 수정·권한 요청은 사용자 행동이며 오류만으로 자동 반복 요청하지 않는다.

### PostgreSQL·S3 저장 실패

1. API Request ID 또는 Job ID에서 관련 Attempt 및 실패 단계(`processing`, `publication`)를 찾는다.
2. `make doctor --deep`으로 서비스 준비 상태를 읽기 전용으로 확인한다.
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

새 오류는 Error Catalog에 의미·메시지·재시도 정책을 등록한다. API·Web·Worker는 그 코드를 사용하고 `docs/testing.md`에 해당 계약의 테스트 책임을 명시한다. 변경 전후 OpenAPI 호환성, 기존 Alembic 데이터 보존, 권한·Snapshot 복구 테스트를 확인한다. 긴급 버그 수정도 로그만 보고 데이터나 Tag를 삭제하지 않는다.

### 운영자 원인 조회와 커밋 경계

- `GET /api/audit?request_id=<UUID>` 또는 `?job_id=<UUID>`는 `audit.read` 역할에서 과거 기록을 조회할 수 있다.
- `GET /api/diagnostics/jobs/{job_id}`는 동일한 감사 권한을 필요로 하며, 작업·실행 시도의 실패 단계, 분류된 원인 및 예외 **클래스명**만 반환한다. 원문 오류·원본 데이터·비밀은 반환하지 않는다.
- `iceberg_prepare` 중단은 Append 미진입이 확인된 실패, `iceberg_commit` 진입 이후는 실제 커밋 결과가 불확실한 실패로 분류한다. Worker가 예고 없이 종료되면 단계 확인 자체가 불가능할 수 있어 모든 실행 중 Iceberg 복구에는 보수적으로 수동 Snapshot 검증을 적용한다.
- `failure_category=unclassified`는 원인 미확정을 의미한다. 임의의 예외 메시지를 자동으로 노출하거나 분류하지 않는다.

### 로그 크기·보관 정책

- 로컬 개발 서비스의 stdout/stderr는 `scripts/log_capture.py`가 2 MiB씩 3개 백업 파일로 회전해 총 용량을 제한한다. 로그 relay와 실제 서비스는 기존 단일 프로세스 그룹 안에서 관리되므로 `make down`이 둘 다 종료한다.
- `make logs`는 파일 전체를 읽지 않고 마지막 64 KiB 범위에서 최대 70줄만 추출하며, 터미널 표시 전에 비밀 문자열을 마스킹한다. 백업 로그도 임의 삭제하거나 사용자 데이터 저장 경로에 접근하지 않는다.
