# SOS Machine Learning Platform

탐구·실험·이해·발전을 지원하는 재사용 가능한 머신러닝 플랫폼입니다.

- **Research Platform:** 데이터 탐색, 모델 연구, 실험 비교 및 재현
- **ML Execution Platform:** 데이터 처리, 학습, 평가, 추론 및 배포
- **ML Service Platform:** 외부 시스템과 AI 에이전트가 API·SDK로 이용하는 ML 기능

제품 방향과 기준 이미지는 [Platform Identity](docs/vision/PLATFORM_IDENTITY.md)를,
설계 및 문서 책임은 [문서 안내](docs/README.md)를 참고하세요.
협업 규칙은 [AGENTS.md](AGENTS.md)에 있습니다.

## 시작하기

개발 실행에는 Docker Compose와 GNU Make가 필요합니다. Python 3.12·uv·Node.js 24·npm은 호스트에서 테스트·타입 검증을 실행할 때 사용합니다. 컨테이너 실행은 Token 인증 설정이 필요합니다.

```bash
make up
make status
make down
```

웹의 Data Explorer에서는 Dataset 개요·데이터 조회·탐색·변환을 이용할 수 있습니다. 합성 예제는 선택 시에만 Dataset으로 등록되며, 분석과 Job 실행은 사용자가 선택합니다. 

`make` 또는 `make help`로 공개 명령을 확인할 수 있습니다. 기본 Web UI는
`http://localhost:9601`, API는 `http://localhost:9602`,
Scalar API 문서는 `http://localhost:9602/docs`입니다.

환경과 외부 서비스 연결, 진단·복구 절차는 [시작하기](docs/getting-started.md)에 있습니다.
