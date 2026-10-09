"""Describe the permanent fixed-package top-up bonus.

Run from ``local-runtime``:
  python manage_topup_promotion.py status

The bonus is not a campaign and has no enable/disable command. Historical
``topup_double`` data remains in SQLite solely to preserve old order audits.
"""
from __future__ import annotations

import argparse
import json

from wh_local.billing import topup_promotion_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Show the permanent fixed-package top-up bonus")
    parser.add_argument("command", choices=("status",))
    args = parser.parse_args()

    result = topup_promotion_status()
    print(json.dumps({"ok": True, "topup_promotion": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
