PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS profit_activity_settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    revision INTEGER NOT NULL DEFAULT 0,
    save_root TEXT NOT NULL DEFAULT '',
    domestic_fee NUMERIC(12, 4) NOT NULL DEFAULT 2.5,
    shipping_subsidy NUMERIC(12, 4) NOT NULL DEFAULT 21,
    refund_rate NUMERIC(10, 6) NOT NULL DEFAULT 0.05,
    us_first_mile_rate NUMERIC(12, 4) NOT NULL DEFAULT 72,
    us_first_mile_fixed NUMERIC(12, 4) NOT NULL DEFAULT 5,
    co_first_mile_rate NUMERIC(12, 4) NOT NULL DEFAULT 80,
    co_first_mile_fixed NUMERIC(12, 4) NOT NULL DEFAULT 0,
    ec_domestic_fee NUMERIC(12, 4) NOT NULL DEFAULT 2.5,
    ec_shipping_subsidy NUMERIC(12, 4) NOT NULL DEFAULT 15,
    ec_shipping_subsidy_price_limit NUMERIC(12, 4) NOT NULL DEFAULT 120,
    ec_first_mile_rate NUMERIC(12, 4) NOT NULL DEFAULT 108,
    ec_first_mile_fixed NUMERIC(12, 4) NOT NULL DEFAULT 0,
    ec_end_fee NUMERIC(12, 4) NOT NULL DEFAULT 27,
    ec_refund_rate NUMERIC(10, 6) NOT NULL DEFAULT 0.05,
    activity_min_net_profit NUMERIC(12, 4) NOT NULL DEFAULT 8,
    activity_profit_rate_threshold NUMERIC(10, 6) NOT NULL DEFAULT 0.20,
    rule_version INTEGER NOT NULL DEFAULT 2,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (workspace_id)
);

CREATE TABLE IF NOT EXISTS profit_activity_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    site_code TEXT NOT NULL,
    skc TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'shared',
    created_by TEXT NOT NULL DEFAULT '',
    created_by_username TEXT NOT NULL DEFAULT 'local',
    product_id TEXT NOT NULL DEFAULT '',
    product_version TEXT NOT NULL DEFAULT '',
    main_image_asset_id TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    source_image_path TEXT NOT NULL DEFAULT '',
    source_groups_json TEXT NOT NULL DEFAULT '[]',
    source_url TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    selling_price NUMERIC(12, 4) NOT NULL,
    cost_price NUMERIC(12, 4) NOT NULL,
    weight_kg NUMERIC(12, 4) NOT NULL,
    domestic_fee NUMERIC(12, 4) NOT NULL,
    shipping_subsidy NUMERIC(12, 4) NOT NULL,
    refund_rate NUMERIC(10, 6) NOT NULL DEFAULT 0,
    shipping_cost NUMERIC(12, 4) NOT NULL,
    end_fee NUMERIC(12, 4) NOT NULL,
    total_cost NUMERIC(12, 4) NOT NULL,
    gross_profit NUMERIC(12, 4) NOT NULL,
    net_profit NUMERIC(12, 4) NOT NULL,
    profit_rate NUMERIC(10, 6) NOT NULL,
    calculation_hash TEXT NOT NULL,
    settings_revision INTEGER NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (workspace_id, site_code, skc)
);

CREATE INDEX IF NOT EXISTS idx_profit_activity_records_workspace_site
    ON profit_activity_records (workspace_id, site_code);

CREATE INDEX IF NOT EXISTS idx_profit_activity_records_workspace_created
    ON profit_activity_records (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_profit_activity_records_created_by
    ON profit_activity_records (workspace_id, created_by);

CREATE TABLE IF NOT EXISTS profit_activity_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    site_code TEXT,
    rule_version INTEGER NOT NULL,
    minimum_net_profit NUMERIC(12, 4) NOT NULL,
    minimum_profit_rate NUMERIC(10, 6) NOT NULL,
    retained_count INTEGER NOT NULL,
    excluded_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_profit_activity_runs_workspace_created
    ON profit_activity_runs (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_profit_activity_runs_workspace_site
    ON profit_activity_runs (workspace_id, site_code);

CREATE TABLE IF NOT EXISTS profit_activity_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    run_id INTEGER NOT NULL,
    record_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    UNIQUE (run_id, record_id),
    FOREIGN KEY (run_id)
        REFERENCES profit_activity_runs (id)
        ON DELETE CASCADE,
    FOREIGN KEY (record_id)
        REFERENCES profit_activity_records (id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_profit_activity_decisions_workspace_run
    ON profit_activity_decisions (workspace_id, run_id);

CREATE TABLE IF NOT EXISTS profit_activity_import_sessions (
    import_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    original_filename TEXT NOT NULL,
    site TEXT NOT NULL,
    rows_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_profit_activity_import_sessions_workspace_created
    ON profit_activity_import_sessions (workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS profit_activity_import_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    import_id TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_profit_activity_import_tasks_workspace_import
    ON profit_activity_import_tasks (workspace_id, import_id);

CREATE TABLE IF NOT EXISTS profit_activity_filter_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_profit_activity_filter_tasks_workspace_created
    ON profit_activity_filter_tasks (workspace_id, created_at DESC);
