# 아키텍처

SOS는 **Research Platform · ML Execution Platform · ML Service Platform**을 공통 API로 연결한다. 논리적인 책임을 분리하되, 이를 처음부터 별도 서버·프로세스로 배포해야 한다는 뜻은 아니다.

## 논리적 구성과 경계

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

## 현재 코드의 모듈 경계

`processing.py`는 Polars LazyFrame과 변환 계약만을 입력으로 사용하며 DB·HTTP·S3에 의존하지 않는다. `datasets.py`는 Dataset 참조의 조회·등록과 Iceberg 적재를 연결하고, `object_store.py` 및 `store.py`가 실제 저장 구현을 담당한다. UI의 예제·작업 화면도 일반 API를 사용하며 별도의 가짜 실행 경로를 만들지 않는다.

장기적인 도메인 경계는 이 실행 형태에 종속되지 않는다. 새로운 Catalog나 저장소 제공자를 추가할 때 Dataset과 Job의 의미를 유지하고, 실제 교체 필요성이 있는 경계에만 어댑터를 도입한다.

S3 전송 전 객체 게시 의도를 PostgreSQL `object_writes`에 기록하고, Dataset과 객체의 확정 상태를 하나의 DB 트랜잭션으로 갱신한다. Transform 결과는 Attempt별 불변 경로를 사용한다. 읽기 전용 Reconciler는 `datasets/`와 `results/` 객체 목록을 페이지 단위로 조회하고, `object_observations`에 날짜 간 관측 근거를 남긴다. 이는 실제 삭제 승인이 아니다. Iceberg Snapshot 보호 여부는 별도 Catalog 검사로 확인하며 범용 S3 정리 범위에서 제외한다. 장기 계약은 [ADR 0002](adr/0002-object-lifecycle.md)를 따른다.
