PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS daily_selection_runs (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    criteria_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    candidate_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_daily_selection_runs_workspace_created
    ON daily_selection_runs (workspace_id, created_at DESC, run_id);

CREATE TABLE IF NOT EXISTS daily_selection_candidates (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    offer_id TEXT NOT NULL,
    source_platform TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_title TEXT NOT NULL,
    main_image_url TEXT,
    price_cny TEXT,
    selection_score TEXT NOT NULL,
    status TEXT NOT NULL,
    raw_candidate_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, run_id, candidate_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES daily_selection_runs (workspace_id, run_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_daily_selection_candidates_workspace_run
    ON daily_selection_candidates (workspace_id, run_id, candidate_id);

CREATE TABLE IF NOT EXISTS daily_selection_feedback (
    feedback_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (workspace_id, run_id, candidate_id)
        REFERENCES daily_selection_candidates (workspace_id, run_id, candidate_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_daily_selection_feedback_workspace_run
    ON daily_selection_feedback (workspace_id, run_id, candidate_id, created_at);

CREATE TABLE IF NOT EXISTS daily_selection_provider_budgets (
    workspace_id TEXT NOT NULL,
    provider_fingerprint TEXT NOT NULL,
    budget_date TEXT NOT NULL,
    api_calls_limit INTEGER NOT NULL,
    api_calls_used INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, provider_fingerprint, budget_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_selection_provider_budgets_workspace_date
    ON daily_selection_provider_budgets (workspace_id, budget_date, provider_fingerprint);

CREATE TABLE IF NOT EXISTS daily_selection_handoffs (
    handoff_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, run_id, candidate_id),
    FOREIGN KEY (workspace_id, run_id, candidate_id)
        REFERENCES daily_selection_candidates (workspace_id, run_id, candidate_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_daily_selection_handoffs_workspace_run
    ON daily_selection_handoffs (workspace_id, run_id, status, created_at);
