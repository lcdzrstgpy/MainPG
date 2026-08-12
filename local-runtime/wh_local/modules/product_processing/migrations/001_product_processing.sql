PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS product_processing_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL DEFAULT 'local',
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT NOT NULL DEFAULT '',
    candidate_id TEXT,
    selection_run_id TEXT,
    handoff_id TEXT UNIQUE,
    handoff_idempotency_key TEXT UNIQUE,
    skc TEXT,
    sku TEXT,
    product_name TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    cost REAL,
    declared_price REAL,
    status TEXT NOT NULL DEFAULT 'draft',
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_product_processing_drafts_workspace
    ON product_processing_drafts (workspace_id);

CREATE INDEX IF NOT EXISTS idx_product_processing_drafts_source_type
    ON product_processing_drafts (source_type);

CREATE INDEX IF NOT EXISTS idx_product_processing_drafts_candidate
    ON product_processing_drafts (candidate_id);

CREATE INDEX IF NOT EXISTS idx_product_processing_drafts_selection_run
    ON product_processing_drafts (selection_run_id);

CREATE INDEX IF NOT EXISTS idx_product_processing_drafts_skc
    ON product_processing_drafts (skc);

CREATE INDEX IF NOT EXISTS idx_product_processing_drafts_status
    ON product_processing_drafts (status);

CREATE TABLE IF NOT EXISTS product_processing_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL DEFAULT 'local',
    title TEXT NOT NULL DEFAULT '产品处理任务',
    status TEXT NOT NULL DEFAULT 'queued',
    preflight_only INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    settings_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT,
    output_file TEXT NOT NULL DEFAULT '',
    error_report_file TEXT NOT NULL DEFAULT '',
    video_manifest_file TEXT NOT NULL DEFAULT '',
    cleared_from_product_processing INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_product_processing_tasks_workspace
    ON product_processing_tasks (workspace_id);

CREATE INDEX IF NOT EXISTS idx_product_processing_tasks_status
    ON product_processing_tasks (status);

CREATE TABLE IF NOT EXISTS product_processing_task_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    product_draft_id INTEGER,
    skc TEXT NOT NULL DEFAULT '',
    spu TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id)
        REFERENCES product_processing_tasks (id)
        ON DELETE CASCADE,
    FOREIGN KEY (product_draft_id)
        REFERENCES product_processing_drafts (id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_product_processing_task_items_task
    ON product_processing_task_items (task_id);

CREATE INDEX IF NOT EXISTS idx_product_processing_task_items_status
    ON product_processing_task_items (status);

CREATE TABLE IF NOT EXISTS product_processing_daily_selection_intakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL DEFAULT 'local',
    run_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    criteria_json TEXT NOT NULL DEFAULT '{}',
    counts_json TEXT NOT NULL DEFAULT '{}',
    errors_json TEXT NOT NULL DEFAULT '[]',
    candidate_count INTEGER NOT NULL DEFAULT 0,
    created_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_product_processing_intakes_workspace
    ON product_processing_daily_selection_intakes (workspace_id);

CREATE INDEX IF NOT EXISTS idx_product_processing_intakes_run
    ON product_processing_daily_selection_intakes (run_id);

CREATE TABLE IF NOT EXISTS product_processing_prompts (
    key TEXT PRIMARY KEY,
    custom TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_processing_source_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_draft_id INTEGER NOT NULL,
    task_id INTEGER,
    kind TEXT NOT NULL DEFAULT 'source',
    url TEXT NOT NULL,
    local_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (product_draft_id, url),
    FOREIGN KEY (product_draft_id)
        REFERENCES product_processing_drafts (id)
        ON DELETE CASCADE,
    FOREIGN KEY (task_id)
        REFERENCES product_processing_tasks (id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_product_processing_source_images_draft
    ON product_processing_source_images (product_draft_id);

CREATE INDEX IF NOT EXISTS idx_product_processing_source_images_task
    ON product_processing_source_images (task_id);

CREATE TABLE IF NOT EXISTS product_processing_handoff_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    product_draft_id INTEGER NOT NULL,
    source_status TEXT NOT NULL DEFAULT 'pending',
    consumer_status TEXT NOT NULL DEFAULT 'consumed',
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (product_draft_id)
        REFERENCES product_processing_drafts (id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_product_processing_handoff_receipts_handoff
    ON product_processing_handoff_receipts (handoff_id);

CREATE INDEX IF NOT EXISTS idx_product_processing_handoff_receipts_workspace
    ON product_processing_handoff_receipts (workspace_id);

CREATE INDEX IF NOT EXISTS idx_product_processing_handoff_receipts_run
    ON product_processing_handoff_receipts (run_id);

CREATE INDEX IF NOT EXISTS idx_product_processing_handoff_receipts_candidate
    ON product_processing_handoff_receipts (candidate_id);

CREATE INDEX IF NOT EXISTS idx_product_processing_handoff_receipts_draft
    ON product_processing_handoff_receipts (product_draft_id);
