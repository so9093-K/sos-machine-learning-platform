"""외부 서비스의 데이터와 스키마를 변경하지 않고 호환성을 확인한다."""

import os
from pathlib import Path

from .object_store import ObjectStore
from .store import Store


def main() -> None:
    """외부 DB의 스키마와 객체 저장소 버킷에 대해 읽기 전용 검증을 수행한다."""
    store = Store(Path(os.getenv("SOS_HOME", ".sos")))
    store.initialize()
    ObjectStore().ready()


if __name__ == "__main__":
    main()
