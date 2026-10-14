# SOS Machine Learning Platform

탐구·실험·이해·발전을 지원하는 재사용 가능한 머신러닝 플랫폼입니다.

- **Research Platform:** 데이터 탐색, 모델 연구, 실험 비교 및 재현
- **ML Execution Platform:** 데이터 처리, 학습, 평가, 추론 및 배포
- **ML Service Platform:** 외부 시스템과 AI 에이전트가 API·SDK로 이용하는 ML 기능

프로젝트 정체성과 기준 이미지는 [Platform Identity](docs/vision/PLATFORM_IDENTITY.md)를 참고하세요.
협업과 코드 작성 규칙은 [AGENTS.md](AGENTS.md)에 있습니다.
문서 영역과 작성 기준은 [문서 안내](docs/README.md)를 참고하세요.

## 개발 환경

Python 3.12, uv, Ruff, pytest를 사용합니다.

```bash
uv python install
uv sync --locked --group dev
uv run --locked ruff check .
uv run --locked ruff format --check .
```

개발 환경과 실제 사용 가능한 기능의 시작 방법을 [Getting Started](docs/getting-started.md)에서 확인할 수 있습니다.
