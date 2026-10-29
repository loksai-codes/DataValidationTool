"""Preflight configuration validators."""
from __future__ import annotations

from typing import Any

from data_validator.config.models import PartitionStrategy, ValidationJobConfig
from data_validator.security.secrets import SecretStore, default_secret_store


class PreflightValidationError(Exception):
    """Raised when preflight configuration validation fails."""
    pass


def preflight_validate_job(
    config: ValidationJobConfig,
    secret_store: SecretStore = default_secret_store,
    source_metadata: Any = None,
    target_metadata: Any = None,
) -> list[str]:
    """Perform preflight checks on job config before execution starts.

    Returns a list of warning messages (non-blocking).
    Raises PreflightValidationError if blocking violations are found.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Unique keys check
    if not config.comparison.unique_keys:
        errors.append("At least one unique key column is required for comparison.")

    # 2. Secret resolution check
    try:
        secret_store.get_connection(config.source.connection_id)
    except KeyError:
        errors.append(
            f"Source connection '{config.source.connection_id}' not found in secrets/configuration."
        )

    try:
        secret_store.get_connection(config.target.connection_id)
    except KeyError:
        errors.append(
            f"Target connection '{config.target.connection_id}' not found in secrets/configuration."
        )

    # 3. Partition strategy sanity
    partition = config.comparison.partition
    if partition.strategy in (
        PartitionStrategy.KEY_RANGE,
        PartitionStrategy.DATE_RANGE,
        PartitionStrategy.KEYSET_PAGINATION,
    ):
        if not partition.column:
            errors.append(
                f"Partition strategy '{partition.strategy.value}' requires a partition column."
            )

    # 4. Table metadata cross-check if provided
    if source_metadata and hasattr(source_metadata, "columns"):
        source_cols = {col.name.lower() for col in source_metadata.columns}
        for k in config.comparison.unique_keys:
            if k.lower() not in source_cols:
                errors.append(f"Unique key '{k}' does not exist in source table columns.")
        if partition.column and partition.column.lower() not in source_cols:
            errors.append(f"Partition column '{partition.column}' does not exist in source table columns.")

    if target_metadata and hasattr(target_metadata, "columns"):
        target_cols = {col.name.lower() for col in target_metadata.columns}
        for k in config.comparison.unique_keys:
            if k.lower() not in target_cols:
                errors.append(f"Unique key '{k}' does not exist in target table columns.")
        if partition.column and partition.column.lower() not in target_cols:
            errors.append(f"Partition column '{partition.column}' does not exist in target table columns.")

    if errors:
        raise PreflightValidationError("; ".join(errors))

    return warnings
