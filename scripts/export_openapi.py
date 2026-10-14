"""FastAPI가 소유한 OpenAPI 계약을 정렬된 JSON으로 출력한다."""

import json

from sos_platform.api import app


def main() -> None:
    print(json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
