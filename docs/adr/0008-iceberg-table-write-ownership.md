# 0008 — Iceberg 테이블 쓰기 소유권과 Catalog 정체성

- 상태: Accepted · 관리자 채택·중단 재개 조항은 [0014](0014-remove-iceberg-adoption.md)가 대체
- 관련 결정: [0003 Snapshot 인증·권한](0003-snapshot-identity-audit.md), [0002 객체 보존](0002-object-lifecycle.md)
- 적용 범위: 현재 로컬 Iceberg SQL Catalog의 `default` 네임스페이스

## 배경

Dataset 참조의 `owner_actor`는 고정 Snapshot을 읽거나 연결하는 권한의 근거다.
그러나 하나의 테이블에 여러 소유자의 Dataset 참조가 있을 수 있고,
테이블 생성과 Snapshot 결과 게시 사이에는 실패·취소·재시도가 존재한다.
따라서 Dataset 참조의 존재만으로 쓰기 소유권을 추정할 수 없다.

## 결정

1. PostgreSQL의 `iceberg_table_owners`가 테이블 이름, 단일 쓰기 소유 Actor,
   무작위 고유 토큰·물리 Table UUID와 예약/활성 상태를 관리한다. 이름의 유일성으로 사용자 간
   동시 예약을 직렬화하며, Snapshot Dataset 등록은 쓰기 권한을 부여하지 않는다.
2. API는 정상 Dataset 소유권·Job 제출 키를 확인한 뒤 실제 Catalog의 테이블
   존재 여부와 소유권을 확인한다. 새로운 이름은 Job 멱등성 확인 이후 같은
   PostgreSQL 트랜잭션에서 예약한다. 실패한 Job·키 충돌은 테이블명도 남기지 않는다.
3. Worker는 Catalog에 테이블을 만들 때 `sos.write-token` 속성을 기록한다.
   기존 테이블에 Append하기 전에 PostgreSQL 예약 Actor·Catalog 토큰·물리
   Table UUID가 모두 일치해야 한다. Worker는 Job 제출 Actor를 기준으로 재검증한다.
4. 기존 레지스트리나 Table Property가 없는 테이블은 소유자가 누구인지
   자동 추측하지 않는다. 테이블이 사라진 Active 예약도 자동으로 새 테이블을
   만들지 않는다. 기존 Snapshot 조회·Tag 보호·참조는 보존하며 쓰기만 차단한다.
5. 테이블에 대한 관리자 조회/진단 권한은 자동 쓰기 권한이 아니다. 로컬 관리형
   환경에서 기존 Dataset 참조가 모두 동일한 명시적 Actor에게 귀속되는 경우만
   별도 읽기 전용 진단을 제공한다. 운영자가 Table UUID·최신 Snapshot ID와
   소유자 Dataset ID를 명시적으로 확인해 실행한 경우에만 쓰기 소유권 채택을
   허용한다. 중단된 채택은 동일 Dataset ID·Table UUID일 때만 재개한다. 모호한 소유권·기존 토큰 충돌·참조 없음은 자동 이전하지 않는다.
   일반 API에서의 테이블 인수나 소유권 양도는 제공하지 않는다.
6. API의 권한 검사는 빠른 거부와 명확한 오류를 위한 것이며, 실제 데이터 격리는
   Worker의 동일 토큰 확인과 Catalog의 Snapshot 커밋 계약으로 다시 보호한다.
   Catalog에 직접 쓰기 권한을 가진 외부 관리자에 대한 완전한 격리는 별도
   Catalog 권한·배포 계약을 필요로 한다.

## 장애 및 한계

예약 이후 Job 제출이나 Catalog 생성이 실패하면 Reserved 레코드가 남을 수 있다.
Catalog 테이블이 동일 예약 토큰으로 이미 생성됐다면 물리 UUID를 원자적으로
고정하고 활성화할 수 있다. 활성화된 테이블은 같은 이름·토큰이라도 UUID가
다르면 새 테이블로 간주하고 쓰기를 거부한다. 이 레코드는 다른 Actor에게
소유권을 양도하지 않으며 자동 삭제하지 않는다.
오류 후 재시도도 테이블 정체성 및 Iceberg 커밋 불확실성 검증을 우회하지 않는다.
기존 테이블 채택 시 PostgreSQL에 `Adopting` 예약·감사를 먼저 기록하고 Catalog
Table Property에 소유 토큰을 기록한 뒤 PostgreSQL 상태·감사를 확정한다.
중간 실패는 자동 삭제·다른 Actor 이전 없이 보류하고, 같은 소유자·Dataset·
Table UUID·최신 Snapshot ID를 재검증한 명시적 재실행으로만 복구한다.
채택은 Catalog Snapshot 데이터와 Tag를 수정하거나 삭제하지 않는다.

현재 예약 정책은 테이블명 선점에 대한 계정별 쿼터와 관리자 복구 UI를 제공하지
않는다. 여러 Actor의 읽기 참조·소유자 없는 참조·근거 없는 기존 테이블은
자동 채택할 수 없다. 외부 관리형 DB에서의 소유권 이전과 다중 호스트의 모든
외부 Catalog 작성자에 대한 분산 배타 잠금도 제공하지 않는다.

관련 회귀는 교차 사용자 쓰기 거부, Worker 경유 검증, 레거시 거부,
정상 신규 생성·동일 Actor Append 및 기존 Snapshot 보존을 대상으로 한다.
