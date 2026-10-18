"""테스트 프로세스에서만 로컬 개발 인증을 명시적으로 선택한다."""

import os

# API 모듈의 전역 app은 테스트 수집 시 생성되므로 fixture보다 먼저 설정한다.
os.environ.setdefault("SOS_AUTH_MODE", "development")
