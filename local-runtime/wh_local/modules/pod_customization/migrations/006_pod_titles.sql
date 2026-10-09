-- Listing-title state is independent from image state so transient text failures
-- never discard already generated or published images.
CREATE TABLE IF NOT EXISTS pod_customization_style_titles (
    batch_id TEXT NOT NULL,
    style_index INTEGER NOT NULL,
    style_task_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('queued', 'generating', 'completed', 'failed')),
    title TEXT NOT NULL DEFAULT '',
    normalized_title TEXT,
    visual_tags_json TEXT NOT NULL DEFAULT '{}',
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (batch_id, style_index),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_customization_style_titles_status
    ON pod_customization_style_titles (batch_id, status, style_index);

CREATE UNIQUE INDEX IF NOT EXISTS uq_pod_customization_style_titles_normalized
    ON pod_customization_style_titles (batch_id, normalized_title)
    WHERE normalized_title IS NOT NULL AND normalized_title <> '';

CREATE TABLE IF NOT EXISTS pod_customization_direct_listing_titles (
    trial_id TEXT PRIMARY KEY,
    style_task_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    title TEXT NOT NULL DEFAULT '',
    normalized_title TEXT,
    visual_tags_json TEXT NOT NULL DEFAULT '{}',
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (trial_id) REFERENCES pod_customization_direct_listing_trials (trial_id) ON DELETE CASCADE
);
