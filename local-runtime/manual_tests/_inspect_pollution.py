# -*- coding: utf-8 -*-
"""Temporary: inspect drafts to identify my test pollution (demo drafts)."""
import sqlite3
from pathlib import Path

DB = Path(r"e:\MainPG\MainPG\local-runtime\outputs\wh-local\workbench.sqlite3")
con = sqlite3.connect(str(DB))
cur = con.cursor()

print("by workspace/source_type/status:")
for r in cur.execute(
    "SELECT workspace_id, source_type, status, COUNT(*) FROM product_processing_drafts "
    "GROUP BY workspace_id, source_type, status ORDER BY workspace_id, source_type"
).fetchall():
    print("  ", r)

print("\ndemo-ish drafts (image_url like example.invalid or source_type manual):")
for r in cur.execute(
    "SELECT id, workspace_id, source_type, title, image_url FROM product_processing_drafts "
    "WHERE image_url LIKE '%example.invalid%' OR source_type IN ('manual','demo') ORDER BY id"
).fetchall():
    print("  id=%s ws=%s src=%s | %s | %s" % (r[0], r[1], r[2], (r[3] or '')[:28], (r[4] or '')[:45]))
con.close()
