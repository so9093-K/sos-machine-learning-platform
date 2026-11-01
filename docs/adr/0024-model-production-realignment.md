# 0024 — 모델 생산과 SOS 모델 연결의 책임 분리

- 상태: **Accepted**
- 대체: [ADR 0016](0016-dagster-orchestration.md)의 SOS 제출·단계 Job 조합(결정 3·5·6), [ADR 0021](0021-analysis-topic-contract.md) 결정 2의 등록 Pipeline
- 관련: [Architecture](../architecture.md), [Contracts](../contracts.md), [Workflows](../workflows.md)

## 배경

등록 Pipeline은 SOS API로 제출되고, Dagster의 각 단계가 SOS Job(사전 검사·학습·Scoring·정책·평가)을
만들었다. 모델 생산이 SOS의 Dataset·Job·권한 경계에 묶여 Dagster가 SOS DB 자격 증명으로 제출 기록을
읽어야 했고, Workflow 제출 상태가 SOS에 따로 남았다. Model Revision은 SOS 학습 Job과 학습 Dataset을
전제로 해, SOS 밖에서 학습·평가·등록한 모델은 SOS에서 제공·적용·서빙할 수 없었다.

## 결정

1. **생산은 생산 Pipeline이 한다:** 데이터 준비·학습·평가·MLflow 등록은 Dagster 등 생산 Pipeline이 직접
   실행한다. 생산 Pipeline은 SOS DB·저장소에 접근하지 않고 SOS 제출 없이 실행된다.
2. **SOS는 고정 버전을 연결한다:** SOS는 MLflow에 등록된 모델 버전을 다시 학습하지 않고 Model Revision으로
   연결한다(`ml_import` Job). 버전은 번호로 고정하며 별칭·최신 버전으로 연결하지 않는다. 연결 Job을 학습
   Job으로 표현하지 않는다.
3. **Model Revision은 출처를 가진 독립 자산이다:** 출처는 SOS 학습(`sos_training`, 학습 Dataset·판본) 또는
   MLflow 연결(`mlflow_registry`, 모델·버전·Run과 생산 측 학습 근거) 중 하나다. 판본의 Job·Attempt는 판본을
   게시한 SOS Job이다.
4. **연결은 모델 패키지 계약을 검증한다:** 연결 대상 버전은 `sos_model.json`(입력 서명·자료형, 학습 중앙값
   전처리, 매개변수, 학습 근거, 생산 Runtime, 검증 표본)과 `estimator.skops`를 가진다. SOS는 허용한 타입만
   복원하고, 전처리는 기록한 중앙값으로 다시 구성하며, 검증 표본의 점수를 SOS Runtime에서 재현한 뒤에만
   게시한다. 모델 관리 권한이 SOS 실행 환경의 코드 실행 권한이 되지 않게 한다. SOS가 학습한 joblib 판본은
   신뢰된 SOS 실행 환경만 쓴 아티팩트이므로 기존 복원 계약을 유지한다.
5. **연결 후에는 같은 판본 경로를 쓴다:** 연결한 아티팩트는 게시 Attempt의 SOS 저장 위치에 두고, 소유자의
   SOS 모델 그룹에 MLflow 버전으로 등록하며 원본 모델·버전·Run을 출처 태그로 남긴다. 제공·Scoring·온라인
   서빙·영구 삭제는 SOS 학습 판본과 같은 경로를 쓴다.
6. **같은 의미의 학습 근거:** 전처리·학습 근거(학습 점수 요약·입력 분포)·모델 패키지는 SOS 라이브러리
   함수를 생산 Pipeline과 SOS가 함께 쓴다. 생산 Pipeline의 Runtime(Python minor·numpy·scikit-learn)은 SOS와
   같아야 한다.
7. **SOS 내부 학습은 독립 기능이다:** Dataset으로 SOS에서 학습하는 기능은 유지한다.
8. **SOS Pipeline 제출 경로를 제거한다:** Pipeline 제출·조회 API와 화면, Dagster GraphQL 연결, Workflow
   제출 기록과 Job의 Workflow 출처를 제거한다. 기존 제출 기록은 감사 이벤트로 남기고 단계 Job과 결과
   판본은 보존한다. 이 전환은 되돌리지 않는다.

## 근거

생산 Pipeline과 SOS가 같은 데이터·Job 상태를 함께 소유하지 않으므로 이중 정본과 DB 자격 증명 공유가
없다. SOS는 생산 경로와 관계없이 같은 판본 계약으로 모델을 제공·적용·서빙·이해하게 한다. 외부 모델을
받으면서도 코드 실행이 가능한 직렬화 형식을 받지 않아, 모델 관리 권한과 실행 환경 권한을 분리한다.

## 영향

Dagster는 MLflow와 SOS API 자격(automation 서비스 계정)만 가진다. 생산 Run의 성공, MLflow 등록, SOS 연결,
SOS Registry 등록은 각각 다른 상태다. 연결한 판본에는 SOS 학습 Dataset이 없으므로 학습 시간 경계를
재사용하는 시간 구간 Scoring을 쓸 수 없다.
