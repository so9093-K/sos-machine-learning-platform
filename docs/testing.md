# 테스트·검증·CI

## 목적

SOS 검증은 제품 계약에서 발견할 가치가 있는 실제 실패를 확인하기 위한 것이다. 이전 테스트의 수·명칭·구현이나 제거된 회귀 범위는 신규 검증을 결정하는 기준이 아니다. 검증이 완전하지 않은 상태를 허용하되 미실행을 성공으로 표현하지 않는다.

## 책임 구조

![테스트 정의·공통 검증 CLI·Native Runner·결과·CI 경계](references/test-engineering-architecture.png)

- **검증 계약·정책:** 제품 책임에서 무엇을, 왜, 언제 확인할지 결정한다. 별도 테스트 등록부를 만들지 않는다.
- **공통 CLI:** `scripts/validate.py`가 Suite를 선택하고 Native Runner를 호출하며 종료 코드를 전달한다.
- **Native Runner:** 현재 pytest가 Python 테스트의 수집·실행·성공 판정을 담당한다. Web은 Vite 기반 Node 내비게이션·SSR 검증과 TypeScript 빌드를 실행한다. 등록형 IF Pipeline의 실제 브라우저 경험은 Chrome과 격리된 Compose의 API·Worker·저장소를 연결하는 별도 E2E 검증으로 확인한다.
- **결과·증거:** 현재는 종료 코드와 출력 로그를 사용한다. JUnit·Run Manifest·Artifacts는 실제 소비 요구가 생기면 필요한 만큼만 생성한다.
- **Thin Adapter:** 로컬·GitHub Actions는 동일한 CLI를 호출한다. 다른 CI 제공자는 실제로 필요할 때 연결한다.

## 현재의 유효한 검증

```bash
python scripts/validate.py list
python scripts/validate.py run python
python scripts/validate.py run all
# 또는 make validate
```

현재 `python` Suite는 `tests/`의 Python 테스트를 pytest로 자동 수집·실행한다. 구체적인 검증 범위는 실제 테스트 코드와 실행 결과를 따른다.

웹의 URL·결과 문맥 렌더링 검사는 `cd web && npm run test:navigation`으로 실행한다. 실제 브라우저 검증은 격리된 환경에서 Compose 서비스를 먼저 준비한 후 `SOS_E2E_TOKEN`을 임시 인증 계정의 Bearer Token으로 지정하고 `node web/scripts/browser-pipeline-e2e.mjs`를 실행한다. Chrome/Chromium이 필요하며, 이 검증은 사용자가 예제를 명시적으로 선택해 등록하고 Pipeline 실행·학습 결과·모델 재사용까지 확인한다. 테스트 계정과 저장 데이터는 검증용 Compose에서만 사용하고 해당 격리 볼륨은 검사 후 제거한다. 이 통합 검사는 `.github/workflows/pipeline-browser-e2e.yml`로 분리되어 관련 코드가 변경된 PR을 **리뷰 준비 상태로 처음 열거나 Draft에서 Ready for review로 전환할 때** 자동 실행한다. 이후에는 `run-pipeline-e2e` 라벨 부착 또는 GitHub Actions 수동 실행으로 다시 검증한다. **매 PR 커밋이나 문서 수정 때 자동 반복하지 않는다.** 각 실행에는 고유 Compose 프로젝트와 CI 임시 설정·데이터 볼륨을 사용하며 종료 시 해당 소유 범위만 정리한다. 변경 뒤 통합 검증을 다시 실행하지 않았다면 이전 성공 결과가 새 HEAD를 검증했다고 표시하지 않는다. 프로덕션 서비스의 사용자 데이터에는 실행하지 않는다.

`all`은 **현재 CLI에 등록된 Suite 전체**라는 뜻이지 SOS 제품 계약 전체를 뜻하지 않는다. pytest가 테스트를 수집하지 못해 비정상 종료하면 CLI도 실패한다. CI의 `validation` 상태도 이 범위에만 해당한다.

## 변경 방식

새 검증은 (1) 발견하려는 구체적인 실패, (2) 실제 판정 결과가 바꾸는 판단, (3) 가장 낮은 실행·환경·유지·진단 비용을 기준으로 추가한다. Python 테스트는 pytest가 자동으로 수집하므로 테스트별 중앙 등록은 필요 없다. 다른 Runner가 필요해질 때 CLI의 간단한 Suite 명령 매핑만 확장한다.

과거 시스템과 일대일 대응하지 않으며, 사용되지 않는 증거·관리 계층을 구현하지 않는다. 테스트 삭제 역시 특별한 승인 제도를 요구하지 않는다. 실행한 검증과 미실행 위험은 PR에 명시한다.
