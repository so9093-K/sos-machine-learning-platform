# SOS Machine Learning Platform

탐구·실험·이해·발전을 지원하는 재사용 가능한 ML 플랫폼입니다.

플랫폼은 세 가지 역할을 지향합니다.

- **Research Platform:** 가설, 실험, 모델 비교, 연구 지식의 축적
- **ML Execution Platform:** 데이터 처리부터 학습·평가·추론·배포까지 재사용 가능한 실행
- **ML Service Platform:** 사람, 외부 애플리케이션, IoT/MES 및 AI 에이전트가 API로 활용

프로젝트의 변하지 않는 제품 정체성은 [PLATFORM_IDENTITY.md](docs/vision/PLATFORM_IDENTITY.md)에 있습니다.
개발 및 협업 규칙은 [AGENTS.md](AGENTS.md)에 있습니다.

## 개발 환경

기본 Python은 **3.12**이며 uv, Ruff, pytest를 사용합니다.

```bash
uv python install
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

프로젝트 버전 `0.0.1`은 릴리스 상태나 완성도를 나타내지 않으며, 작업마다 자동으로 변경하지 않습니다.

현재는 개발 기반을 준비하는 단계입니다. 구체적인 모델·서버 구현은 이후 단계에서 추가합니다.
