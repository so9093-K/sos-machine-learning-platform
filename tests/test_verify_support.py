"""OpenAPI 기준 선택과 Wheel 검증의 안전 계약을 검사한다."""

import pytest

from scripts import verify_openapi


def test_base_ref_rejects_branch_names_before_git_access():
    with pytest.raises(ValueError, match="40자리"):
        verify_openapi.check("main")


def test_base_ref_rejects_shell_metacharacters_before_git_access():
    with pytest.raises(ValueError, match="40자리"):
        verify_openapi.check("HEAD;rm -rf /")
