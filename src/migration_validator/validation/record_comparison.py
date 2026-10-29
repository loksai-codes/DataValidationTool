"""
Record-level comparison engine with key-based streaming and SHA-256 fingerprinting.
Scales to millions of records by operating in bounded batches without whole-table memory loading.
Detects missing records, extra records, changed records, and column-level differences.
"""

import time
import uuid
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

import pandas as pd

from migration_validator.connectors.base import BaseConnector
from migration_validator.logging_config import get_logger
from migration_validator.validation.models import (
    CheckResult,
    CheckType,
    Discrepancy,
    DiscrepancyType,
    ValidationStatus,
)
from migration_validator.validation.serialization import (
    canonical_value_str,
    compute_record_hash,
)

logger = get_logger("validation.record_comparison")


class RecordComparisonEngine:
    """Streams and validates records across systems using SHA-256 canonical fingerprints."""

    def __init__(
        self,
        source_connector: BaseConnector,
        dest_connector: BaseConnector,
        source_table: str,
        dest_table: str,
        key_columns: List[str],
        column_mapping: Dict[str, str],
        batch_size: int = 5000,
        source_db: Optional[str] = None,
        dest_schema: Optional[str] = "public"
    ):
        self.source = source_connector
        self.dest = dest_connector
        self.source_table = source_table
        self.dest_table = dest_table
        self.key_columns = key_columns
        self.column_mapping = column_mapping
        self.batch_size = batch_size
        self.source_db = source_db
        self.dest_schema = dest_schema

        self.primary_key = key_columns[0]
        self.src_cols = list(column_mapping.keys())
        self.dst_cols = [column_mapping[c] for c in self.src_cols]

    def stream_compare(
        self,
        max_records: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None
    ) -> Generator[Tuple[List[Discrepancy], Dict[str, int]], None, None]:
        """
        Stream records in deterministic order using keyset pagination.
        Yields (batch_discrepancies, batch_stats) to keep memory bounded.
        """
        last_src_key: Optional[Any] = None
        last_dst_key: Optional[Any] = None
        src_exhausted = False
        dst_exhausted = False

        total_evaluated = 0
        dest_key_name = self.column_mapping[self.primary_key]

        while True:
            if cancel_check and cancel_check():
                logger.info("Record comparison cancelled by user.")
                break

            if src_exhausted and dst_exhausted:
                break

            # Fetch batches from both systems if not yet exhausted
            if not src_exhausted:
                src_df, next_src_cursor = self.source.fetch_records_batch(
                    table_name=self.source_table,
                    columns=self.src_cols,
                    key_columns=[self.primary_key],
                    batch_size=self.batch_size,
                    last_key=last_src_key,
                    schema_or_db=self.source_db
                )
                if src_df.empty or next_src_cursor is None:
                    src_exhausted = True
                else:
                    last_src_key = next_src_cursor
            else:
                src_df = pd.DataFrame()

            if not dst_exhausted:
                dst_df, next_dst_cursor = self.dest.fetch_records_batch(
                    table_name=self.dest_table,
                    columns=self.dst_cols,
                    key_columns=[dest_key_name],
                    batch_size=self.batch_size,
                    last_key=last_dst_key,
                    schema_or_db=self.dest_schema
                )
                if dst_df.empty or next_dst_cursor is None:
                    dst_exhausted = True
                else:
                    last_dst_key = next_dst_cursor
            else:
                dst_df = pd.DataFrame()

            if src_df.empty and dst_df.empty:
                break

            batch_discrepancies: List[Discrepancy] = []
            stats = {
                "evaluated": 0,
                "identical": 0,
                "missing": 0,
                "extra": 0,
                "changed": 0,
                "column_diffs": 0
            }

            # Map destination column names back to source column names for aligned comparison
            dst_rename_map = {dst: src for src, dst in self.column_mapping.items()}
            dst_df_aligned = dst_df.rename(columns=dst_rename_map)

            # Index by primary key
            src_records = {
                row[self.primary_key]: row.to_dict()
                for _, row in src_df.iterrows()
            } if not src_df.empty else {}

            dst_records = {
                row[self.primary_key]: row.to_dict()
                for _, row in dst_df_aligned.iterrows()
            } if not dst_df_aligned.empty else {}

            all_keys = sorted(set(src_records.keys()) | set(dst_records.keys()))
            stats["evaluated"] = len(all_keys)
            total_evaluated += len(all_keys)

            for key in all_keys:
                in_src = key in src_records
                in_dst = key in dst_records

                if in_src and not in_dst:
                    # Missing in destination
                    stats["missing"] += 1
                    batch_discrepancies.append(
                        Discrepancy(
                            type=DiscrepancyType.MISSING_IN_DEST,
                            key=str(key),
                            details={"source_record": src_records[key]}
                        )
                    )
                elif in_dst and not in_src:
                    # Extra in destination
                    stats["extra"] += 1
                    batch_discrepancies.append(
                        Discrepancy(
                            type=DiscrepancyType.EXTRA_IN_DEST,
                            key=str(key),
                            details={"dest_record": dst_records[key]}
                        )
                    )
                else:
                    # Key present in both: compare SHA-256 fingerprint
                    src_rec = src_records[key]
                    dst_rec = dst_records[key]

                    src_hash = compute_record_hash(src_rec, self.src_cols)
                    dst_hash = compute_record_hash(dst_rec, self.src_cols)

                    if src_hash == dst_hash:
                        stats["identical"] += 1
                    else:
                        stats["changed"] += 1
                        # Pinpoint column discrepancies
                        for col in self.src_cols:
                            s_canon = canonical_value_str(src_rec.get(col))
                            d_canon = canonical_value_str(dst_rec.get(col))
                            if s_canon != d_canon:
                                stats["column_diffs"] += 1
                                batch_discrepancies.append(
                                    Discrepancy(
                                        type=DiscrepancyType.VALUE_MISMATCH,
                                        key=str(key),
                                        column=col,
                                        source_val=s_canon,
                                        dest_val=d_canon,
                                        details={
                                            "raw_source": str(src_rec.get(col)),
                                            "raw_dest": str(dst_rec.get(col))
                                        }
                                    )
                                )

            yield batch_discrepancies, stats

            if progress_callback:
                progress_callback(total_evaluated, -1, f"Validated {total_evaluated:,} records...")

            if max_records and total_evaluated >= max_records:
                logger.info("Reached maximum record comparison limit (%d).", max_records)
                break

            if src_exhausted and dst_exhausted:
                break


def run_record_comparison_check(
    engine: RecordComparisonEngine,
    max_records: Optional[int] = None,
    discrepancy_sink: Optional[Callable[[List[Discrepancy]], None]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None
) -> Tuple[CheckResult, int, Dict[str, int]]:
    """
    Executes record-level comparison streaming, passing discrepancies to sink and computing status.
    Returns: (CheckResult, total_discrepancy_count, discrepancy_counts_by_type)
    """
    start = time.perf_counter()
    check_id = f"CHK-REC-FINGERPRINT-{uuid.uuid4().hex[:8]}"

    total_evaluated = 0
    total_identical = 0
    total_missing = 0
    total_extra = 0
    total_changed = 0
    total_col_diffs = 0

    for batch_discrepancies, stats in engine.stream_compare(
        max_records=max_records,
        progress_callback=progress_callback,
        cancel_check=cancel_check
    ):
        total_evaluated += stats["evaluated"]
        total_identical += stats["identical"]
        total_missing += stats["missing"]
        total_extra += stats["extra"]
        total_changed += stats["changed"]
        total_col_diffs += stats["column_diffs"]

        if discrepancy_sink and batch_discrepancies:
            discrepancy_sink(batch_discrepancies)

    elapsed_ms = (time.perf_counter() - start) * 1000.0

    total_discrepancies = total_missing + total_extra + total_changed
    counts_by_type = {
        DiscrepancyType.MISSING_IN_DEST.value: total_missing,
        DiscrepancyType.EXTRA_IN_DEST.value: total_extra,
        DiscrepancyType.VALUE_MISMATCH.value: total_col_diffs,
    }

    if total_discrepancies == 0 and total_evaluated > 0:
        status = ValidationStatus.PASSED
        err_msg = None
    elif total_evaluated == 0:
        status = ValidationStatus.INCONCLUSIVE
        err_msg = "No records were evaluated during record-level comparison."
    else:
        status = ValidationStatus.FAILED
        err_msg = (
            f"Record discrepancies detected: {total_missing} missing in destination, "
            f"{total_extra} extra in destination, {total_changed} changed records ({total_col_diffs} column mismatches)"
        )

    check_result = CheckResult(
        check_id=check_id,
        check_type=CheckType.RECORD_COMPARISON,
        check_name="Record-Level SHA-256 Fingerprint Parity",
        status=status,
        source_value=f"{total_evaluated:,} evaluated",
        dest_value=f"{total_identical:,} identical",
        difference=f"{total_discrepancies:,} mismatched records",
        tolerance="0 discrepancies (Exact Parity)",
        duration_ms=round(elapsed_ms, 2),
        error_details=err_msg,
        metadata={
            "total_evaluated": total_evaluated,
            "identical": total_identical,
            "missing": total_missing,
            "extra": total_extra,
            "changed": total_changed,
            "column_differences": total_col_diffs
        }
    )

    return check_result, total_discrepancies, counts_by_type
