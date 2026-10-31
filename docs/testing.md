# 테스트·검증·CI

검증은 빠른 수정과 피드백을 지원한다. 변경 위험에 맞는 집중 검사를 우선하고, 권한·데이터 무결성·실행 안전성의 실제 실패를 보호한다. 문구·CSS 클래스·HTML 순서나 스키마·인덱스 선언을 그대로 반복하는 검사는 유지하지 않는다. 문구·스타일·배치 변경마다 전체 테스트·브라우저·Compose 실행을 반복하지 않는다.

Python은 pytest, 웹은 npm 명령을 직접 사용한다. 별도 Suite 실행기·테스트 등록부·결과 재판정 계층을 두지 않으며 로컬과 CI는 같은 명령을 실행한다.

```bash
# 변경한 기능의 테스트 파일을 선택한다.
uv run --locked pytest -q tests/test_api_reference_contract.py
# Python 전체: CI 또는 전체 실행이 필요한 변경에서 사용한다. make validate도 같은 명령이다.
uv run --locked pytest -q tests
# 웹의 URL 문맥·결과 식별·입력 검증
npm --prefix web run test:navigation
# TypeScript·Vite 빌드
npm --prefix web run build
```

CI는 Python 테스트와 웹 문맥 검사·빌드를 실행한다. 자동 검증에는 실제 브라우저·API·Worker·저장소·MLflow를 연결한 통합 실행이 포함되지 않는다. CI 성공은 전체 제품 계약의 검증 완료를 뜻하지 않는다.

Feast SDK 동작을 변경한 경우 필요에 따라 `uv run --locked --group dev --group feast pytest -q tests/test_feast_live.py`를 실행한다. 실제 Runtime 연결을 바꾼 경우에만 필요한 서비스를 별도 Compose 프로젝트로 기동하고 결과를 PR에 남긴다. 일회성 서비스 기동 시나리오는 제품 테스트 코드로 보관하지 않는다.

검증을 추가할 때는 발견하려는 구체적 실패와 유지 비용을 판단한다. 새 테스트·증거·관리 계층을 기본 산출물로 요구하지 않는다. 미실행을 성공으로 표현하지 않으며 실행한 범위와 남은 위험을 PR에 명시한다.
