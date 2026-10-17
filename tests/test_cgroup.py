"""cgroup v2 필수 메모리 격리의 설정·프로세스 이동 경계를 검증한다."""

import os
from pathlib import Path
from uuid import uuid4

import pytest

import sos_platform.cgroup as cgroup
from sos_platform.cgroup import CgroupIsolation
from sos_platform.errors import RuntimeConfigurationError


def test_cgroup_default_off_and_invalid_mode(monkeypatch):
    monkeypatch.delenv("SOS_WORKER_CGROUP_MODE", raising=False)
    assert CgroupIsolation.from_env(512).parent is None
    monkeypatch.setenv("SOS_WORKER_CGROUP_MODE", "optional")
    with pytest.raises(RuntimeConfigurationError, match="off or required"):
        CgroupIsolation.from_env(512)


def test_required_cgroup_requires_valid_absolute_delegation(monkeypatch, tmp_path):
    monkeypatch.setenv("SOS_WORKER_CGROUP_MODE", "required")
    monkeypatch.setenv("SOS_WORKER_CGROUP_PARENT", "relative/path")
    with pytest.raises(RuntimeConfigurationError, match="absolute delegated"):
        CgroupIsolation.from_env(512)
    monkeypatch.setenv("SOS_WORKER_CGROUP_PARENT", str(tmp_path))
    with pytest.raises(RuntimeConfigurationError, match="cgroup v2 mount"):
        CgroupIsolation.from_env(512).validate_before_claim()


def test_cgroup_probe_child_attachment_limits_and_cleanup(tmp_path, monkeypatch):
    """가상 cgroup 제어 파일로 모든 쓰기·검증·정리 호출을 검사한다.

    이 테스트는 실제 커널 격리의 증거가 아니다. 물리 검증에는
    Linux cgroup v2 메모리 컨트롤러가 위임된 호스트가 필요하다.
    """
    mount = tmp_path / "cgroup"
    delegated = mount / "sos"
    delegated.mkdir(parents=True)
    (mount / "cgroup.controllers").write_text("cpu memory io", encoding="ascii")
    (delegated / "cgroup.subtree_control").write_text("memory", encoding="ascii")
    monkeypatch.setattr(cgroup, "CGROUP_MOUNT", mount)
    monkeypatch.setenv("SOS_WORKER_CGROUP_MODE", "required")
    monkeypatch.setenv("SOS_WORKER_CGROUP_PARENT", str(delegated))
    original_mkdir = Path.mkdir
    original_rmdir = Path.rmdir

    def fake_kernel_mkdir(path, *args, **kwargs):
        original_mkdir(path, *args, **kwargs)
        if path.name.startswith("sos-"):
            for name in (
                "memory.max",
                "memory.swap.max",
                "memory.oom.group",
                "cgroup.procs",
                "memory.events",
            ):
                (path / name).write_text("", encoding="ascii")

    def fake_kernel_rmdir(path):
        if path.name.startswith("sos-"):
            for name in (
                "memory.max",
                "memory.swap.max",
                "memory.oom.group",
                "cgroup.procs",
                "memory.events",
            ):
                (path / name).unlink(missing_ok=True)
        original_rmdir(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", fake_kernel_mkdir)
        patch.setattr(Path, "rmdir", fake_kernel_rmdir)
        policy = CgroupIsolation.from_env(256)
        policy.validate_before_claim()
        assert list(delegated.glob("sos-probe-*")) == []

        attempt = str(uuid4())
        policy.attach_current_child(attempt)
        group = delegated / f"sos-attempt-{attempt.replace('-', '')}"
        assert (group / "memory.max").read_text() == str(256 * 1024 * 1024)
        assert (group / "memory.swap.max").read_text() == "0"
        assert (group / "memory.oom.group").read_text() == "1"
        assert (group / "cgroup.procs").read_text() == str(os.getpid())
        (group / "memory.events").write_text("low 0\noom_kill 1\n", encoding="ascii")
        assert policy.oom_killed(attempt) is True
        policy.cleanup(attempt)
        assert not group.exists()


def test_existing_attempt_groups_ignore_symlinks_and_unrelated_names(tmp_path, monkeypatch):
    """위임 경로의 정규 Attempt 디렉터리만 탐지하고 심볼릭 링크를 따르지 않는다."""
    mount = tmp_path / "cgroup"
    delegated = mount / "sos"
    delegated.mkdir(parents=True)
    (mount / "cgroup.controllers").write_text("memory", encoding="ascii")
    (delegated / "cgroup.subtree_control").write_text("memory", encoding="ascii")
    monkeypatch.setattr(cgroup, "CGROUP_MOUNT", mount)
    attempt = uuid4()
    (delegated / f"sos-attempt-{attempt.hex}").mkdir()
    (delegated / "sos-attempt-bad-name").mkdir()
    (delegated / "sos-other").mkdir()
    (delegated / f"sos-attempt-{uuid4().hex}").symlink_to(
        delegated / f"sos-attempt-{attempt.hex}", target_is_directory=True
    )
    policy = CgroupIsolation(parent=delegated, memory_bytes=256 * 1024 * 1024)
    assert policy.existing_attempt_ids() == [str(attempt)]


def test_required_cgroup_fails_if_memory_controller_not_enabled(tmp_path, monkeypatch):
    mount = tmp_path / "cgroup"
    delegated = mount / "sos"
    delegated.mkdir(parents=True)
    (mount / "cgroup.controllers").write_text("memory cpu", encoding="ascii")
    (delegated / "cgroup.subtree_control").write_text("cpu", encoding="ascii")
    monkeypatch.setattr(cgroup, "CGROUP_MOUNT", mount)
    monkeypatch.setenv("SOS_WORKER_CGROUP_MODE", "required")
    monkeypatch.setenv("SOS_WORKER_CGROUP_PARENT", str(delegated))
    with pytest.raises(RuntimeConfigurationError, match="memory controller"):
        CgroupIsolation.from_env(256).validate_before_claim()
