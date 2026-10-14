"""ë°ì´í° ì ì¥ì ì´ê¸°íë¥¼ ì¤ííë¤. ë°ë³µ í¸ì¶í´ë ë°ì´í°ë ì­ì íì§ ìëë¤."""

from .object_store import ObjectStore
from .store import Store
from pathlib import Path
import os


def main():
    store = Store(Path(os.getenv("SOS_HOME", ".sos")))
    store.initialize()
    ObjectStore().ensure_bucket()


if __name__ == "__main__":
    main()
