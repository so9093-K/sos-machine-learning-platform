# 문서 안내

- [Platform Identity](vision/PLATFORM_IDENTITY.md): 제품 정체성과 기준 이미지
- [ADR](adr/README.md): 장기적 영향이 있는 기술·아키텍처 결정

아래 문서는 해당 기능이나 인터페이스가 구체화되어 설명할 내용이 있을 때 작성한다. 빈 파일은 만들지 않는다.

| 문서 | 책임 |
| --- | --- |
| `getting-started.md` | 설치·실행·최초 실험 등 사용 시작 절차 |
| `architecture.md` | 시스템 구성요소, 책임과 경계, 요청 흐름 |
| `workflows.md` | 데이터 처리, 학습·평가·추론 흐름과 작업 상태 |
| `contracts.md` | 데이터·플러그인·Job·Model Bundle 계약과 호환성 규칙 |
| `runtimes.md` | 실행 방식, 환경 격리, 프레임워크 호환성과 업그레이드 |
| `development.md` | README를 넘어서는 개발·검증·CI 절차 |
| `operations.md` | 배포·관측·장애 대응·백업·복구 절차 |

코드·타입·설정·자동 생성 API 명세를 원본으로 사용한다. 문서에는 구조, 의도, 실제 사용 및 운영 방법을 기록하며 관련 변경 시 함께 검토한다.
