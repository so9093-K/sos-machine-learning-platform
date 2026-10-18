"""FastAPI가 소유한 OpenAPI 계약을 정렬된 JSON으로 출력한다."""

import json
import os


def main() -> None:
    # 문서 생성기는 네트워크 서버를 실행하지 않으며 개발 신원을 명시적으로 선택한다.
    os.environ.setdefault("SOS_AUTH_MODE", "development")
    from sos_platform.api import app

    print(json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
