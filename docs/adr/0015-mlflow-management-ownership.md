# 0015 — MLflow 모델·실험 관리 정본 전환

- 상태: Accepted
- 이전 결정: [0012](0012-ml-experiment-boundaries.md)의 선택적 Tracking 및 자체 모델 관리 책임 범위 대체

## 배경

SOS에서 관리용 실험 검색·Run 비교·모델 등록·버전 관리까지 독자 구축하면
MLflow와 유사한 역할이 중복된다. 초기 개발 프로젝트에는 유지할 운영 데이터나
외부 구형 API 소비자가 없으므로 영구 호환 계층을 구현하지 않는다.

## 결정

1. MLflow는 Experiment·Run·Registered Model·Model Version·Alias의 관리 정본이다.
2. SOS는 Dataset·Job/Attempt·Scoring·Policy·Evaluation·Actor 권한·신뢰된 모델
   아티팩트의 불변 실행 참조를 책임진다. SOS Model Revision은 별도의 모델 Registry가 아니다.
3. SOS Worker는 학습 완료와 외부 Registry 등록을 구별한다. 성공한 학습의 상태는
   외부 실패로 뒤집지 않고, 등록 완료 전 신규 모델 Scoring/후속 Pipeline을 진행하지 않는다.
4. 학습 Job ID와 아티팩트 SHA로 중복 Model Version 등록을 탐지한다. 두 저장소의
   원자적 exactly-once를 주장하지 않는다.
5. MLflow 관리 UI는 개발 환경에서 운영자 전용으로 제한한다. 사용자 대상 조회는
   SOS API가 Actor·실제 Job 소유권·Registry 메타데이터를 대조한 후 제공한다.
6. 변경 가능한 Alias는 실행 시 고정 버전으로 확정하며, 직렬화 모델 복원은
   SOS가 발급한 신뢰된 아티팩트만 Runtime·Feature·무결성을 검증한 후 허용한다.
7. 구형 구조를 대체한 중복 관리 기능·설정·UI는 제거한다. 기존 데이터의
   복잡한 이관과 사용되지 않는 호환 계층은 만들지 않는다.

## 근거와 영향

SOS는 연구·실행·활용의 공통 플랫폼 계약에 집중하고, 전문 실험·모델 관리
기능은 MLflow에 위임한다. 외부 관리 장애는 등록 지연과 재시도를 발생시킬 수
있으므로 Job/Attempt와 모델 사용 가능 상태의 의미를 구분해야 한다.
