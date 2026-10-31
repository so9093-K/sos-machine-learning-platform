# 0019 — Ray Serve 기반 온라인 서빙

- 상태: **Accepted** · 웹 배포 진입 범위는 [ADR 0023](0023-explicit-model-deployment-entry.md)으로 대체 · Dashboard를 내부망에만 여는 조항은 [0020](0020-serving-operator-visibility.md)이 대체
- 관련: [Architecture](../architecture.md), [Contracts](../contracts.md#온라인-서빙-endpoint), [Runtimes](../runtimes.md#온라인-서빙-runtime), [ADR 0015](0015-mlflow-management-ownership.md), [ADR 0017](0017-model-lifecycle.md)
- 범위: 모델 판본의 동기 온라인 추론, Endpoint 의도·실제 실행 상태, 판본 전환·되돌리기·중단

## 배경

SOS의 모델 적용은 비동기 Scoring Job과 단일 입력 Playground Job뿐이었다. 관측 한 건의 점수가
바로 필요한 외부 시스템도 Dataset을 등록하고 Job 완료를 기다려야 했고, Playground에서 확인한
계산을 같은 계약의 API로 이어 쓸 수 없었다. Architecture의 Serving Runtime은 책임 경계로만
존재했다. 복제본 관리·라우팅·상태 확인·재시작을 SOS가 직접 구현하면 범용 서빙 프레임워크를
따로 유지하게 된다.

## 결정

1. **Serving Runtime 정본:** 복제본 실행·라우팅·Health·재시작은 Ray Serve가 맡는다. Ray Serve의
   Dashboard·HTTP 포트는 Compose 내부망에만 열고, 외부 소비자는 인증·권한·입력 검증을 거치는
   SOS API(`/api/serving/endpoints/...`)만 사용한다.
2. **의도와 실제 상태 분리:** SOS DB의 `serving_endpoints`는 Endpoint의 의도(서빙·중단,
   활성·전환 대상·직전 판본)만 보관한다. 실제 실행 상태는 요청마다 Ray Serve에서 조회하고
   DB에 저장하지 않는다.
3. **독립 컨트롤러:** `serving-controller` 프로세스가 의도를 Ray Serve 선언형 Config로 바꿔
   전체를 적용(PUT)하고, 이미 수렴했으면 아무것도 하지 않는다. Job Worker·MLflow·Dagster에
   의존하지 않으며, Ray Serve 장애 중에는 잠시 멈췄다가 다시 시도할 뿐 의도를 바꾸지 않는다.
4. **준비 확인 후 전환:** Endpoint·판본 쌍마다 별도 애플리케이션을 두어 새 판본을 기존 판본과
   함께 준비한다. 애플리케이션이 실행 중이고, 실제 복제본에 GET 요청해 받은 판본 ID와 설정 지문
   (import 경로·판본 기록·배포 설정의 SHA-256)이 의도와 일치할 때만 활성 판본을 바꾼다. 전환은
   조건부 갱신(CAS)이다. Ray가 설정 변경 직후 이전 설정의 상태를 실행 중으로 보고하는 구간이
   있으므로 상태 값만으로 판단하지 않는다. 준비에 실패하면 기존 활성 판본이 계속 요청을 처리하고
   소유자에게 실패 사유를 보인다. 되돌리기는 직전 판본을 같은 경로로 다시 준비하는 일이다.
5. **기존 신뢰 실행 재사용:** 복제본은 Worker와 같은 `load_trusted_isolation_forest`로 모델을
   복원하고 Playground와 같은 `score_observations`로 계산한다. 같은 `uv.lock`이 같은 Runtime을
   뜻하지 않으므로, 복원 시 판본에 기록된 학습 Runtime(Python minor·numpy·scikit-learn)과
   서빙 이미지의 실제 설치 판본, 아티팩트 SHA-256을 대조한다. 불일치하면 복제본이 시작되지 않고
   배포 실패로 사유와 함께 보고된다. 범용 추론 프레임워크는 만들지 않는다. 응답은 출력 유형
   (`output_type`)으로 출력 계약을 구별하며 현재 계약은 이상 점수(`anomaly_score_v1`)뿐이다.
   분류·회귀처럼 출력이 다른 모델을 서빙할 때 그 출력 계약을 함께 정의한다.
6. **접근:** 추론은 `model.infer` 권한(researcher·automation·administrator)이 필요하다.
   소유자·관리자가 아니면 활성 판본이 지금 제공 중인 Endpoint만 조회·호출할 수 있고 제공 중단은
   즉시 반영된다. 전환·되돌리기·중단·삭제는 소유자 또는 관리자(`model.manage`)만 한다.
   응답의 `owned`는 호출 계정의 소유 여부, `manageable`은 관리 가능 여부로 구분해, 관리자가 모든
   Endpoint를 볼 수 있다는 사실을 자기 Endpoint로 오인하지 않게 한다. 추론 요청은 Job이나 결과로
   저장하지 않는다.
7. **Registry 상태:** 추론과 Endpoint 생성·전환·되돌리기는 Playground·Scoring과 같은 Registry 검증
   (보관 여부·출처·SHA-256)을 통과한 판본만 쓴다. 매 요청 MLflow를 호출하지 않도록 API 프로세스가
   확인 성공을 최대 30초 재사용하며, 보관·등록 무효화는 그 안에 추론에 반영된다. MLflow 장애로
   확인할 수 없으면 다른 새 사용과 같이 거절한다. 서빙 Runtime은 MLflow와 무관하게 계속 실행되며
   MLflow가 복구되면 바로 다시 응답한다.
8. **수명주기:** Endpoint의 활성·전환 대상·직전 판본으로 참조되는 모델은 영구 삭제할 수 없다.
   Endpoint는 중단한 뒤에만 삭제한다. 모델 제공은 SOS에서 새 적용을 허용하는 일이며 서빙 배포와
   별개다.
9. **공급 경로:** 배포와 새 판본 전환은 모델 공급 과정(`scripts/supply_model.py --endpoint`)이나
   API로 요청한다. 공급 대상은 공급 계정이 소유한 같은 이름의 Endpoint이며 처음 정한 ID를 상태
   파일에 고정한다. 웹은 상태 확인과 되돌리기·전환 취소·중단·재개·삭제만 제공한다.

## 근거

- Ray Serve는 Kubernetes 없이 Compose에서 실행되고, 선언형 Config와 REST API로 의도를 반복
  적용할 수 있어 컨트롤러가 멱등하게 수렴시킬 수 있다. 여러 애플리케이션을 함께 두어 새 판본을
  기존 판본 옆에서 준비할 수 있고, 같은 Config로 이후 클러스터 실행에 옮길 수 있다.
- 추론을 API 프로세스 안에서 하면 API가 ML 의존성과 모델 메모리를 떠안아 제어와 실행 분리가
  깨진다. Worker Job으로 처리하면 동기 응답이 불가능하다.
- Python 복제본이 기존 신뢰 복원 함수를 그대로 쓰므로 Scoring·Playground·온라인 추론의 점수가
  같은 코드에서 나오고 복원 검증 규칙이 하나로 유지된다.

## 영향

- Compose에 `serving`(Ray head, `serving` dependency group 이미지)과 `serving-controller`가
  추가된다. Dockerfile은 `SOS_UV_GROUPS`로 이미지별 dependency group을 고른다.
- 온라인 추론은 요청당 최대 100건, 응답 대기 상한 `SOS_SERVING_TIMEOUT_SECONDS`(기본 10초)다.
- MLflow 장애가 30초 넘게 이어지면 온라인 추론도 새 요청을 거절한다. 이미 실행 중인 서빙 Runtime과
  Endpoint 의도는 바뀌지 않는다.
- 서빙 이미지의 Python minor·numpy·scikit-learn을 바꾸면 기존 판본은 새 복제본에서 복원되지
  않는다. 이미 실행 중인 애플리케이션의 Config가 바뀌면 Ray는 그 애플리케이션을 제자리에서 다시
  배포하고, 새 복제본이 실패하면 기존 복제본도 내려간다. Runtime을 바꿀 때는 새 Runtime에서 학습한
  판본으로 먼저 전환한 뒤 이미지를 교체한다.
- 현재 구성은 단일 Ray head 노드이며 판본당 복제본 하나(CPU 0.25)를 둔다.
