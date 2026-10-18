"""FastAPI가 소유한 OpenAPI 계약을 정렬된 JSON으로 출력한다."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from sos_platform.api import create_app
from sos_platform.security import IdentitySettings


def main() -> None:
    """서버 인증 환경변수에 의존하지 않고 임시 저장소에서 명세를 생성한다."""
    with TemporaryDirectory(prefix="sos-openapi-") as home:
        app = create_app(Path(home), identity=IdentitySettings("development", {}))
        schema = app.openapi()
    print(json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
