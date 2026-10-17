"""Linux cgroup v2에 Worker 자식의 실제 메모리 상한을 설정한다.

기본 개발 환경에서는 꺼져 있다. 운영자가 위임받은 writable cgroup 아래에
Attempt별 하위 그룹을 만들 수 있을 때만 required 모드를 허용한다.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from .errors import RuntimeConfigurationError

CGROUP_MOUNT = Path("/sys/fs/cgroup")


@dataclass(frozen=True)
class CgroupIsolation:
    """실행 전 fail-closed 검증을 거치는 선택적 Linux 메모리 격리."""

    parent: Path | None = None
    memory_bytes: int = 0

    @classmethod
    def from_env(cls, max_rss_mib: int) -> "CgroupIsolation":
        """off 또는 required 모드를 해석하며 기본값은 off다."""
        mode = os.getenv("SOS_WORKER_CGROUP_MODE", "off")
        if mode == "off":
            return cls()
        if mode != "required":
            raise RuntimeConfigurationError("SOS_WORKER_CGROUP_MODE must be off or required")
        path = os.getenv("SOS_WORKER_CGROUP_PARENT")
        if not path or not Path(path).is_absolute():
            raise RuntimeConfigurationError(
                "SOS_WORKER_CGROUP_PARENT must name an absolute delegated cgroup v2 path"
            )
        return cls(parent=Path(path), memory_bytes=max_rss_mib * 1024 * 1024)

    def _delegated_parent(self) -> Path:
        if self.parent is None:
            raise RuntimeConfigurationError("cgroup isolation is disabled")
        if sys.platform != "linux":
            raise RuntimeConfigurationError("Required cgroup isolation needs Linux")
        try:
            mount = CGROUP_MOUNT.resolve(strict=True)
            parent = self.parent.resolve(strict=True)
            # 시스템 cgroup root와 다른 서비스의 cgroup은 수정하지 않는다.
            if parent == mount or not parent.is_relative_to(mount):
                raise RuntimeConfigurationError(
                    "SOS_WORKER_CGROUP_PARENT must be under the cgroup v2 mount"
                )
            if "memory" not in (mount / "cgroup.controllers").read_text().split():
                raise RuntimeConfigurationError("cgroup v2 memory controller is unavailable")
            if "memory" not in (parent / "cgroup.subtree_control").read_text().split():
                raise RuntimeConfigurationError(
                    "memory controller must be enabled in delegated cgroup.subtree_control"
                )
        except OSError as exc:
            raise RuntimeConfigurationError("Required cgroup v2 delegation is unavailable") from exc
        return parent

    def _new_group(self, name: str) -> Path:
        parent = self._delegated_parent()
        path = parent / name
        try:
            path.mkdir()
            (path / "memory.max").write_text(str(self.memory_bytes), encoding="ascii")
            (path / "memory.swap.max").write_text("0", encoding="ascii")
            (path / "memory.oom.group").write_text("1", encoding="ascii")
            if (
                (path / "memory.max").read_text(encoding="ascii").strip() != str(self.memory_bytes)
                or (path / "memory.swap.max").read_text(encoding="ascii").strip() != "0"
                or (path / "memory.oom.group").read_text(encoding="ascii").strip() != "1"
            ):
                raise RuntimeConfigurationError("Kernel did not accept the cgroup memory limits")
        except (OSError, RuntimeConfigurationError) as exc:
            # 실제 cgroup 제어 파일은 커널에서 제공하며 삭제하지 않는다.
            try:
                path.rmdir()
            except OSError:
                pass
            raise RuntimeConfigurationError(
                "Required cgroup memory limits are unavailable"
            ) from exc
        return path

    def validate_before_claim(self) -> None:
        """작업 인수 전에 위임·쓰기·메모리/스왑 상한 적용 가능성을 확인한다."""
        if self.parent is None:
            return
        probe = self._new_group(f"sos-probe-{uuid4().hex}")
        try:
            # 빈 그룹만 삭제한다. 사용자 소유 프로세스·하위 그룹은 건드리지 않는다.
            probe.rmdir()
        except OSError as exc:
            raise RuntimeConfigurationError("Cgroup probe could not be removed") from exc

    def attach_current_child(self, attempt_id: str) -> None:
        """자식이 계산·I/O를 시작하기 전에 자신을 제한된 그룹으로 이동한다."""
        if self.parent is None:
            return
        name = f"sos-attempt-{UUID(attempt_id).hex}"
        path = self._new_group(name)
        try:
            pid = os.getpid()
            (path / "cgroup.procs").write_text(str(pid), encoding="ascii")
            if str(pid) not in (path / "cgroup.procs").read_text(encoding="ascii").split():
                raise RuntimeConfigurationError("Worker child was not attached to its cgroup")
        except (OSError, RuntimeConfigurationError) as exc:
            try:
                path.rmdir()
            except OSError:
                pass
            raise RuntimeConfigurationError("Required cgroup attachment failed") from exc

    def oom_killed(self, attempt_id: str) -> bool:
        """커널 memory.events의 oom_kill 횟수로 강제 종료 원인을 구분한다."""
        if self.parent is None:
            return False
        path = self._delegated_parent() / f"sos-attempt-{UUID(attempt_id).hex}"
        try:
            events = (path / "memory.events").read_text(encoding="ascii")
        except FileNotFoundError:
            return False
        return any(
            name == "oom_kill" and int(value) > 0
            for name, value in (line.split() for line in events.splitlines())
        )

    def cleanup(self, attempt_id: str) -> None:
        """종료된 Attempt가 소유한 빈 cgroup만 회수한다."""
        if self.parent is None:
            return
        path = self._delegated_parent() / f"sos-attempt-{UUID(attempt_id).hex}"
        try:
            path.rmdir()
        except FileNotFoundError:
            pass
