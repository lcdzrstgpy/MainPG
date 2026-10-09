CREATE TABLE IF NOT EXISTS product_dimension_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id VARCHAR(255) NOT NULL DEFAULT '__global__',
    category_key TEXT NOT NULL,
    package_profile VARCHAR(64) NOT NULL,
    known_len_min REAL, known_len_max REAL, known_len_default REAL,
    known_wid_min REAL, known_wid_max REAL, known_wid_default REAL,
    known_hei_min REAL, known_hei_max REAL, known_hei_default REAL,
    known_wgt_min REAL, known_wgt_max REAL, known_wgt_default REAL,
    stat_len_p10 REAL, stat_len_p50 REAL, stat_len_p90 REAL,
    stat_wid_p10 REAL, stat_wid_p50 REAL, stat_wid_p90 REAL,
    stat_hei_p10 REAL, stat_hei_p50 REAL, stat_hei_p90 REAL,
    stat_wgt_p10 REAL, stat_wgt_p50 REAL, stat_wgt_p90 REAL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    len_sample_count INTEGER NOT NULL DEFAULT 0,
    wid_sample_count INTEGER NOT NULL DEFAULT 0,
    hei_sample_count INTEGER NOT NULL DEFAULT 0,
    wgt_sample_count INTEGER NOT NULL DEFAULT 0,
    source_confirmed_n INTEGER NOT NULL DEFAULT 0,
    manual_confirmed_n INTEGER NOT NULL DEFAULT 0,
    quarantined_axis_count INTEGER NOT NULL DEFAULT 0,
    accuracy_json TEXT NOT NULL DEFAULT '{}',
    created_at VARCHAR(64) NOT NULL DEFAULT '',
    updated_at VARCHAR(64) NOT NULL DEFAULT '',
    CONSTRAINT uq_dimension_template_identity UNIQUE (workspace_id, category_key, package_profile)
);

CREATE INDEX IF NOT EXISTS idx_dimension_templates_profile
    ON product_dimension_templates (workspace_id, package_profile);

CREATE TABLE IF NOT EXISTS product_dimension_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id VARCHAR(255) NOT NULL,
    observation_key VARCHAR(255) NOT NULL,
    category_key TEXT NOT NULL,
    package_profile VARCHAR(64) NOT NULL,
    source_kind VARCHAR(32) NOT NULL,
    task_id INTEGER NOT NULL DEFAULT 0,
    product_draft_id INTEGER NOT NULL DEFAULT 0,
    variant_key TEXT NOT NULL DEFAULT '',
    length_cm REAL, width_cm REAL, height_cm REAL, weight_g REAL,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    quality_json TEXT NOT NULL DEFAULT '{}',
    raw_estimate_json TEXT NOT NULL DEFAULT '{}',
    resolved_estimate_json TEXT NOT NULL DEFAULT '{}',
    error_metrics_json TEXT NOT NULL DEFAULT '{}',
    created_at VARCHAR(64) NOT NULL DEFAULT '',
    CONSTRAINT uq_dimension_observation_identity UNIQUE (workspace_id, observation_key)
);

CREATE INDEX IF NOT EXISTS idx_dimension_observations_template
    ON product_dimension_observations (workspace_id, category_key, package_profile);

CREATE TABLE IF NOT EXISTS product_dimension_template_refresh_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id VARCHAR(255) NOT NULL,
    category_key TEXT NOT NULL,
    package_profile VARCHAR(64) NOT NULL,
    pending_changes INTEGER NOT NULL DEFAULT 1,
    not_before_epoch REAL NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at VARCHAR(64) NOT NULL DEFAULT '',
    CONSTRAINT uq_dimension_template_refresh_identity
        UNIQUE (workspace_id, category_key, package_profile)
);

CREATE INDEX IF NOT EXISTS idx_dimension_template_refresh_due
    ON product_dimension_template_refresh_queue (not_before_epoch);
