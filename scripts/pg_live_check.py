#!/usr/bin/env python3
"""Run Meemee's PostgreSQL suites against a real server, with no skips.

Usage:
    python scripts/pg_live_check.py                  # start a throwaway local server
    python scripts/pg_live_check.py --dsn postgresql://user:pw@host/db

Without --dsn it uses the free `pgserver` package (pip install pgserver), which ships
PostgreSQL binaries and needs no system install or paid service. The server's data
directory is a temp dir removed afterwards. The DSN role must be able to CREATE DATABASE,
because tests_pg/test_live_stores.py gives every test its own throwaway database.

Exit code is pytest's: 0 only when every PostgreSQL test ran and passed. A skipped live
test counts as a failure here, since the point of this script is live verification.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def start_local_server(timezone: str | None) -> tuple[str, object, Path]:
    try:
        import pgserver
    except ImportError:
        sys.exit("no --dsn given and pgserver is not installed: pip install pgserver")
    data = Path(tempfile.mkdtemp(prefix="meemee-pg-"))
    server = pgserver.get_server(data, cleanup_mode="stop")
    if timezone:
        server.psql(f"ALTER SYSTEM SET timezone = '{timezone}';")
        server.psql("SELECT pg_reload_conf();")
    return server.get_uri(), server, data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", help="existing PostgreSQL DSN; default starts a local pgserver")
    parser.add_argument("--timezone", help="server TimeZone for the local server, e.g. Asia/Kolkata or UTC")
    parser.add_argument("pytest_args", nargs="*", help="extra pytest arguments")
    args = parser.parse_args()

    server = data = None
    dsn = args.dsn
    if not dsn:
        dsn, server, data = start_local_server(args.timezone)
    env = dict(os.environ, MEEMEE_TEST_POSTGRES_DSN=dsn, MEEMEE_TEST_DATABASE_URL=dsn)
    try:
        import psycopg

        with psycopg.connect(dsn) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
            tz = conn.execute("SHOW TimeZone").fetchone()[0]
        print(f"PostgreSQL {version}, TimeZone={tz}", flush=True)
        command = [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", "tests_pg",
                   "tests/test_pg_job_cursor.py", "tests/test_pg_memory_contract.py",
                   "tests/test_pg_rate_limit.py", "tests/test_postgres_copy.py",
                   "tests/test_live_runs_e2e.py", "tests/test_rate_limit_backends.py", *args.pytest_args]
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode == 0 and "SKIPPED" in result.stdout:
            print("pg_live_check: some PostgreSQL tests were skipped; treating as failure", file=sys.stderr)
            return 1
        return result.returncode
    finally:
        if server is not None:
            server.cleanup()
        if data is not None:
            shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
