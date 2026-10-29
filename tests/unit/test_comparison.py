"""Unit tests for comparison engine: normalization, schema, counts, keys, and record comparison."""
from decimal import Decimal
import pytest

from data_validator.comparison.count_checks import run_count_check
from data_validator.comparison.normalization import (
    NormalizationConfig,
    compare_decimals,
    normalize_datetime,
    normalize_string,
    values_are_equal,
)
from data_validator.comparison.partition_planner import plan_partitions
from data_validator.comparison.record_compare import compare_records_batch
from data_validator.comparison.schema_checks import SchemaDiffSeverity, run_schema_check
from data_validator.config.models import ComparisonConfig, PartitionConfig, PartitionStrategy
from data_validator.persistence.models import DiscrepancyType


def test_normalization_rules():
    # String trim and case
    cfg = NormalizationConfig(trim_strings=True, case_sensitive_strings=False)
    assert normalize_string("  Hello World  ", cfg) == "hello world"

    # Decimal tolerance
    assert compare_decimals(Decimal("100.005"), Decimal("100.006"), tolerance="0.01") is True
    assert compare_decimals(Decimal("100.005"), Decimal("100.050"), tolerance="0.01") is False

    # Values equal with decimal tolerance
    cfg_dec = NormalizationConfig(decimal_tolerance="0.05")
    assert values_are_equal("10.50", 10.52, cfg_dec) is True
    assert values_are_equal("10.50", 10.60, cfg_dec) is False

    # Timestamp normalization
    dt_iso = normalize_datetime("2026-10-29T12:00:00Z")
    assert dt_iso is not None
    assert dt_iso.year == 2026


def test_schema_checks():
    cfg = ComparisonConfig(unique_keys=["id"])

    # Compatible
    src = [{"name": "id", "type": "int4", "nullable": False}, {"name": "val", "type": "varchar(50)", "nullable": True}]
    tgt = [{"name": "id", "type": "int8", "nullable": False}, {"name": "val", "type": "text", "nullable": True}]
    res = run_schema_check(src, tgt, cfg)
    assert res.passed is True
    assert res.severity in (SchemaDiffSeverity.COMPATIBLE, SchemaDiffSeverity.WARNING)

    # Missing unique key in target -> Blocking
    tgt_no_key = [{"name": "val", "type": "text", "nullable": True}]
    res_blocking = run_schema_check(src, tgt_no_key, cfg)
    assert res_blocking.passed is False
    assert res_blocking.severity == SchemaDiffSeverity.BLOCKING


def test_count_checks():
    res_match = run_count_check(1000, 1000)
    assert res_match.passed is True
    assert res_match.delta == 0

    res_mismatch = run_count_check(1000, 950)
    assert res_mismatch.passed is False
    assert res_mismatch.delta == -50


def test_partition_planner_deterministic_coverage():
    cfg = PartitionConfig(
        strategy=PartitionStrategy.KEY_RANGE,
        column="customer_id",
        range_size=1000,
        include_null_partition=True,
    )
    partitions = plan_partitions("run_plan_001", cfg, column_min=1, column_max=2500)
    assert len(partitions) == 4  # [1-1000], [1001-2000], [2001-2500], and NULL partition
    assert partitions[0].lower_bound == "1"
    assert partitions[0].upper_bound == "1000"
    assert partitions[1].lower_bound == "1001"
    assert partitions[1].upper_bound == "2000"
    assert partitions[2].lower_bound == "2001"
    assert partitions[2].upper_bound == "2500"
    assert partitions[3].lower_bound is None  # NULL partition


def test_record_compare_all_discrepancy_types():
    source_records = [
        {"id": 1, "name": "Alice", "amount": 100.0},
        {"id": 2, "name": "Bob", "amount": 200.0},
        {"id": 3, "name": "Charlie", "amount": 300.0},  # Missing in target
        {"id": 4, "name": "David", "amount": 400.0},
        {"id": 4, "name": "David Dup", "amount": 400.0},  # Duplicate key in source
        {"id": None, "name": "NullKeyUser", "amount": 500.0},  # NULL key
    ]

    target_records = [
        {"id": 1, "name": "Alice", "amount": 100.0},      # Match
        {"id": 2, "name": "Robert", "amount": 250.0},     # Changed record
        {"id": 4, "name": "David", "amount": 400.0},
        {"id": 5, "name": "Eve", "amount": 500.0},        # Extra in target
    ]

    result = compare_records_batch(
        source_records=source_records,
        target_records=target_records,
        unique_keys=["id"],
        run_id="run_comp_001",
        partition_id="part_comp_001",
    )

    types = {d.discrepancy_type for d in result.discrepancies}
    assert DiscrepancyType.MISSING_IN_TARGET in types
    assert DiscrepancyType.EXTRA_IN_TARGET in types
    assert DiscrepancyType.CHANGED_RECORD in types
    assert DiscrepancyType.DUPLICATE_KEY in types
    assert DiscrepancyType.NULL_KEY in types

    assert result.missing_count >= 1  # Charlie
    assert result.extra_count >= 1    # Eve
    assert result.changed_count >= 1  # Bob vs Robert
    assert result.duplicate_key_count >= 1  # ID 4 duplicated in source
    assert result.null_key_count >= 1       # ID None

    # Check that changed record has column details
    changed_discs = [d for d in result.discrepancies if d.discrepancy_type == DiscrepancyType.CHANGED_RECORD]
    cols = {d.column_name for d in changed_discs}
    assert "name" in cols or "amount" in cols
