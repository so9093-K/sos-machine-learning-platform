# Platform Identity

이 문서는 SOS Machine Learning Platform의 **지속적으로 지켜갈 제품 방향**을 정의합니다.
사용 기술, 모델 구현, 내부 아키텍처, 화면 디자인은 목적에 맞게 발전할 수 있습니다.

## 세 가지 제품 정체성

1. **Research Platform:** 가설을 세우고, 모델·아키텍처를 연구하고, 실험을 비교하며 지식을 축적합니다.
2. **ML Execution Platform:** 데이터 처리, 피처 엔지니어링, 학습, 최적화, 평가, 저장, 추론·배포 기능을 재사용 가능한 능력으로 제공합니다.
3. **ML Service Platform:** 외부 애플리케이션, 서버, IoT/MES 시스템, AI 에이전트가 학습·평가·추론 기능을 자신의 시스템에서 사용하도록 합니다.

## 지속적인 가치 창출

**Explore → Experiment → Evaluate → Apply → Observe → Evolve**

연구·학습·배포·관찰의 결과는 다음 탐구와 실험으로 이어집니다.
각 단계는 필요에 따라 독립적으로 접근할 수 있으며, 모든 사용 사례에 순차 실행을 강제하지 않습니다.

## 연구 경험

- **Fork:** 기존 실험에서 새로운 시도 시작
- **Compare:** 실험의 조건과 결과 비교
- **Reproduce:** 이전 실험의 조건으로 재현
- **Understand:** 결과와 차이 이해
- **Explore:** 모델을 직접 시험하고 탐구
- **Apply:** 결과를 다른 환경에서 재사용·적용

## 접근 방식

- **Human:** Web UI·Playground
- **External App:** API·SDK·Events
- **AI Agent:** API·Tools

이들은 공통된 프로젝트·학습·모델·평가·추론 기능을 공유하며, 실행 인프라와 사용자 인터페이스는 분리합니다.

## 지켜야 할 것과 바꿀 수 있는 것

- **지켜야 할 것:** 탐구의 자유, 지식의 연속성, 재사용성, 외부 활용성, 사용자 가치 중심 설계.
- **바꿀 수 있는 것:** 프레임워크 버전, 라이브러리, 상세 아키텍처, 개별 UI 컴포넌트 및 구현 방법.
- 현재 방법을 정답으로 고정하지 않으며, 더 나은 근거가 생기면 구현을 발전시킵니다.

## 기준 이미지

기준 이미지 원본을 `references/`에 아래 이름으로 보관합니다. 이 그림은 제품 방향을 표현하며,
기술 구현이나 고정된 실행 순서를 강제하는 상세 설계도가 아닙니다.

1. `01-continuous-improvement.png` — 연구·학습·운영 피드백의 순환
2. `02-platform-pillars.png` — 세 가지 제품 정체성
3. `03-research-experience.png` — Fork · Compare · Reproduce · Understand · Explore · Apply
4. `04-common-platform-api.png` — 사람·외부 서비스·에이전트의 공통 API
5. `05-platform-overview.png` — 사용자 경험과 실행 인프라의 분리

> 최초 Draft PR에서 PNG 원본 업로드를 별도로 확인합니다. 이미지가 준비되기 전에는 이 목록만을 기준으로 사용합니다.
