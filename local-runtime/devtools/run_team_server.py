from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the standalone MainPG team store service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8015)
    parser.add_argument("--database", default="", help="SQLite database path for team data.")
    args = parser.parse_args()

    runtime_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(runtime_root))

    from wh_local.modules.team_store.auth import AccountResolver
    from wh_local.modules.team_store.repository import TeamRepository
    from wh_local.modules.team_store.routes import create_team_app
    from wh_local.modules.team_store.service import TeamStoreService

    database_path = (
        Path(args.database).resolve()
        if args.database
        else runtime_root / "data" / "team.sqlite3"
    )
    repository = TeamRepository(database_path)
    service = TeamStoreService(repository)
    resolver = AccountResolver(
        auth_base_url=os.environ.get("TEAM_AUTH_BASE_URL", ""),
        dev_account_id=os.environ.get("TEAM_DEV_ACCOUNT_ID", ""),
    )
    uvicorn.run(
        create_team_app(service, resolver),
        host=args.host,
        port=args.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
