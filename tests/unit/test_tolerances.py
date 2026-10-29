"""
Unit tests for numeric aggregate tolerances.
"""

import pytest

from migration_validator.validation.aggregates import is_within_tolerance


@pytest.mark.unit
def test_exact_matches():
    assert is_within_tolerance(100.0, 100.0, tol_abs=0.01, tol_rel=0.0001) is True
    assert is_within_tolerance(0.0, 0.0, tol_abs=0.01, tol_rel=0.0001) is True


@pytest.mark.unit
def test_within_absolute_tolerance():
    # 0.005 difference is <= 0.01 abs tol
    assert is_within_tolerance(100.0, 100.005, tol_abs=0.01, tol_rel=0.0) is True
    assert is_within_tolerance(100.0, 99.995, tol_abs=0.01, tol_rel=0.0) is True


@pytest.mark.unit
def test_exceeding_absolute_tolerance():
    # 0.05 difference exceeds 0.01 abs tol
    assert is_within_tolerance(100.0, 100.05, tol_abs=0.01, tol_rel=0.0) is False


@pytest.mark.unit
def test_within_relative_tolerance():
    # Large numbers: diff of 100 on 10,000,000 is 0.001% (rel=0.00001)
    src = 10_000_000.0
    dst = 10_000_050.0  # diff = 50, rel_diff = 50 / 10,000,000 = 0.000005 <= 0.0001
    assert is_within_tolerance(src, dst, tol_abs=1.0, tol_rel=0.0001) is True


@pytest.mark.unit
def test_zero_source_handling():
    # If source is zero, relative division by zero must not throw exception
    assert is_within_tolerance(0.0, 0.005, tol_abs=0.01, tol_rel=0.0001) is True
    assert is_within_tolerance(0.0, 0.05, tol_abs=0.01, tol_rel=0.0001) is False
