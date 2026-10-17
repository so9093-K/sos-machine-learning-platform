"""OpenAPI 기준 선택과 Wheel 검증의 안전 계약을 검사한다."""

import importlib.util
import sys
from pathlib import Path

import pytest

source = Path(__file__).resolve().parents[1] / "scripts" / "verify_openapi.py"
spec = importlib.util.spec_from_file_location("sos_test_verify_openapi", source)
assert spec is not None and spec.loader is not None
verify_openapi = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verify_openapi
spec.loader.exec_module(verify_openapi)


def test_base_ref_rejects_branch_names_before_git_access():
    with pytest.raises(ValueError, match="40자리"):
        verify_openapi.check("main")


def test_base_ref_rejects_shell_metacharacters_before_git_access():
    with pytest.raises(ValueError, match="40자리"):
        verify_openapi.check("HEAD;rm -rf /")
