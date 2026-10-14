"""데이터 저장소 초기화를 실행한다. 반복 호출해도 데이터는 삭제하지 않는다."""

import os
from pathlib import Path

from .object_store import ObjectStore
from .store import Store


def main():
    store = Store(Path(os.getenv("SOS_HOME", ".sos")))
    store.initialize()
    ObjectStore().ensure_bucket()


if __name__ == "__main__":
    main()
