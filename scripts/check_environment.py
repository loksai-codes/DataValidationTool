"""
Environment verification script.
Checks Python version, package dependencies, AWS connectivity,
and PostgreSQL service status.
"""

import os
import sys

# Add src to path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from migration_validator.config import load_config
from migration_validator.logging_config import setup_logging

logger = setup_logging("INFO")


def check_python_version() -> bool:
    print(f"[+] Python Version: {sys.version}")
    if sys.version_info < (3, 12):
        print("[-] ERROR: Python 3.12+ required.")
        return False
    print("    [PASS] Python version meets requirements.")
    return True


def check_dependencies() -> bool:
    required = [
        "streamlit", "pandas", "pyarrow", "boto3",
        "pyathena", "sqlalchemy", "psycopg", "plotly", "pydantic"
    ]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
            print(f"    [OK] {pkg}")
        except ImportError:
            missing.append(pkg)
            print(f"    [FAIL] {pkg} is NOT installed")

    if missing:
        print(f"[-] ERROR: Missing packages: {missing}")
        return False
    print("    [PASS] All core dependencies installed.")
    return True


def check_postgres(config) -> bool:
    print("\n[+] Checking PostgreSQL Connection...")
    import psycopg

    try:
        conn = psycopg.connect(
            host=config.postgres.host,
            port=config.postgres.port,
            dbname=config.postgres.database,
            user=config.postgres.user,
            password=config.postgres.raw_password,
            connect_timeout=5
        )
        with conn.cursor() as cur:
            cur.execute("SELECT version();")
            ver = cur.fetchone()[0]
            print(f"    [OK] Connected to PostgreSQL: {ver}")

            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'customers';")
            tbl_exists = cur.fetchone()[0] > 0
            if tbl_exists:
                cur.execute("SELECT COUNT(*) FROM public.customers;")
                cnt = cur.fetchone()[0]
                print(f"    [OK] Table public.customers found with {cnt:,} records.")
            else:
                print("    [WARN] Table public.customers not found in database.")
        conn.close()
        return True
    except Exception as e:
        print(f"    [FAIL] PostgreSQL connection failed: {e}")
        return False


def check_aws_s3(config) -> bool:
    print("\n[+] Checking AWS S3 & Profile...")
    import boto3

    try:
        session = boto3.Session(
            profile_name=config.aws.profile,
            region_name=config.aws.region
        )
        s3 = session.client("s3")
        res = s3.head_object(
            Bucket=config.aws.s3_source_bucket,
            Key=config.aws.s3_source_key
        )
        size_mb = res["ContentLength"] / (1024 * 1024)
        print(f"    [OK] S3 Parquet object found: s3://{config.aws.s3_source_bucket}/{config.aws.s3_source_key} ({size_mb:.2f} MB)")
        return True
    except Exception as e:
        print(f"    [FAIL] S3 access failed: {e}")
        return False


def check_athena(config) -> bool:
    print("\n[+] Checking Athena Access...")
    import boto3

    try:
        session = boto3.Session(
            profile_name=config.aws.profile,
            region_name=config.aws.region
        )
        athena = session.client("athena")
        athena.get_work_group(WorkGroup=config.aws.athena_workgroup)
        print(f"    [OK] Athena Workgroup '{config.aws.athena_workgroup}' is active.")
        return True
    except Exception as e:
        print(f"    [INFO] Athena Query Execution Notice: {e}")
        print("    [NOTE] If IAM user lacks athena:StartQueryExecution, the validator provides")
        print("           S3 Direct Parquet verification as a seamless dual-engine option.")
        return True


def main():
    print("=" * 60)
    print("DATA MIGRATION VALIDATOR - ENVIRONMENT CHECK")
    print("=" * 60)

    config = load_config()

    py_ok = check_python_version()
    dep_ok = check_dependencies()
    pg_ok = check_postgres(config)
    s3_ok = check_aws_s3(config)
    _ = check_athena(config)

    print("\n" + "=" * 60)
    if py_ok and dep_ok and pg_ok and s3_ok:
        print("RESULT: SUCCESS - Environment is ready for validation runs!")
    else:
        print("RESULT: WARNING/INCOMPLETE - Please review items above.")
    print("=" * 60)


if __name__ == "__main__":
    main()
