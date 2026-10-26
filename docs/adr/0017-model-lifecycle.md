# 0017 — 목적별 모델 그룹과 모델 자산 삭제 계약

- 상태: **Accepted**
- 관련: [ADR 0015](0015-mlflow-management-ownership.md), [ADR 0016](0016-dagster-orchestration.md)

## 배경

초기 Isolation Forest 학습 결과는 Actor·알고리즘 단위의 MLflow Registered Model 한 개에 계속 새 버전으로 등록된다. 목적이 다른 모델이 동일 버전 목록에 쌓이므로 표시 이름·보관·재사용·삭제의 사용자 경험이 불명확해진다. 모델 파일·Scoring·평가·설명은 PostgreSQL과 S3 및 MLflow에 나뉘어 있으며 하나의 트랜잭션으로 제거할 수 없다.

## 결정

1. **논리적 그룹과 버전:** MLflow Registered Model을 소유 Actor별 목적 그룹으로 사용한다. 새 학습은 안전한 `registry_key`를 선택하며 동일 키의 새 학습은 새로운 MLflow Model Version이 된다. 기존 모델의 그룹 키는 `default`로 유지하며 MLflow 이름·기존 버전 번호를 재발급하지 않는다.
2. **관리 정본 유지:** 모델 그룹 이름·설명·보관 여부, 모델 버전 및 보관 Tag는 MLflow 정본이다. SOS에 별도 Registry/Model 이름 관리 테이블을 만들지 않는다. 표시 이름 변경은 기존 불변 모델 바이트·Feature·설정·Dataset 판본을 수정하지 않는다.
3. **버전 보관과 실제 사용:** `sos.archived` Tag가 지정된 Registry 그룹 또는 버전은 신규 Playground·Scoring에 사용하지 않는다. 복원하면 기존 고정 버전의 검증을 다시 수행한다. 보관은 아티팩트 삭제가 아니다.
4. **모델 영구 삭제:** 사용자가 명시적으로 확인하면 MLflow 등록 버전과 SOS 소유 모델 아티팩트(버전된 S3 객체 포함)를 제거한다. 진행 중인 Job·공유 아티팩트 및 MLflow 등록 미완료 상태에서는 거부한다. 삭제할 수 있는 모델만 허용하고, 다른 Actor의 객체나 외부 소유 Storage를 조용히 삭제하지 않는다.
5. **역사적 결과의 보존:** 이미 확정된 Scoring Result·Decision Policy·Evaluation·Explanation·Job/Attempt 및 감사 이력은 독립 결과로 유지한다. 관계형 참조를 깨뜨리지 않도록 SOS Model Revision은 `Deleted` tombstone으로 남겨 사용 불가와 이전 학습 근거를 표현한다. 삭제된 모델을 통한 새 추론을 허용하지 않으며 삭제된 모델 파일이 로드 가능한 것으로 표시하지 않는다. 학습/평가 결과를 함께 영구 삭제하는 별도 수명주기는 이 변경의 범위에 넣지 않는다.
6. **다중 정본의 삭제 경계:** `Active → Deleting → Deleted`는 외부 파일 삭제의 의도를 기록하는 SOS 상태이며 MLflow Registry의 버전 정본을 복제하지 않는다. 모델 사용 요청과 삭제 의도 기록은 PostgreSQL 행 잠금을 사용해 경합을 제한한다. MLflow 삭제와 S3 삭제 후에만 완료를 확정하고, 실패하면 `Deleting`을 남겨 같은 요청으로 재시도한다. S3 Object Lock·외부 백업·관리 대상 외 사본까지 물리 소거를 보장하지 않는다.
7. **버전 삭제와 그룹 삭제:** 개별 버전 삭제가 모델 아티팩트·Registry 버전을 제거한다. 그룹 삭제는 기본 그룹을 제외하고 현재 Registry 버전이 없는 경우에만 수행한다.
8. **실행 환경 재현성:** Dagster Python Runtime은 API·Worker와 동일한 `pyproject.toml` 및 `uv.lock`에서 전용 dependency group을 선택한다. Dagster 의존성은 API/Worker Runtime에 설치하지 않는다. 현재 PostgreSQL 드라이버는 upstream 의존성과 맞는 psycopg2를 유지한다.

## 영향과 검증

모델 그룹·보관·삭제는 권한 있는 Actor가 SOS API를 통해 조작하고, UI는 모델 선택과 학습 설정을 구분한다. 모델 파일 삭제 이후에는 Registry의 버전 링크가 사라질 수 있으나, 기존 Scoring의 평가 근거는 실제 생성 당시의 모델 ID를 유지한다. 실제 물리 삭제와 다중 서비스 실패 복구, 버전 관리된 S3 저장소, 외부 소비자 중단은 배포 환경에서 직접 확인한다. 검증하지 않은 인프라 특성을 보장하지 않는다.
