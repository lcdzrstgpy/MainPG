from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the standalone W-H customer auth service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--database", default="", help="SQLite database path for platform accounts.")
    args = parser.parse_args()

    runtime_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(runtime_root))

    from wh_local.customer.auth_server import create_auth_app

    database_path = Path(args.database).resolve() if args.database else None
    uvicorn.run(create_auth_app(database_path), host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
