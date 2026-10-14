# SOS Machine Learning Platform — Platform Identity

> **Explore · Experiment · Understand · Evolve**
>
> 연구, ML 실행, 외부 서비스 활용을 연결하는 확장 가능한 머신러닝 플랫폼.

## 1. 제품 정체성

![Research Platform · ML Execution Platform · ML Service Platform](references/02-platform-pillars.png)

| 영역 | 제공 가치 |
| --- | --- |
| **Research Platform** | 데이터 탐색, 가설·모델·아키텍처 연구, 실험 비교 및 연구 지식 축적 |
| **ML Execution Platform** | 데이터 처리, 피처 엔지니어링, 학습, 최적화, 평가, 모델 관리, 추론·배포 |
| **ML Service Platform** | 외부 애플리케이션, 서버, IoT/MES 시스템, AI 에이전트에 ML 기능 제공 |

## 2. 지속적 개선 과정

![Continuous ML Lifecycle](references/01-continuous-improvement.png)

- **Research & Experiment:** 가설, 모델 설계, 연구, 실험 비교
- **Training & Evaluation:** 전처리, 학습, 최적화, 평가, 버전 관리
- **Deployment & Inference:** REST, Batch, Streaming, Edge
- **Observation & Feedback:** 운영 결과, 오류 사례, 사용자 피드백
- **Continuous Improvement:** 축적된 근거를 새로운 탐구·실험·개선에 반영

각 과정은 독립적으로 활용 가능하며 고정된 순서를 강제하지 않는다.

## 3. 연구 경험

![Experiment Workflow — Fork, Compare, Reproduce, Understand, Explore, Apply](references/03-research-experience.png)

| 기능 | 사용자 행동 |
| --- | --- |
| **Fork** | 이전 실험을 기반으로 새로운 실험 생성 |
| **Compare** | 설정, 데이터, 모델, 결과의 차이 비교 |
| **Reproduce** | 기록된 조건으로 실험 재현 |
| **Understand** | 실험 결과와 변화 원인 탐색 |
| **Explore** | 모델의 동작을 직접 테스트 |
| **Apply** | 연구 결과를 재사용하고 다른 환경에 적용 |

성공한 실험뿐 아니라 실패한 실험의 조건과 결과도 이후 연구에 활용할 수 있도록 보존한다.

## 4. 플랫폼 접근

![Human · External App · AI Agent → Common ML Platform API](references/04-common-platform-api.png)

- **Human:** Web UI, Playground
- **External App:** API, SDK, Events
- **AI Agent:** API, Tools

학습·평가·모델 관리·추론 등 핵심 기능은 특정 UI에 종속되지 않는다. 외부 시스템은 플랫폼을 통해 모델 학습을 요청하고, 등록된 모델을 사용하며, 운영 피드백을 전달할 수 있다.

## 5. 시스템 경계

![ML Platform and Pluggable Execution Infrastructure](references/05-platform-overview.png)

- **ML Platform:** 공통 프로젝트, 실행, 모델, 배포 API
- **Execution Infrastructure:** Training Workers, Serving Workers, Storage, Connectors
- **확장성:** 새로운 모델·알고리즘·학습 방식·프레임워크·실행 환경을 수용
- **재사용성:** 데이터 변환, 모델, 평가, 실행 결과, 배포 구성을 재사용 가능한 단위로 관리

## 설계 기준

1. **사용자 가치:** 기술이나 라이브러리의 기능 자체보다 연구·실행·연동 경험을 우선한다.
2. **연구의 자유:** 현재 지원하는 모델 및 프레임워크가 새로운 접근법을 제한하지 않는다.
3. **연속성:** 데이터, 설정, 실험, 모델, 실행 환경 및 피드백의 관계를 추적하고 재현할 수 있도록 한다.
4. **API-first:** 웹 UI와 외부 시스템은 동일한 플랫폼 기능을 활용한다.
5. **분리와 재사용:** 플랫폼 기능과 실행 인프라를 분리하고, 컴포넌트를 다른 실험·서비스에서도 활용할 수 있게 한다.
6. **지속적 진화:** 새로운 근거와 기술에 따라 구현과 사용자 경험을 개선한다.

이 문서는 **제품 정체성과 설계 방향**을 정의한다. 특정 라이브러리, 모델 구조, 프레임워크 버전, 화면 구성 및 배포 기술을 고정하는 구현 명세가 아니다.
