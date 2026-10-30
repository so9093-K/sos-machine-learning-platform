# 0020 — 온라인 서빙 운영 가시성과 실제 상태 정합성

- 상태: **Accepted**
- 관련: [ADR 0019](0019-ray-serve-online-serving.md), [Runtimes](../runtimes.md#온라인-서빙-runtime), [Getting Started](../getting-started.md#로컬-운영-도구), [Diagnostics](../diagnostics.md#온라인-서빙-장애전환-실패)
- 범위: Ray Dashboard 공개 범위, 실제 Serve Config와 의도의 대조, 서빙 자원 수용, API 준비 상태의 범위
- 대체: ADR 0019 결정 1의 "Dashboard 포트는 Compose 내부망에만 연다" 조항

## 배경

ADR 0019는 Ray Dashboard를 우회 경로로 보고 Compose 내부망에만 열었다. 누가 무엇을 봐야 하는지보다
위험을 먼저 따져 접근을 닫았고, 대신 볼 경로를 만들지 않았다. 실제로 배포 실패 원인 전체와 복제본
로그는 컨테이너 안의 Ray 세션 로그와 Ray의 Serve 상태에만 남고 `make logs`에는 남지 않는다.

같은 시기 운영 경로에서 다음 빈틈이 확인됐다.

- 컨트롤러는 애플리케이션 이름과 자신이 마지막으로 적용한 Config만 비교했다. 같은 이름에 다른 Config가
  적용돼도 수렴한 것으로 보았고, API는 활성 애플리케이션의 RUNNING만으로 `ready`를 판정했다.
- 복제본을 둘 CPU가 부족하면 Ray는 배치를 기다리며 DEPLOYING에 머물지만, SOS는 요청을 받은 뒤
  이유 없이 "준비 중"만 보였다. 전환은 새 판본을 기존 판본 옆에서 준비하므로 복제본이 하나 더 필요하다.
- `/api/ready`는 Job Worker가 멈추면 503을 반환했다. 온라인 추론은 Worker 없이 동작하는데도 API 전체가
  준비되지 않은 것으로 평가됐다.

## 결정

1. **보는 경로부터 정한다:** 소비자는 SOS API로 활성 판본과 준비 상태를, 소유자·관리자는 SOS 온라인
   서빙 화면과 API로 판본·실행 상태·상태 이유를, 로컬 운영자는 Ray Dashboard로 복제본 상태·실패
   메시지 전체·로그·자원 사용량을 본다.
2. **Ray Dashboard는 로컬 운영 도구다:** Dagster·MLflow·Feast UI와 같이 `127.0.0.1`에만 공개하고
   (`SOS_RAY_DASHBOARD_PORT`, 기본 9609), 같은 PC의 운영 계정에게 SOS 웹 링크를 제공한다. 신뢰 경계는
   호스트 운영자다. Ray 2.59 Dashboard는 브라우저발 요청에 GET·HEAD·OPTIONS만 허용하므로 다른 웹페이지가
   CSRF·DNS rebinding으로 배포나 작업 제출을 할 수 없다. 추론의 공식 경로는 SOS API이므로 Serve HTTP
   포트는 계속 공개하지 않는다.
3. **실제 Config로 판정한다:** 컨트롤러와 API는 같은 함수로 의도한 애플리케이션 Config를 만든다.
   컨트롤러는 Ray가 실제로 적용한 Config(`import_path`·`route_prefix`·`args`·`deployments`)가 의도와
   다르면 다시 적용한다. API는 이름이 같아도 Config가 다르면 그 애플리케이션을 `OUT_OF_SYNC`로 보고
   `ready`로 판정하지 않는다. 새 판본의 승격은 계속 실제 복제본의 설정 지문으로 확인한다.
4. **자원 수용은 접수할 때 판정한다:** 생성·전환·되돌리기·재개는 변경 뒤 서빙할 복제본 수(전환 대상
   포함)가 서빙 Runtime의 실제 CPU(Ray State API의 노드 자원)로 실행할 수 있는 수를 넘으면 사유와 함께
   거절한다. 판정은 DB 트랜잭션 잠금으로 직렬화한다. 수용량을 확인할 수 없으면 새 서빙 의도를 받지
   않는다. 그래도 자원을 기다리는 애플리케이션(예: Runtime CPU가 줄어든 뒤)은 그 이유를 소유자에게 보인다.
5. **로그로도 따라갈 수 있다:** 컨트롤러는 Config 적용, 애플리케이션 실패(상태가 바뀔 때 한 번), 판본
   전환을 식별자와 상태만으로 기록한다. 원인 원문은 로그에 쓰지 않는다.
6. **전체 장애와 기능 장애를 구별한다:** `/api/ready`는 API에 필요한 DB·객체 저장소가 준비되지 않을
   때만 503이다. Job 실행(Worker)과 온라인 서빙 Runtime은 API와 따로 실행되는 기능이므로 `features`에
   상태를 보고하고, 하나라도 멈추면 `degraded`(200)다.

## 근거

- 접근을 닫으면 운영자는 실패를 진단할 수 없다. 필요한 사람이 볼 경로를 먼저 만들고 그 범위를 통제한다.
  SOS는 이미 인증 없는 운영 도구를 localhost에만 공개해 운영하므로 Ray Dashboard만 다를 이유가 없다.
- SOS가 배포 의도의 정본이라면 Ray의 실제 Config와 대조해야 한다. 이름과 기억만으로는 직접 바뀐
  Config나 일부 적용을 놓친다.
- 무중단 전환은 여유 자원을 전제로 한다. 받을 수 없는 요청은 받는 순간 이유와 함께 거절해야 이전
  판본을 유지한 채 무한히 기다리는 상태를 만들지 않는다.
- 하위 기능의 장애로 API 전체를 내리면 독립적으로 동작하는 온라인 추론까지 배포 환경의 헬스체크에서
  제외될 수 있다.

## 영향

- `serving` 서비스가 Dashboard 포트를 `127.0.0.1:${SOS_RAY_DASHBOARD_PORT:-9609}`에 공개한다.
- 온라인 서빙 API의 실행 상태에 `runtime_application`과 `status_reason`(실패 원인 또는 대기 이유)이 있고,
  상태에 SOS 판정 `OUT_OF_SYNC`가 추가된다. 수용량 초과는 409로 거절된다.
- `/api/ready` 응답은 `status`(`ready`·`degraded`·`unavailable`), `services`(DB·S3), `features`(Job 실행·
  온라인 서빙)로 구성된다. Compose의 API 헬스체크는 Worker 장애로 실패하지 않는다.
