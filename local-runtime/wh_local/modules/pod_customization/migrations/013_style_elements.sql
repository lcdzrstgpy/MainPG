-- Per-style element assignment (v4 batch diversity). Elements are assigned at
-- batch creation with the batch_id as the random seed, so retries within the
-- same batch replay the same assignment while different batches differ.
-- elements_json shape: {"primary": "...", "co": "...", "accents": ["...", "..."]}
CREATE TABLE IF NOT EXISTS pod_customization_style_elements (
    batch_id TEXT NOT NULL,
    style_index INTEGER NOT NULL CHECK (style_index >= 1),
    elements_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (batch_id, style_index),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pod_style_elements_batch
    ON pod_customization_style_elements (batch_id, style_index);
