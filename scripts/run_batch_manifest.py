"""CLI runner for executing multi-job CSV manifests with bounded concurrency."""
from __future__ import annotations

import argparse
import concurrent.futures
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows terminal
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add src to pythonpath
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from data_validator.config.loader import load_batch_manifest
from data_validator.config.models import ValidationJobConfig
from data_validator.config.validators import preflight_validate_job
from data_validator.execution.coordinator import ValidationCoordinator
from data_validator.persistence.sqlite_repository import SQLiteRunRepository
from data_validator.security.secrets import default_secret_store


def run_single_job(job_cfg: ValidationJobConfig, repo_path: str) -> dict:
    repo = SQLiteRunRepository(repo_path)
    coord = ValidationCoordinator(config=job_cfg, repository=repo, secret_store=default_secret_store)
    rec = coord.run()
    total_disc = rec.missing_count + rec.extra_count + rec.changed_count + rec.duplicate_key_count + rec.null_key_count
    return {
        "job_name": rec.job_name,
        "run_id": rec.run_id,
        "status": rec.status.value,
        "rows_compared": rec.rows_compared,
        "discrepancies": total_disc,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch Multi-Job Validation Runner")
    parser.add_argument("manifest", help="Path to CSV manifest file")
    parser.add_argument("--max-workers", type=int, default=2, help="Max concurrent jobs (default: 2)")
    parser.add_argument("--repo-path", default="data/validation_metadata.db", help="SQLite metadata DB path")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"❌ Manifest file not found: {manifest_path}")
        sys.exit(1)

    print(f"📋 Loading batch manifest from: {manifest_path}")
    jobs = load_batch_manifest(manifest_path)
    print(f"Loaded {len(jobs)} job definition(s).")

    # Preflight validate all jobs before launch
    print("\n🔍 Pre-validating all jobs before launch...")
    validation_failures = 0
    for idx, job in enumerate(jobs, start=1):
        try:
            preflight_validate_job(job, secret_store=default_secret_store)
            print(f"  [{idx}/{len(jobs)}] ✅ Job '{job.job_name}' passed preflight.")
        except Exception as e:
            print(f"  [{idx}/{len(jobs)}] ❌ Job '{job.job_name}' failed preflight: {e}")
            validation_failures += 1

    if validation_failures > 0:
        print(f"\n❌ Aborting batch execution: {validation_failures} job(s) failed preflight validation.")
        sys.exit(1)

    print(f"\n🚀 Launching {len(jobs)} job(s) with concurrency limit {args.max_workers}...")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        future_map = {executor.submit(run_single_job, job, args.repo_path): job.job_name for job in jobs}
        for future in concurrent.futures.as_completed(future_map):
            job_name = future_map[future]
            try:
                res = future.result()
                results.append(res)
                print(f"  🏁 Finished '{res['job_name']}': {res['status']} ({res['rows_compared']:,} rows, {res['discrepancies']:,} discrepancies)")
            except Exception as e:
                print(f"  ❌ Job '{job_name}' raised unhandled exception: {e}")

    print("\n" + "=" * 60)
    print("Batch Run Summary:")
    print("=" * 60)
    for r in results:
        print(f" - {r['job_name']} (Run ID: {r['run_id']}): {r['status']}")


if __name__ == "__main__":
    main()
