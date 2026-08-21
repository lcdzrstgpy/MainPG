-- Additive metadata for direct-listing style-grid results.
-- Keeping this separate means already-running legacy batches keep their original rows untouched.
CREATE TABLE IF NOT EXISTS pod_customization_style_grid_publications (
    result_id TEXT PRIMARY KEY,
    role TEXT NOT NULL DEFAULT '',
    public_url TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (result_id) REFERENCES pod_customization_style_grid_results (result_id) ON DELETE CASCADE
);
