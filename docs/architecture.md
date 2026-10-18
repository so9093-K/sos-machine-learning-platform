# 아키텍처

SOS는 **Research Platform · ML Execution Platform · ML Service Platform**을 공통 API로 연결한다. 논리적인 책임을 분리하되, 이를 처음부터 별도 서버·프로세스로 배포해야 한다는 뜻은 아니다.

## 논리적 구성과 경계

![SOS Control Plane과 교체 가능한 Training·Serving Runtime의 책임 분리](references/control-plane-runtime-architecture.png)

이 그림은 목표 책임 경계를 표현한다. **Control Plane은 모델·실행·평가·배포의 제어 상태**, 실제 Training·Serving Runtime은 격리된 실행을 담당한다. 그림에 적힌 프레임워크·스토리지 제품은 예시이며 필수 기술 스택이 아니다. 현재 구현되지 않은 모델 운영 능력도 포함한 장기 논리 구조다. Data Explorer와 Model Playground는 별도 플랫폼이 아니라 같은 API·권한·모델 판본을 사용하는 경험이다.

```text
Human / External System / AI Agent
                 |
        Common Platform API
                 |
       Application / Control
  (projects, experiments, jobs,
   models, policy, metadata)
                 |
         Execution Boundary
            /         \
   Training/Eval     Inference
      Workers         Workers
            \         /
      Artifacts / Data / Tracking
```

- **공통 API:** 입력 검증, 인증·권한 적용, 작업 제출·조회, 결과 접근을 일관되게 제공한다. UI는 이 계약을 통해 기능을 사용한다.
- **Application / Control:** 프로젝트·실험·작업·모델의 논리적 관계와 접근 정책을 관리한다. ML 알고리즘을 직접 실행하는 코드와 분리한다.
- **Execution Boundary:** Job과 Runtime 요구를 실제 실행기로 전달하고, 상태·결과·오류·취소를 연결한다. 특정 큐·스케줄러 제품의 개념을 공통 도메인 계약으로 노출하지 않는다.
- **Workers:** 데이터 처리, 학습, 평가, 모델 복원 및 추론을 실제로 수행한다. 프레임워크별 실행 구현이 플랫폼 코어를 변경하도록 요구하지 않는다.
- **Data / Artifacts / Tracking:** 데이터 참조, 변경 불가능한 결과물, 실행·평가 이력을 보존한다. 메타데이터와 대용량 아티팩트의 저장 방식은 별개의 책임이다.

## 구조적 불변 조건

1. **API-first:** 웹 UI, 외부 시스템, AI 에이전트는 같은 핵심 기능과 권한 모델을 활용한다.
2. **제어와 실행 분리:** 장시간 소요되는 학습을 API 요청 처리에 묶어 두지 않는다. 짧은 호출과 비동기 Job을 작업 특성에 맞게 구분한다.
3. **도메인과 인프라 분리:** 모델·실험·작업의 의미는 저장소, ML 추적 도구, Worker 엔진에 종속되지 않는다. 구체적 구현은 경계의 어댑터로 연결한다.
4. **추적 가능성:** 작업 요청부터 실제 실행 시도, 데이터·모델·평가·아티팩트까지 식별자와 참조 관계로 연결할 수 있어야 한다.
5. **보안 경계:** 사용자 권한, 작업별 자원, 비밀정보와 외부 모델·플러그인 실행 권한을 구분한다. 단순한 파일 저장이나 모델 로드는 신뢰할 수 있는 코드 실행과 같지 않다.
6. **점진적 배치:** 로컬 단일 환경으로 시작할 수 있지만 논리적 분리가 유지되어 원격 Worker 및 독립 Serving으로 확장할 수 있어야 한다.

[Experience](experience.md)는 사용자 목표, [Contracts](contracts.md)는 객체와 인터페이스의 의미, [Workflows](workflows.md)는 상태와 흐름, [Runtimes](runtimes.md)는 실행 환경을 정의한다. 이 문서는 책임과 의존 방향을 다루며 API 필드·데이터베이스 테이블·배포 기술을 고정하지 않는다.

## 데이터 저장소와 실행 제어

플랫폼 메타데이터(Dataset, Job, Execution Attempt)는 PostgreSQL에 영속화하고, 실제 원본 데이터·변환 결과는 S3 호환 객체 저장소에 보관한다. 서로 다른 저장소의 원자성을 가정하지 않으며, 게시 실패 시 공유 가능한 원본 객체와 Attempt별 결과 객체의 소유권·보존 및 복구 정책을 구분한다. Iceberg Catalog는 테이블·Snapshot의 메타데이터를 담당하며 데이터 파일은 객체 저장소를 활용한다. 공통 Dataset 계약은 특정 제품의 내부 객체에 종속되지 않는다.

Makefile과 개발 CLI는 서비스 기동·진단의 인터페이스이자 배포별 어댑터다. 개발 환경의 Compose 구성이 플랫폼 서비스의 의존 방향을 결정하지 않는다. 외부 관리형 데이터베이스·스토리지 또는 독립적으로 배치된 Worker를 사용할 수 있으며, 실행 중(Running)과 실제 서비스 준비(Ready)는 구별하여 판정한다.

## 범용 Dataset Understanding 책임

DuckDB 기반 Profiler는 원본 판본에서 통계·품질을 계산하며 역할 추론과 추천 규칙은 독립된 순수 정책으로 분리한다. 실행과 취소는 기존 Job/Attempt를 재사용하고 완료된 Profile은 PostgreSQL에서 원본과 별도로 추적한다. 웹과 외부 클라이언트는 동일한 API를 사용한다. 물리적 저장소나 특정 모델·LLM의 추론을 필수 조건으로 삼지 않는다.

## 현재 코드의 모듈 경계

`discovery.py`는 DuckDB를 사용해 파일 Dataset·고정 Iceberg Snapshot의 Preview·EDA·Profile·집계를 전담한다. 파일 입력 형식별 검증·DuckDB/Polars 읽기 설정은 `file_formats.py`의 제한된 Reader 등록 지점이 소유한다. 등록된 Reader의 의미는 Dataset에 귀속된 해석 설정을 따르며 새로운 포맷을 이유로 Discovery·Preparation의 공통 작업 계약을 변경하지 않는다. `processing.py`는 Polars Feature·Transform 의미를 담당하며 DB·HTTP·S3에 의존하지 않는다. `datasets.py`는 Dataset 등록·검증된 원본 캐시·Iceberg 적재를 연결하고, `object_store.py`가 S3 전송을 관리하고 `store.py`의 공통 트랜잭션 경계와 `store_objects.py`·`store_snapshots.py`·`store_datasets.py`·`store_jobs.py`·`store_administration.py`가 도메인별 PostgreSQL 상태를 관리한다. `resource_admission.py`는 Discovery·Worker 공통 논리 메모리 수용 판단을 담당하고, 실제 RSS 감시와 프로세스 실행은 Worker의 책임이다. UI의 예제·작업 화면도 일반 API를 사용하며 별도의 가짜 실행 경로를 만들지 않는다.

장기적인 도메인 경계는 이 실행 형태에 종속되지 않는다. 새로운 Catalog나 저장소 제공자를 추가할 때 Dataset과 Job의 의미를 유지하고, 실제 교체 필요성이 있는 경계에만 어댑터를 도입한다.

Iceberg 테이블의 쓰기 소유권과 Catalog 정체성은 별도 `iceberg_table_owners` 레지스트리와 Iceberg Table Property의 무작위 토큰을 대조한다. Snapshot Dataset 참조는 읽기·보존의 근거이지 append 소유권이 아니다. 기존 레지스트리가 없는 테이블은 자동 소유권 이전 없이 쓰기를 거부한다. 세부 결정은 [ADR 0008](adr/0008-iceberg-table-write-ownership.md)을 따른다.

S3 전송 전 객체 게시 의도를 PostgreSQL `object_writes`에 기록하고, Dataset과 객체의 확정 상태를 하나의 DB 트랜잭션으로 갱신한다. Transform 결과는 Attempt별 불변 경로를 사용한다. 읽기 전용 Reconciler는 `datasets/`와 `results/` 객체 목록을 페이지 단위로 조회하고, `object_observations`에 날짜 간 관측 근거를 남긴다. 이는 실제 삭제 승인이 아니다. Iceberg Snapshot 보호 여부는 별도 Catalog 검사로 확인하며 범용 S3 정리 범위에서 제외한다. 장기 계약은 [ADR 0002](adr/0002-object-lifecycle.md)를 따른다.

## 인증·보존·감사 경계

API는 동일한 Actor·Action·Resource 권한 판단을 거쳐 Dataset·Job·Storage에 접근한다. 현재는 로컬 개발 신원과 사설 인증 검증용 불투명 Bearer 토큰 지문 검증을 지원하며, OIDC 로그인·TLS·외부 공개용 배포 경계는 구현하지 않았다. 인증 제공자 구현과 도메인 권한을 분리한다. PostgreSQL은 보존 의도·보안 감사·Dataset 소유권의 정본이고 Iceberg Catalog는 Snapshot Tag의 실제 유효 상태를 판단한다. 외부 Catalog 커밋을 PostgreSQL 트랜잭션과 동일시하지 않는다.
