CREATE TABLE IF NOT EXISTS profit_activity_sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    site_code TEXT NOT NULL,
    display_name TEXT NOT NULL,
    first_mile_rate NUMERIC(12, 4) NOT NULL DEFAULT 0,
    first_mile_fixed NUMERIC(12, 4) NOT NULL DEFAULT 0,
    domestic_fee NUMERIC(12, 4) NOT NULL DEFAULT 0,
    shipping_subsidy NUMERIC(12, 4) NOT NULL DEFAULT 0,
    end_fee NUMERIC(12, 4) NOT NULL DEFAULT 0,
    refund_rate NUMERIC(10, 6) NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (workspace_id, site_code)
);

CREATE INDEX IF NOT EXISTS idx_profit_activity_sites_workspace
ON profit_activity_sites (workspace_id, created_at, id);
