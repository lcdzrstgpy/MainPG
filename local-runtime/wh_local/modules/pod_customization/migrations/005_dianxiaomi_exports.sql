-- Immutable listing snapshots and an explicit copy seam for POD Dianxiaomi exports.
-- Copy is supplied by an upstream title chain or a caller; this module never invents it.
CREATE TABLE IF NOT EXISTS pod_customization_style_copy (
    batch_id TEXT NOT NULL,
    style_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    english_title TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (batch_id, style_index),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_customization_style_copy_batch
    ON pod_customization_style_copy (batch_id, style_index);

ALTER TABLE pod_customization_batches
    ADD COLUMN listing_fields_json TEXT NOT NULL DEFAULT 'null';
