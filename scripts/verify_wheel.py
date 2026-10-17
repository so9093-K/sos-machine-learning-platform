"""배포 Wheel에서 Alembic Migration과 Store 초기화가 실제 동작하는지 확인한다."""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMOKE = """
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, sys.argv[1])
import sos_platform
from sos_platform.store import Store
assert Path(sos_platform.__file__).is_relative_to(sys.argv[1])
with TemporaryDirectory() as directory:
    store = Store(Path(directory))
    store.initialize()
    store.engine.dispose()
"""


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sos-wheel-") as temp:
        folder = Path(temp)
        build = folder / "dist"
        target = folder / "site"
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(build)], cwd=ROOT, check=True)
        wheels = list(build.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("빌드 Wheel이 정확히 하나여야 합니다.")
        subprocess.run(
            ["uv", "pip", "install", "--target", str(target), "--no-deps", str(wheels[0])],
            cwd=ROOT,
            check=True,
        )
        subprocess.run([sys.executable, "-I", "-c", SMOKE, str(target)], cwd=temp, check=True)


if __name__ == "__main__":
    main()
