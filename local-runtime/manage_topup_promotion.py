"""Developer command for the manually operated double-points campaign.

Run from ``local-runtime``:
  python manage_topup_promotion.py status
  python manage_topup_promotion.py enable
  python manage_topup_promotion.py disable

Use ``--database`` only for a deliberately selected local/test database.
The command never exposes payment credentials and does not require a service
restart because it updates the shared SQLite configuration row.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from wh_local.billing import set_topup_promotion_active, topup_promotion_status
from wh_local.config import default_config
from wh_local.db import init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the top-up double-points campaign")
    parser.add_argument("command", choices=("status", "enable", "disable"))
    parser.add_argument("--database", type=Path, help="Explicit SQLite database path")
    parser.add_argument("--operator", default="developer-cli", help="Audit label only")
    args = parser.parse_args()

    database_path = args.database or default_config().database_path
    init_db(database_path)
    if args.command == "status":
        result = topup_promotion_status(database_path)
    else:
        result = set_topup_promotion_active(
            database_path,
            active=args.command == "enable",
            updated_by=args.operator,
        )
    print(json.dumps({"ok": True, "topup_promotion": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
