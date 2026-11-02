"""
Optimized record-level comparison engine supporting vectorized Pandas, DuckDB, Polars,
hash-assisted, and partitioned exact comparison strategies.
Provides bounded memory consumption, composite key support, deterministic canonicalization,
and detailed field-level discrepancy diagnosis.
"""

import hashlib
import time
import uuid
from abc import ABC, abstractmethod
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


def _format_key_str(row: Any, key_columns: List[str]) -> str:
    """Format single or composite key columns into a deterministic string representation."""
    if len(key_columns) == 1:
        k = key_columns[0]
        val = row.get(k) if isinstance(row, dict) else row[k]
        return str(val)
    parts = []
    for k in key_columns:
        val = row.get(k) if isinstance(row, dict) else row[k]
        parts.append(f"{k}={val}")
    return "(" + ", ".join(parts) + ")"


def _detect_and_extract_duplicate_keys(
    df: pd.DataFrame,
    key_columns: List[str],
    location_label: str,
    max_samples: int,
) -> Tuple[pd.DataFrame, List[Discrepancy], int]:
    """
    Detect duplicate key violations, emit DUPLICATE_KEY discrepancies,
    and return deduplicated DataFrame to prevent Cartesian product blowup.
    """
    if df.empty or len(key_columns) == 0:
        return df, [], 0
    if not all(k in df.columns for k in key_columns):
        return df, [], 0
    dup_mask = df.duplicated(subset=key_columns, keep=False)
    if not dup_mask.any():
        return df, [], 0

    dup_rows = df[dup_mask]
    dup_count = int(dup_mask.sum())
    discs = []
    if max_samples > 0:
        for _, row in dup_rows.head(max_samples).iterrows():
            key_repr = _format_key_str(row, key_columns)
            r_dict = row.to_dict()
            discs.append(
                Discrepancy(
                    type=DiscrepancyType.DUPLICATE_KEY,
                    key=key_repr,
                    details={"dataset": location_label, "record": r_dict},
                )
            )
    deduped_df = df.drop_duplicates(subset=key_columns, keep="first")
    return deduped_df, discs, dup_count


def _classify_field_discrepancy(
    col: str,
    s_raw: Any,
    d_raw: Any,
    key_repr: str,
) -> Tuple[bool, Optional[Discrepancy], bool]:
    """
    Compare raw values using canonical serialization.
    Returns: (is_mismatch, discrepancy_or_none, is_null_mismatch)
    """
    s_is_null = s_raw is None or pd.isna(s_raw)
    d_is_null = d_raw is None or pd.isna(d_raw)

    if s_is_null and d_is_null:
        return False, None, False

    s_canon = canonical_value_str(s_raw)
    d_canon = canonical_value_str(d_raw)

    if s_canon == d_canon:
        return False, None, False

    if s_is_null != d_is_null:
        disc_type = DiscrepancyType.NULL_MISMATCH
        is_null_mismatch = True
    else:
        disc_type = DiscrepancyType.VALUE_MISMATCH
        is_null_mismatch = False

    disc = Discrepancy(
        type=disc_type,
        key=key_repr,
        column=col,
        source_val=s_canon,
        dest_val=d_canon,
        details={"raw_source": str(s_raw), "raw_dest": str(d_raw)},
    )
    return True, disc, is_null_mismatch


class BaseComparisonStrategy(ABC):
    """Abstract comparison strategy interface."""

    @abstractmethod
    def compare_batch(
        self,
        src_df: pd.DataFrame,
        dst_df_aligned: pd.DataFrame,
        key_columns: List[str],
        columns: List[str],
        max_samples: int = 1000,
    ) -> Tuple[List[Discrepancy], Dict[str, int]]:
        """
        Compare source and aligned destination DataFrames.
        Returns: (discrepancies_sample, stats_dict)
        """
        pass


class BaselinePythonStrategy(BaseComparisonStrategy):
    """Reference implementation using row dictionaries and per-row SHA-256 fingerprinting."""

    def compare_batch(
        self,
        src_df: pd.DataFrame,
        dst_df_aligned: pd.DataFrame,
        key_columns: List[str],
        columns: List[str],
        max_samples: int = 1000,
    ) -> Tuple[List[Discrepancy], Dict[str, int]]:
        batch_discrepancies: List[Discrepancy] = []
        stats = {
            "evaluated": 0,
            "identical": 0,
            "missing": 0,
            "extra": 0,
            "changed": 0,
            "column_diffs": 0,
            "null_diffs": 0,
            "duplicate_keys": 0,
        }

        # Check for duplicate keys
        src_df_clean, src_dup_discs, src_dup_count = _detect_and_extract_duplicate_keys(
            src_df, key_columns, "source", max_samples
        )
        dst_df_clean, dst_dup_discs, dst_dup_count = _detect_and_extract_duplicate_keys(
            dst_df_aligned, key_columns, "destination", max_samples - len(src_dup_discs)
        )
        batch_discrepancies.extend(src_dup_discs)
        batch_discrepancies.extend(dst_dup_discs)
        stats["duplicate_keys"] = src_dup_count + dst_dup_count

        def make_key(row):
            if len(key_columns) == 1:
                return row[key_columns[0]]
            return tuple(row[k] for k in key_columns)

        src_records = {}
        if not src_df_clean.empty:
            for _, row in src_df_clean.iterrows():
                r_dict = row.to_dict()
                src_records[make_key(r_dict)] = r_dict

        dst_records = {}
        if not dst_df_clean.empty:
            for _, row in dst_df_clean.iterrows():
                r_dict = row.to_dict()
                dst_records[make_key(r_dict)] = r_dict

        all_keys = sorted(set(src_records.keys()) | set(dst_records.keys()), key=lambda x: str(x))
        stats["evaluated"] = len(all_keys)

        for key in all_keys:
            in_src = key in src_records
            in_dst = key in dst_records
            key_repr = _format_key_str(src_records[key] if in_src else dst_records[key], key_columns)

            if in_src and not in_dst:
                stats["missing"] += 1
                if len(batch_discrepancies) < max_samples:
                    batch_discrepancies.append(
                        Discrepancy(
                            type=DiscrepancyType.MISSING_IN_DEST,
                            key=key_repr,
                            details={"source_record": src_records[key]},
                        )
                    )
            elif in_dst and not in_src:
                stats["extra"] += 1
                if len(batch_discrepancies) < max_samples:
                    batch_discrepancies.append(
                        Discrepancy(
                            type=DiscrepancyType.EXTRA_IN_DEST,
                            key=key_repr,
                            details={"dest_record": dst_records[key]},
                        )
                    )
            else:
                src_rec = src_records[key]
                dst_rec = dst_records[key]
                src_hash = compute_record_hash(src_rec, columns)
                dst_hash = compute_record_hash(dst_rec, columns)

                if src_hash == dst_hash:
                    stats["identical"] += 1
                else:
                    row_changed = False
                    for col in columns:
                        if col in key_columns:
                            continue
                        is_diff, disc, is_null = _classify_field_discrepancy(
                            col, src_rec.get(col), dst_rec.get(col), key_repr
                        )
                        if is_diff:
                            row_changed = True
                            stats["column_diffs"] += 1
                            if is_null:
                                stats["null_diffs"] += 1
                            if len(batch_discrepancies) < max_samples and disc is not None:
                                batch_discrepancies.append(disc)
                    if row_changed:
                        stats["changed"] += 1
                    else:
                        stats["identical"] += 1

        return batch_discrepancies, stats


class VectorizedPandasStrategy(BaseComparisonStrategy):
    """
    Vectorized Pandas comparison using outer merge and columnar equality checks.
    Avoids row-by-row dict conversion for matching records.
    """

    def compare_batch(
        self,
        src_df: pd.DataFrame,
        dst_df_aligned: pd.DataFrame,
        key_columns: List[str],
        columns: List[str],
        max_samples: int = 1000,
    ) -> Tuple[List[Discrepancy], Dict[str, int]]:
        batch_discrepancies: List[Discrepancy] = []
        stats = {
            "evaluated": 0,
            "identical": 0,
            "missing": 0,
            "extra": 0,
            "changed": 0,
            "column_diffs": 0,
            "null_diffs": 0,
            "duplicate_keys": 0,
        }

        # Check for duplicate keys
        src_df_clean, src_dup_discs, src_dup_count = _detect_and_extract_duplicate_keys(
            src_df, key_columns, "source", max_samples
        )
        dst_df_clean, dst_dup_discs, dst_dup_count = _detect_and_extract_duplicate_keys(
            dst_df_aligned, key_columns, "destination", max_samples - len(src_dup_discs)
        )
        batch_discrepancies.extend(src_dup_discs)
        batch_discrepancies.extend(dst_dup_discs)
        stats["duplicate_keys"] = src_dup_count + dst_dup_count

        if src_df_clean.empty and dst_df_clean.empty:
            return batch_discrepancies, stats

        if src_df_clean.empty:
            stats["extra"] = len(dst_df_clean)
            stats["evaluated"] = len(dst_df_clean)
            for _, row in dst_df_clean.head(max_samples - len(batch_discrepancies)).iterrows():
                r_dict = row.to_dict()
                batch_discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.EXTRA_IN_DEST,
                        key=_format_key_str(r_dict, key_columns),
                        details={"dest_record": r_dict},
                    )
                )
            return batch_discrepancies, stats

        if dst_df_clean.empty:
            stats["missing"] = len(src_df_clean)
            stats["evaluated"] = len(src_df_clean)
            for _, row in src_df_clean.head(max_samples - len(batch_discrepancies)).iterrows():
                r_dict = row.to_dict()
                batch_discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.MISSING_IN_DEST,
                        key=_format_key_str(r_dict, key_columns),
                        details={"source_record": r_dict},
                    )
                )
            return batch_discrepancies, stats

        # Vectorized outer merge on key columns
        merged = pd.merge(
            src_df_clean,
            dst_df_clean,
            on=key_columns,
            how="outer",
            indicator=True,
            suffixes=("_src", "_dst"),
        )
        stats["evaluated"] = len(merged)

        # 1. Missing in destination
        missing_mask = merged["_merge"] == "left_only"
        missing_count = int(missing_mask.sum())
        stats["missing"] = missing_count
        if missing_count > 0 and len(batch_discrepancies) < max_samples:
            rem = max_samples - len(batch_discrepancies)
            missing_rows = merged[missing_mask].head(rem)
            for _, row in missing_rows.iterrows():
                r_src = {c: row[f"{c}_src"] if f"{c}_src" in row else row[c] for c in columns}
                batch_discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.MISSING_IN_DEST,
                        key=_format_key_str(r_src, key_columns),
                        details={"source_record": r_src},
                    )
                )

        # 2. Extra in destination
        extra_mask = merged["_merge"] == "right_only"
        extra_count = int(extra_mask.sum())
        stats["extra"] = extra_count
        if extra_count > 0 and len(batch_discrepancies) < max_samples:
            rem = max_samples - len(batch_discrepancies)
            extra_rows = merged[extra_mask].head(rem)
            for _, row in extra_rows.iterrows():
                r_dst = {c: row[f"{c}_dst"] if f"{c}_dst" in row else row[c] for c in columns}
                batch_discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.EXTRA_IN_DEST,
                        key=_format_key_str(r_dst, key_columns),
                        details={"dest_record": r_dst},
                    )
                )

        # 3. Present in both: columnar vector comparison
        both_mask = merged["_merge"] == "both"
        both_count = int(both_mask.sum())
        if both_count == 0:
            return batch_discrepancies, stats

        both_df = merged[both_mask]
        compare_cols = [c for c in columns if c not in key_columns]

        if not compare_cols:
            stats["identical"] = both_count
            return batch_discrepancies, stats

        col_mismatch_masks: Dict[str, pd.Series] = {}
        col_null_mismatch_masks: Dict[str, pd.Series] = {}
        row_has_mismatch = pd.Series(False, index=both_df.index)

        for col in compare_cols:
            src_col_name = f"{col}_src" if f"{col}_src" in both_df else col
            dst_col_name = f"{col}_dst" if f"{col}_dst" in both_df else col

            s_s = both_df[src_col_name]
            d_s = both_df[dst_col_name]

            s_is_null = s_s.isna()
            d_is_null = d_s.isna()
            null_diff = s_is_null != d_is_null

            both_null = s_is_null & d_is_null
            exact_eq = (s_s == d_s) | both_null

            final_diff = pd.Series(False, index=both_df.index)
            final_null_diff = pd.Series(False, index=both_df.index)

            if null_diff.any():
                final_null_diff.loc[both_df.index[null_diff]] = True
                final_diff.loc[both_df.index[null_diff]] = True

            non_null_unequal = ~exact_eq & ~null_diff
            if non_null_unequal.any():
                unequal_indices = both_df.index[non_null_unequal]
                s_canon = s_s.loc[unequal_indices].apply(canonical_value_str)
                d_canon = d_s.loc[unequal_indices].apply(canonical_value_str)
                true_mismatch = s_canon != d_canon
                final_diff.loc[unequal_indices] = true_mismatch

            col_mismatch_masks[col] = final_diff
            col_null_mismatch_masks[col] = final_null_diff
            row_has_mismatch |= final_diff

        changed_count = int(row_has_mismatch.sum())
        stats["changed"] = changed_count
        stats["identical"] = both_count - changed_count

        for col, mask in col_mismatch_masks.items():
            stats["column_diffs"] += int(mask.sum())
        for col, mask in col_null_mismatch_masks.items():
            stats["null_diffs"] += int(mask.sum())

        if changed_count > 0 and len(batch_discrepancies) < max_samples:
            rem = max_samples - len(batch_discrepancies)
            diff_df = both_df[row_has_mismatch].head(rem)
            for idx, row in diff_df.iterrows():
                key_repr = _format_key_str(row, key_columns)
                for col in compare_cols:
                    if col_mismatch_masks[col].loc[idx]:
                        src_val_raw = row[f"{col}_src" if f"{col}_src" in row else col]
                        dst_val_raw = row[f"{col}_dst" if f"{col}_dst" in row else col]
                        s_canon = canonical_value_str(src_val_raw)
                        d_canon = canonical_value_str(dst_val_raw)
                        is_null = col_null_mismatch_masks[col].loc[idx]
                        disc_type = DiscrepancyType.NULL_MISMATCH if is_null else DiscrepancyType.VALUE_MISMATCH
                        batch_discrepancies.append(
                            Discrepancy(
                                type=disc_type,
                                key=key_repr,
                                column=col,
                                source_val=s_canon,
                                dest_val=d_canon,
                                details={
                                    "raw_source": str(src_val_raw),
                                    "raw_dest": str(dst_val_raw),
                                },
                            )
                        )
                        if len(batch_discrepancies) >= max_samples:
                            break
                if len(batch_discrepancies) >= max_samples:
                    break

        return batch_discrepancies, stats


class DuckDBStrategy(BaseComparisonStrategy):
    """
    Analytical SQL-based comparison using an embedded in-memory DuckDB engine.
    High throughput, zero full-table Python object instantiation, null-safe equality.
    """

    def __init__(self):
        import duckdb
        self.duckdb = duckdb
        self.con = self.duckdb.connect(database=":memory:")

    def compare_batch(
        self,
        src_df: pd.DataFrame,
        dst_df_aligned: pd.DataFrame,
        key_columns: List[str],
        columns: List[str],
        max_samples: int = 1000,
    ) -> Tuple[List[Discrepancy], Dict[str, int]]:
        batch_discrepancies: List[Discrepancy] = []
        stats = {
            "evaluated": 0,
            "identical": 0,
            "missing": 0,
            "extra": 0,
            "changed": 0,
            "column_diffs": 0,
            "null_diffs": 0,
            "duplicate_keys": 0,
        }

        # Check for duplicate keys
        src_df_clean, src_dup_discs, src_dup_count = _detect_and_extract_duplicate_keys(
            src_df, key_columns, "source", max_samples
        )
        dst_df_clean, dst_dup_discs, dst_dup_count = _detect_and_extract_duplicate_keys(
            dst_df_aligned, key_columns, "destination", max_samples - len(src_dup_discs)
        )
        batch_discrepancies.extend(src_dup_discs)
        batch_discrepancies.extend(dst_dup_discs)
        stats["duplicate_keys"] = src_dup_count + dst_dup_count

        if src_df_clean.empty and dst_df_clean.empty:
            return batch_discrepancies, stats

        cur = self.con.cursor()
        try:
            cur.register("src_batch", src_df_clean)
            cur.register("dst_batch", dst_df_clean)

            join_cond = " AND ".join([f's."{k}" = d."{k}"' for k in key_columns])
            compare_cols = [c for c in columns if c not in key_columns]

            # 1. Missing in destination
            if not src_df_clean.empty and not dst_df_clean.empty:
                missing_sql = f"""
                    SELECT s.* FROM src_batch s
                    LEFT JOIN dst_batch d ON {join_cond}
                    WHERE d."{key_columns[0]}" IS NULL;
                """
                missing_df = cur.execute(missing_sql).fetchdf()
            elif not src_df_clean.empty:
                missing_df = src_df_clean
            else:
                missing_df = pd.DataFrame()

            stats["missing"] = len(missing_df)
            if not missing_df.empty and len(batch_discrepancies) < max_samples:
                rem = max_samples - len(batch_discrepancies)
                for _, row in missing_df.head(rem).iterrows():
                    r_dict = row.to_dict()
                    batch_discrepancies.append(
                        Discrepancy(
                            type=DiscrepancyType.MISSING_IN_DEST,
                            key=_format_key_str(r_dict, key_columns),
                            details={"source_record": r_dict},
                        )
                    )

            # 2. Extra in destination
            if not src_df_clean.empty and not dst_df_clean.empty:
                extra_sql = f"""
                    SELECT d.* FROM dst_batch d
                    LEFT JOIN src_batch s ON {join_cond}
                    WHERE s."{key_columns[0]}" IS NULL;
                """
                extra_df = cur.execute(extra_sql).fetchdf()
            elif not dst_df_clean.empty:
                extra_df = dst_df_clean
            else:
                extra_df = pd.DataFrame()

            stats["extra"] = len(extra_df)
            if not extra_df.empty and len(batch_discrepancies) < max_samples:
                rem = max_samples - len(batch_discrepancies)
                for _, row in extra_df.head(rem).iterrows():
                    r_dict = row.to_dict()
                    batch_discrepancies.append(
                        Discrepancy(
                            type=DiscrepancyType.EXTRA_IN_DEST,
                            key=_format_key_str(r_dict, key_columns),
                            details={"dest_record": r_dict},
                        )
                    )

            if src_df_clean.empty or dst_df_clean.empty or not compare_cols:
                stats["evaluated"] = stats["missing"] + stats["extra"]
                return batch_discrepancies, stats

            # 3. Inner join for candidate rows present in both
            diff_conditions = [
                f'(s."{c}" IS DISTINCT FROM d."{c}")'
                for c in compare_cols
            ]
            any_diff_sql = " OR ".join(diff_conditions)

            total_paired = cur.execute(f"SELECT COUNT(*) FROM src_batch s JOIN dst_batch d ON {join_cond};").fetchone()[0]
            stats["evaluated"] = stats["missing"] + stats["extra"] + total_paired

            proj_parts = [f's."{k}"' for k in key_columns]
            for c in compare_cols:
                proj_parts.append(f's."{c}" AS "src_{c}"')
                proj_parts.append(f'd."{c}" AS "dst_{c}"')
                proj_parts.append(f'(s."{c}" IS DISTINCT FROM d."{c}") AS "diff_{c}"')

            diff_query = f"""
                SELECT {", ".join(proj_parts)}
                FROM src_batch s
                JOIN dst_batch d ON {join_cond}
                WHERE {any_diff_sql};
            """
            diff_records_df = cur.execute(diff_query).fetchdf()

            changed_count = 0
            if not diff_records_df.empty:
                for _, row in diff_records_df.iterrows():
                    key_repr = _format_key_str(row, key_columns)
                    row_mismatched = False
                    for c in compare_cols:
                        if row[f"diff_{c}"]:
                            s_raw = row[f"src_{c}"]
                            d_raw = row[f"dst_{c}"]
                            is_diff, disc, is_null = _classify_field_discrepancy(c, s_raw, d_raw, key_repr)
                            if is_diff:
                                row_mismatched = True
                                stats["column_diffs"] += 1
                                if is_null:
                                    stats["null_diffs"] += 1
                                if len(batch_discrepancies) < max_samples and disc is not None:
                                    batch_discrepancies.append(disc)
                    if row_mismatched:
                        changed_count += 1

            stats["changed"] = changed_count
            stats["identical"] = total_paired - changed_count
        finally:
            cur.close()

        return batch_discrepancies, stats


class PolarsStrategy(BaseComparisonStrategy):
    """Columnar execution using Polars LazyFrames / DataFrames."""

    def __init__(self):
        import polars as pl
        self.pl = pl

    def compare_batch(
        self,
        src_df: pd.DataFrame,
        dst_df_aligned: pd.DataFrame,
        key_columns: List[str],
        columns: List[str],
        max_samples: int = 1000,
    ) -> Tuple[List[Discrepancy], Dict[str, int]]:
        batch_discrepancies: List[Discrepancy] = []
        stats = {
            "evaluated": 0,
            "identical": 0,
            "missing": 0,
            "extra": 0,
            "changed": 0,
            "column_diffs": 0,
            "null_diffs": 0,
            "duplicate_keys": 0,
        }

        # Check for duplicate keys
        src_df_clean, src_dup_discs, src_dup_count = _detect_and_extract_duplicate_keys(
            src_df, key_columns, "source", max_samples
        )
        dst_df_clean, dst_dup_discs, dst_dup_count = _detect_and_extract_duplicate_keys(
            dst_df_aligned, key_columns, "destination", max_samples - len(src_dup_discs)
        )
        batch_discrepancies.extend(src_dup_discs)
        batch_discrepancies.extend(dst_dup_discs)
        stats["duplicate_keys"] = src_dup_count + dst_dup_count

        if src_df_clean.empty and dst_df_clean.empty:
            return batch_discrepancies, stats

        # Align key datatypes and datetime timezones to prevent Polars SchemaError
        src_copy = src_df_clean.copy()
        dst_copy = dst_df_clean.copy()
        for k in key_columns:
            if k in src_copy.columns and k in dst_copy.columns:
                if src_copy[k].dtype != dst_copy[k].dtype:
                    src_copy[k] = src_copy[k].astype(str)
                    dst_copy[k] = dst_copy[k].astype(str)

        for c in columns:
            if c in src_copy.columns and c in dst_copy.columns:
                s_is_dt = pd.api.types.is_datetime64_any_dtype(src_copy[c])
                d_is_dt = pd.api.types.is_datetime64_any_dtype(dst_copy[c])
                if s_is_dt and d_is_dt:
                    if getattr(src_copy[c].dtype, "tz", None) is not None:
                        src_copy[c] = src_copy[c].dt.tz_convert("UTC").dt.tz_localize(None)
                    if getattr(dst_copy[c].dtype, "tz", None) is not None:
                        dst_copy[c] = dst_copy[c].dt.tz_convert("UTC").dt.tz_localize(None)

        pl_src = self.pl.from_pandas(src_copy) if not src_copy.empty else self.pl.DataFrame()
        pl_dst = self.pl.from_pandas(dst_copy) if not dst_copy.empty else self.pl.DataFrame()

        # Handle empty cases
        if pl_src.is_empty():
            stats["extra"] = len(pl_dst)
            stats["evaluated"] = len(pl_dst)
            for row in pl_dst.head(max_samples - len(batch_discrepancies)).iter_rows(named=True):
                batch_discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.EXTRA_IN_DEST,
                        key=_format_key_str(row, key_columns),
                        details={"dest_record": row},
                    )
                )
            return batch_discrepancies, stats

        if pl_dst.is_empty():
            stats["missing"] = len(pl_src)
            stats["evaluated"] = len(pl_src)
            for row in pl_src.head(max_samples - len(batch_discrepancies)).iter_rows(named=True):
                batch_discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.MISSING_IN_DEST,
                        key=_format_key_str(row, key_columns),
                        details={"source_record": row},
                    )
                )
            return batch_discrepancies, stats

        # Anti-join for missing
        missing_pl = pl_src.join(pl_dst, on=key_columns, how="anti")
        stats["missing"] = len(missing_pl)
        if len(missing_pl) > 0 and len(batch_discrepancies) < max_samples:
            rem = max_samples - len(batch_discrepancies)
            for row in missing_pl.head(rem).iter_rows(named=True):
                batch_discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.MISSING_IN_DEST,
                        key=_format_key_str(row, key_columns),
                        details={"source_record": row},
                    )
                )

        # Anti-join for extra
        extra_pl = pl_dst.join(pl_src, on=key_columns, how="anti")
        stats["extra"] = len(extra_pl)
        if len(extra_pl) > 0 and len(batch_discrepancies) < max_samples:
            rem = max_samples - len(batch_discrepancies)
            for row in extra_pl.head(rem).iter_rows(named=True):
                batch_discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.EXTRA_IN_DEST,
                        key=_format_key_str(row, key_columns),
                        details={"dest_record": row},
                    )
                )

        # Inner join for common keys
        compare_cols = [c for c in columns if c not in key_columns]
        joined = pl_src.join(pl_dst, on=key_columns, how="inner", suffix="_dst")
        stats["evaluated"] = stats["missing"] + stats["extra"] + len(joined)

        if joined.is_empty() or not compare_cols:
            stats["identical"] = len(joined)
            return batch_discrepancies, stats

        # Use ne_missing for null-safe inequality
        diff_exprs = [
            self.pl.col(c).ne_missing(self.pl.col(f"{c}_dst"))
            for c in compare_cols
        ]
        any_diff = diff_exprs[0]
        for e in diff_exprs[1:]:
            any_diff = any_diff | e

        candidate_diffs = joined.filter(any_diff)
        changed_count = 0

        if not candidate_diffs.is_empty():
            for row in candidate_diffs.iter_rows(named=True):
                key_repr = _format_key_str(row, key_columns)
                row_mismatched = False
                for c in compare_cols:
                    s_raw = row[c]
                    d_raw = row[f"{c}_dst"]
                    is_diff, disc, is_null = _classify_field_discrepancy(c, s_raw, d_raw, key_repr)
                    if is_diff:
                        row_mismatched = True
                        stats["column_diffs"] += 1
                        if is_null:
                            stats["null_diffs"] += 1
                        if len(batch_discrepancies) < max_samples and disc is not None:
                            batch_discrepancies.append(disc)
                if row_mismatched:
                    changed_count += 1

        stats["changed"] = changed_count
        stats["identical"] = len(joined) - changed_count

        return batch_discrepancies, stats


class HashAssistedStrategy(BaseComparisonStrategy):
    """
    Vectorized SHA-256 fingerprinting for candidate detection followed by exact value verification.
    Combines rapid hash matching for identical rows with exact column diagnosis for differences.
    """

    def compare_batch(
        self,
        src_df: pd.DataFrame,
        dst_df_aligned: pd.DataFrame,
        key_columns: List[str],
        columns: List[str],
        max_samples: int = 1000,
    ) -> Tuple[List[Discrepancy], Dict[str, int]]:
        batch_discrepancies: List[Discrepancy] = []
        stats = {
            "evaluated": 0,
            "identical": 0,
            "missing": 0,
            "extra": 0,
            "changed": 0,
            "column_diffs": 0,
            "null_diffs": 0,
            "duplicate_keys": 0,
        }

        # Check for duplicate keys
        src_df_clean, src_dup_discs, src_dup_count = _detect_and_extract_duplicate_keys(
            src_df, key_columns, "source", max_samples
        )
        dst_df_clean, dst_dup_discs, dst_dup_count = _detect_and_extract_duplicate_keys(
            dst_df_aligned, key_columns, "destination", max_samples - len(src_dup_discs)
        )
        batch_discrepancies.extend(src_dup_discs)
        batch_discrepancies.extend(dst_dup_discs)
        stats["duplicate_keys"] = src_dup_count + dst_dup_count

        if src_df_clean.empty and dst_df_clean.empty:
            return batch_discrepancies, stats

        # Vectorized outer merge on key columns
        merged = pd.merge(
            src_df_clean,
            dst_df_clean,
            on=key_columns,
            how="outer",
            indicator=True,
            suffixes=("_src", "_dst"),
        )
        stats["evaluated"] = len(merged)

        # 1. Missing in destination
        missing_mask = merged["_merge"] == "left_only"
        missing_count = int(missing_mask.sum())
        stats["missing"] = missing_count
        if missing_count > 0 and len(batch_discrepancies) < max_samples:
            rem = max_samples - len(batch_discrepancies)
            for _, row in merged[missing_mask].head(rem).iterrows():
                r_src = {c: row[f"{c}_src"] if f"{c}_src" in row else row[c] for c in columns}
                batch_discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.MISSING_IN_DEST,
                        key=_format_key_str(r_src, key_columns),
                        details={"source_record": r_src},
                    )
                )

        # 2. Extra in destination
        extra_mask = merged["_merge"] == "right_only"
        extra_count = int(extra_mask.sum())
        stats["extra"] = extra_count
        if extra_count > 0 and len(batch_discrepancies) < max_samples:
            rem = max_samples - len(batch_discrepancies)
            for _, row in merged[extra_mask].head(rem).iterrows():
                r_dst = {c: row[f"{c}_dst"] if f"{c}_dst" in row else row[c] for c in columns}
                batch_discrepancies.append(
                    Discrepancy(
                        type=DiscrepancyType.EXTRA_IN_DEST,
                        key=_format_key_str(r_dst, key_columns),
                        details={"dest_record": r_dst},
                    )
                )

        # 3. Present in both: compute row hashes
        both_mask = merged["_merge"] == "both"
        both_count = int(both_mask.sum())
        if both_count == 0:
            return batch_discrepancies, stats

        both_df = merged[both_mask]
        compare_cols = [c for c in columns if c not in key_columns]

        if not compare_cols:
            stats["identical"] = both_count
            return batch_discrepancies, stats

        # Vectorized hash computation
        def get_row_hash(row, suffix: str):
            parts = []
            for col in sorted(compare_cols):
                col_name = f"{col}{suffix}" if f"{col}{suffix}" in row else col
                parts.append(f"{col}:{canonical_value_str(row[col_name])}")
            serialized = "|".join(parts)
            return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

        src_hashes = both_df.apply(lambda r: get_row_hash(r, "_src"), axis=1)
        dst_hashes = both_df.apply(lambda r: get_row_hash(r, "_dst"), axis=1)

        hash_match = src_hashes == dst_hashes
        identical_count = int(hash_match.sum())
        stats["identical"] = identical_count

        changed_count = 0
        if not hash_match.all():
            mismatched_df = both_df[~hash_match]
            for idx, row in mismatched_df.iterrows():
                key_repr = _format_key_str(row, key_columns)
                row_mismatched = False
                for col in compare_cols:
                    s_raw = row[f"{col}_src" if f"{col}_src" in row else col]
                    d_raw = row[f"{col}_dst" if f"{col}_dst" in row else col]
                    is_diff, disc, is_null = _classify_field_discrepancy(col, s_raw, d_raw, key_repr)
                    if is_diff:
                        row_mismatched = True
                        stats["column_diffs"] += 1
                        if is_null:
                            stats["null_diffs"] += 1
                        if len(batch_discrepancies) < max_samples and disc is not None:
                            batch_discrepancies.append(disc)
                if row_mismatched:
                    changed_count += 1
                else:
                    stats["identical"] += 1

        stats["changed"] = changed_count
        return batch_discrepancies, stats

        return batch_discrepancies, stats


def get_comparison_strategy(strategy_name: str = "auto") -> BaseComparisonStrategy:
    """Factory selecting the optimal or specified comparison strategy."""
    name = strategy_name.lower().strip()
    if name == "duckdb":
        try:
            return DuckDBStrategy()
        except ImportError:
            logger.warning("DuckDB not installed; falling back to vectorized pandas.")
            return VectorizedPandasStrategy()
    elif name == "polars":
        try:
            return PolarsStrategy()
        except ImportError:
            logger.warning("Polars not installed; falling back to vectorized pandas.")
            return VectorizedPandasStrategy()
    elif name == "hash_assisted":
        return HashAssistedStrategy()
    elif name == "baseline":
        return BaselinePythonStrategy()
    elif name in ["vectorized_pandas", "pandas"]:
        return VectorizedPandasStrategy()
    elif name == "auto":
        # Default priority: DuckDB -> Polars -> Vectorized Pandas
        try:
            return DuckDBStrategy()
        except Exception:
            try:
                return PolarsStrategy()
            except Exception:
                return VectorizedPandasStrategy()
    else:
        logger.warning("Unknown strategy '%s'; using default auto.", strategy_name)
        return get_comparison_strategy("auto")


class RecordComparisonEngine:
    """
    Streams and validates records across systems using keyset pagination and pluggable analytical engines.
    Scales to millions of records with bounded memory, supporting composite keys and discrepancy sampling.
    """

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
        dest_schema: Optional[str] = "public",
        strategy: str = "auto",
        max_discrepancy_samples: int = 1000,
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
        self.strategy_name = strategy
        self.max_discrepancy_samples = max_discrepancy_samples

        self.strategy_impl = get_comparison_strategy(strategy)
        self.primary_key = key_columns[0]
        self.src_cols = list(column_mapping.keys())
        self.dst_cols = [column_mapping[c] for c in self.src_cols]

    def stream_compare(
        self,
        max_records: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Generator[Tuple[List[Discrepancy], Dict[str, int]], None, None]:
        """
        Stream records in deterministic order using keyset pagination.
        Yields (batch_discrepancies, batch_stats) to keep memory strictly bounded.
        """
        last_src_key: Optional[Any] = None
        last_dst_key: Optional[Any] = None
        src_exhausted = False
        dst_exhausted = False

        total_evaluated = 0
        total_samples_collected = 0
        dest_key_name = self.column_mapping[self.primary_key]
        dst_rename_map = {dst: src for src, dst in self.column_mapping.items()}

        src_buffer = pd.DataFrame(columns=self.src_cols)
        dst_buffer = pd.DataFrame(columns=self.src_cols)
        is_composite = len(self.key_columns) > 1

        while True:
            if cancel_check and cancel_check():
                logger.info("Record comparison cancelled by user.")
                break

            if src_exhausted and dst_exhausted and src_buffer.empty and dst_buffer.empty:
                break

            # Fetch batches from source if buffer is low
            if len(src_buffer) < self.batch_size and not src_exhausted:
                new_src_df, next_src_cursor = self.source.fetch_records_batch(
                    table_name=self.source_table,
                    columns=self.src_cols,
                    key_columns=[self.primary_key],
                    batch_size=self.batch_size,
                    last_key=last_src_key,
                    schema_or_db=self.source_db,
                )
                if new_src_df.empty or next_src_cursor is None:
                    src_exhausted = True
                if not new_src_df.empty:
                    last_src_key = next_src_cursor
                    src_buffer = pd.concat([src_buffer, new_src_df], ignore_index=True) if not src_buffer.empty else new_src_df

            # Fetch batches from destination if buffer is low
            if len(dst_buffer) < self.batch_size and not dst_exhausted:
                new_dst_df, next_dst_cursor = self.dest.fetch_records_batch(
                    table_name=self.dest_table,
                    columns=self.dst_cols,
                    key_columns=[dest_key_name],
                    batch_size=self.batch_size,
                    last_key=last_dst_key,
                    schema_or_db=self.dest_schema,
                )
                if new_dst_df.empty or next_dst_cursor is None:
                    dst_exhausted = True
                if not new_dst_df.empty:
                    last_dst_key = next_dst_cursor
                    new_dst_aligned = new_dst_df.rename(columns=dst_rename_map)
                    dst_buffer = pd.concat([dst_buffer, new_dst_aligned], ignore_index=True) if not dst_buffer.empty else new_dst_aligned

            if src_buffer.empty and dst_buffer.empty:
                break

            # Determine synchronized batch slices
            if not src_buffer.empty and not dst_buffer.empty:
                if not src_exhausted and not dst_exhausted:
                    if is_composite:
                        src_keys = src_buffer[self.key_columns].apply(tuple, axis=1)
                        dst_keys = dst_buffer[self.key_columns].apply(tuple, axis=1)
                        cutoff = min(src_keys.iloc[-1], dst_keys.iloc[-1])
                        src_mask = src_keys <= cutoff
                        dst_mask = dst_keys <= cutoff
                    else:
                        pk = self.primary_key
                        s_max = src_buffer[pk].iloc[-1]
                        d_max = dst_buffer[pk].iloc[-1]
                        if type(s_max) is not type(d_max):
                            try:
                                s_cmp = float(s_max)
                                d_cmp = float(d_max)
                                cutoff = min(s_cmp, d_cmp)
                                src_mask = src_buffer[pk].astype(float) <= cutoff
                                dst_mask = dst_buffer[pk].astype(float) <= cutoff
                            except Exception:
                                cutoff = min(s_max, d_max)
                                src_mask = src_buffer[pk] <= cutoff
                                dst_mask = dst_buffer[pk] <= cutoff
                        else:
                            cutoff = min(s_max, d_max)
                            src_mask = src_buffer[pk] <= cutoff
                            dst_mask = dst_buffer[pk] <= cutoff

                    src_batch = src_buffer[src_mask]
                    src_buffer = src_buffer[~src_mask].reset_index(drop=True)
                    dst_batch = dst_buffer[dst_mask]
                    dst_buffer = dst_buffer[~dst_mask].reset_index(drop=True)
                elif src_exhausted and not dst_exhausted:
                    if is_composite:
                        src_keys = src_buffer[self.key_columns].apply(tuple, axis=1)
                        cutoff = src_keys.iloc[-1]
                        dst_keys = dst_buffer[self.key_columns].apply(tuple, axis=1)
                        dst_mask = dst_keys <= cutoff
                    else:
                        pk = self.primary_key
                        cutoff = src_buffer[pk].iloc[-1]
                        dst_mask = dst_buffer[pk] <= cutoff
                    src_batch = src_buffer
                    src_buffer = pd.DataFrame(columns=self.src_cols)
                    dst_batch = dst_buffer[dst_mask]
                    dst_buffer = dst_buffer[~dst_mask].reset_index(drop=True)
                elif dst_exhausted and not src_exhausted:
                    if is_composite:
                        dst_keys = dst_buffer[self.key_columns].apply(tuple, axis=1)
                        cutoff = dst_keys.iloc[-1]
                        src_keys = src_buffer[self.key_columns].apply(tuple, axis=1)
                        src_mask = src_keys <= cutoff
                    else:
                        pk = self.primary_key
                        cutoff = dst_buffer[pk].iloc[-1]
                        src_mask = src_buffer[pk] <= cutoff
                    dst_batch = dst_buffer
                    dst_buffer = pd.DataFrame(columns=self.src_cols)
                    src_batch = src_buffer[src_mask]
                    src_buffer = src_buffer[~src_mask].reset_index(drop=True)
                else:
                    src_batch = src_buffer
                    dst_batch = dst_buffer
                    src_buffer = pd.DataFrame(columns=self.src_cols)
                    dst_buffer = pd.DataFrame(columns=self.src_cols)
            elif not src_buffer.empty and dst_buffer.empty:
                if dst_exhausted:
                    src_batch = src_buffer
                    dst_batch = pd.DataFrame(columns=self.src_cols)
                    src_buffer = pd.DataFrame(columns=self.src_cols)
                else:
                    continue
            elif src_buffer.empty and not dst_buffer.empty:
                if src_exhausted:
                    src_batch = pd.DataFrame(columns=self.src_cols)
                    dst_batch = dst_buffer
                    dst_buffer = pd.DataFrame(columns=self.src_cols)
                else:
                    continue
            else:
                break

            if src_batch.empty and dst_batch.empty:
                if src_exhausted and dst_exhausted:
                    break
                continue

            # Calculate remaining sample headroom
            headroom = max(0, self.max_discrepancy_samples - total_samples_collected)

            # Delegate comparison to selected strategy
            batch_discrepancies, stats = self.strategy_impl.compare_batch(
                src_df=src_batch,
                dst_df_aligned=dst_batch,
                key_columns=self.key_columns,
                columns=self.src_cols,
                max_samples=headroom,
            )

            total_evaluated += stats["evaluated"]
            total_samples_collected += len(batch_discrepancies)

            yield batch_discrepancies, stats

            if progress_callback:
                progress_callback(total_evaluated, -1, f"Validated {total_evaluated:,} records ({stats['identical']:,} identical)...")

            if max_records and total_evaluated >= max_records:
                logger.info("Reached maximum record comparison limit (%d).", max_records)
                break

            if src_exhausted and dst_exhausted and src_buffer.empty and dst_buffer.empty:
                break


def run_record_comparison_check(
    engine: RecordComparisonEngine,
    max_records: Optional[int] = None,
    discrepancy_sink: Optional[Callable[[List[Discrepancy]], None]] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Tuple[CheckResult, int, Dict[str, int]]:
    """
    Executes record-level comparison streaming, passing discrepancies to sink and computing status.
    Returns: (CheckResult, total_discrepancy_count, discrepancy_counts_by_type)
    """
    start = time.perf_counter()
    check_id = f"CHK-REC-COMP-{uuid.uuid4().hex[:8]}"

    total_evaluated = 0
    total_identical = 0
    total_missing = 0
    total_extra = 0
    total_changed = 0
    total_col_diffs = 0
    total_null_diffs = 0
    total_dup_keys = 0

    for batch_discrepancies, stats in engine.stream_compare(
        max_records=max_records,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    ):
        total_evaluated += stats["evaluated"]
        total_identical += stats["identical"]
        total_missing += stats["missing"]
        total_extra += stats["extra"]
        total_changed += stats["changed"]
        total_col_diffs += stats["column_diffs"]
        total_null_diffs += stats.get("null_diffs", 0)
        total_dup_keys += stats.get("duplicate_keys", 0)

        if discrepancy_sink and batch_discrepancies:
            discrepancy_sink(batch_discrepancies)

    elapsed_ms = (time.perf_counter() - start) * 1000.0

    total_val_diffs = max(0, total_col_diffs - total_null_diffs)
    total_discrepancies = total_missing + total_extra + total_changed + total_dup_keys
    counts_by_type = {
        DiscrepancyType.MISSING_IN_DEST.value: total_missing,
        DiscrepancyType.EXTRA_IN_DEST.value: total_extra,
        DiscrepancyType.VALUE_MISMATCH.value: total_val_diffs,
        DiscrepancyType.NULL_MISMATCH.value: total_null_diffs,
        DiscrepancyType.DUPLICATE_KEY.value: total_dup_keys,
    }

    if total_discrepancies == 0 and total_evaluated > 0:
        status = ValidationStatus.PASSED
        err_msg = None
    elif total_evaluated == 0:
        status = ValidationStatus.INCONCLUSIVE
        err_msg = "No records were evaluated during record-level comparison."
    else:
        status = ValidationStatus.FAILED
        parts = []
        if total_missing:
            parts.append(f"{total_missing} missing")
        if total_extra:
            parts.append(f"{total_extra} extra")
        if total_changed:
            parts.append(f"{total_changed} changed records ({total_val_diffs} value diffs, {total_null_diffs} null diffs)")
        if total_dup_keys:
            parts.append(f"{total_dup_keys} duplicate keys")
        err_msg = f"Record discrepancies detected: {', '.join(parts)}"

    check_result = CheckResult(
        check_id=check_id,
        check_type=CheckType.RECORD_COMPARISON,
        check_name="Record-Level Discrepancy & Value Parity",
        status=status,
        source_value=f"{total_evaluated:,} evaluated",
        dest_value=f"{total_identical:,} identical",
        difference=f"{total_discrepancies:,} mismatched records",
        tolerance="0 discrepancies (Exact Parity)",
        duration_ms=round(elapsed_ms, 2),
        error_details=err_msg,
        metadata={
            "strategy": engine.strategy_name,
            "total_evaluated": total_evaluated,
            "identical": total_identical,
            "missing": total_missing,
            "extra": total_extra,
            "changed": total_changed,
            "column_differences": total_col_diffs,
            "null_differences": total_null_diffs,
            "duplicate_keys": total_dup_keys,
        },
    )

    return check_result, total_discrepancies, counts_by_type
