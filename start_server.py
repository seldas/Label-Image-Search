#!/usr/bin/env python3
"""
One-click launcher for Labeling Image Query:
1. Ensures .env and persistent data directories exist.
2. Starts all Docker Compose containers (postgres, backend, worker, frontend).
3. Waits for PostgreSQL to become healthy.
4. Runs `python -m app.db.sync_db` inside the backend container to create/migrate tables & columns.
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"[start_server] $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=ROOT_DIR, check=check)


def ensure_env_and_dirs() -> None:
    env_file = ROOT_DIR / ".env"
    example_file = ROOT_DIR / ".env.example"
    if not env_file.exists():
        if example_file.exists():
            shutil.copy(example_file, env_file)
            print("[start_server] Created .env from .env.example (please verify VLLM_BASE_URL & passwords).")
        else:
            print("[start_server] Warning: .env and .env.example not found.")

    for rel in ("data/pgdata", "data/images", "data/downloads", "models"):
        (ROOT_DIR / rel).mkdir(parents=True, exist_ok=True)


def wait_for_postgres(timeout_sec: int = 60) -> None:
    print("[start_server] Waiting for PostgreSQL container to be healthy...")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        res = subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres", "pg_isready", "-q"],
            cwd=ROOT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if res.returncode == 0:
            print("[start_server] PostgreSQL is ready.")
            return
        time.sleep(2)
    raise RuntimeError("Timed out waiting for PostgreSQL container to become healthy.")


def sync_database() -> None:
    print("[start_server] Running sync_db (schema & additive column migrations)...")
    run(["docker", "compose", "exec", "-T", "backend", "python", "-m", "app.db.sync_db"])


def main() -> int:
    parser = argparse.ArgumentParser(description="One-click startup & DB sync for Labeling Image Query")
    parser.add_argument("--no-build", action="store_true", help="Skip rebuilding Docker images")
    parser.add_argument("--sync-db-only", action="store_true", help="Only run sync_db on running containers")
    args = parser.parse_args()

    ensure_env_and_dirs()

    if not args.sync_db_only:
        up_cmd = ["docker", "compose", "up", "-d"]
        if not args.no_build:
            up_cmd.append("--build")
        run(up_cmd)

    wait_for_postgres()
    sync_database()

    print("\n[start_server] All services are up and database is synced!")
    print("  - Web UI & Admin Dashboard : http://localhost:8845")
    print("  - FastAPI Swagger Docs     : http://localhost:8846/docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
