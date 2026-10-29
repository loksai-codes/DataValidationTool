"""Config loader for JSON jobs and CSV manifests."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from data_validator.config.models import (
    BatchManifestRow,
    ComparisonConfig,
    EngineType,
    PartitionConfig,
    ProviderType,
    TableEndpointConfig,
    ValidationJobConfig,
)


def load_job_config(source: str | Path | dict[str, Any]) -> ValidationJobConfig:
    """Load and validate a single validation job configuration from file or dict."""
    if isinstance(source, (str, Path)):
        p = Path(source)
        if not p.exists():
            raise FileNotFoundError(f"Configuration file not found: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif isinstance(source, dict):
        data = source
    else:
        raise TypeError("Source must be a file path or dictionary.")

    return ValidationJobConfig.model_validate(data)


def load_batch_manifest(
    csv_path: str | Path, base_dir: str | Path | None = None
) -> list[ValidationJobConfig]:
    """Load a batch of validation jobs from a CSV manifest.

    If a row specifies `config_file`, that JSON file is loaded as the base
    and overridden with the row's specific table/connection settings.
    """
    p = Path(csv_path)
    if not p.exists():
        raise FileNotFoundError(f"Manifest file not found: {p}")

    search_dir = Path(base_dir) if base_dir else p.parent

    jobs: list[ValidationJobConfig] = []
    with open(p, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row_idx, raw_row in enumerate(reader, start=1):
            # Clean empty strings to None
            row_dict = {k.strip(): (v.strip() if v and v.strip() else None) for k, v in raw_row.items()}
            manifest_row = BatchManifestRow.model_validate(row_dict)

            if manifest_row.config_file:
                config_path = search_dir / manifest_row.config_file
                if not config_path.exists():
                    # Fallback to configs/examples if not directly found
                    alt_path = Path("configs/examples") / manifest_row.config_file
                    if alt_path.exists():
                        config_path = alt_path
                    else:
                        raise FileNotFoundError(
                            f"Referenced config file '{manifest_row.config_file}' in manifest row {row_idx} not found."
                        )
                base_cfg = load_job_config(config_path)
                data = base_cfg.model_dump(by_alias=True)
                # Override with row specifics
                data["job_name"] = manifest_row.job_name
                data["source"]["engine"] = manifest_row.source_engine.value
                data["source"]["connection_id"] = manifest_row.source_connection_id
                data["source"]["database"] = manifest_row.source_database
                data["source"]["schema"] = manifest_row.source_schema
                data["source"]["table"] = manifest_row.source_table

                data["target"]["engine"] = manifest_row.target_engine.value
                data["target"]["connection_id"] = manifest_row.target_connection_id
                data["target"]["database"] = manifest_row.target_database
                data["target"]["schema"] = manifest_row.target_schema
                data["target"]["table"] = manifest_row.target_table

                data["comparison"]["unique_keys"] = [k.strip() for k in manifest_row.unique_key.split(",") if k.strip()]
                jobs.append(ValidationJobConfig.model_validate(data))
            else:
                # Construct standalone job from row
                keys = [k.strip() for k in manifest_row.unique_key.split(",") if k.strip()]
                job = ValidationJobConfig(
                    config_version=1,
                    job_name=manifest_row.job_name,
                    source=TableEndpointConfig(
                        provider=ProviderType.AWS if manifest_row.source_engine == EngineType.ATHENA else ProviderType.OTHER,
                        engine=manifest_row.source_engine,
                        connection_id=manifest_row.source_connection_id,
                        database=manifest_row.source_database,
                        schema=manifest_row.source_schema,
                        table=manifest_row.source_table,
                    ),
                    target=TableEndpointConfig(
                        provider=ProviderType.AWS if manifest_row.target_engine == EngineType.ATHENA else ProviderType.OTHER,
                        engine=manifest_row.target_engine,
                        connection_id=manifest_row.target_connection_id,
                        database=manifest_row.target_database,
                        schema=manifest_row.target_schema,
                        table=manifest_row.target_table,
                    ),
                    comparison=ComparisonConfig(
                        unique_keys=keys,
                        partition=PartitionConfig(
                            column=keys[0] if keys else None,
                        ),
                    ),
                )
                jobs.append(job)

    return jobs


def export_json_schema(output_path: str | Path = "configs/schemas/validation_job_schema.json") -> Path:
    """Generate and export the JSON Schema from the Pydantic model."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    schema = ValidationJobConfig.model_json_schema()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)
    return out
