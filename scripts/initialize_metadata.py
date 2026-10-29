"""
CLI utility to initialize or update the validator_meta database schema.
Executes versioned migrations safely and idempotently.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from migration_validator.config import load_config
from migration_validator.logging_config import setup_logging
from migration_validator.repositories.metadata_repository import MetadataRepository

logger = setup_logging("INFO")


def main():
    print("=" * 60)
    print("INITIALIZING METADATA REPOSITORY (validator_meta)")
    print("=" * 60)

    config = load_config()
    repo = MetadataRepository(config.postgres)

    try:
        repo.initialize_schema()
        print("[SUCCESS] Dedicated schema 'validator_meta' initialized successfully.")
        print("          All tracking tables and indices are active.")
    except Exception as e:
        print(f"[ERROR] Failed to initialize metadata repository: {e}")
        sys.exit(1)
    finally:
        repo.close()

    print("=" * 60)


if __name__ == "__main__":
    main()
