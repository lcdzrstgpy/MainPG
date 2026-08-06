-- Temu renders up to 50 SKC groups per batch page, but a group can have more
-- than one SKU.  Rebuild the chunk table so item_count measures SKU rows while
-- the service enforces the 50-SKC page boundary.
CREATE TABLE price_verification_quote_capture_chunks_v2 (
    chunk_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    page_url TEXT NOT NULL DEFAULT '',
    item_count INTEGER NOT NULL CHECK (item_count BETWEEN 1 AND 5000),
    capture_json TEXT NOT NULL,
    items_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, batch_id, content_sha256),
    FOREIGN KEY (batch_id) REFERENCES price_verification_quote_capture_batches(batch_id)
        ON DELETE CASCADE
);

INSERT INTO price_verification_quote_capture_chunks_v2 (
    chunk_id, workspace_id, batch_id, content_sha256, page_url, item_count,
    capture_json, items_json, captured_at, created_at
)
SELECT
    chunk_id, workspace_id, batch_id, content_sha256, page_url, item_count,
    capture_json, items_json, captured_at, created_at
FROM price_verification_quote_capture_chunks;

DROP TABLE price_verification_quote_capture_chunks;

ALTER TABLE price_verification_quote_capture_chunks_v2
    RENAME TO price_verification_quote_capture_chunks;

CREATE INDEX idx_price_verification_quote_capture_chunks_batch
    ON price_verification_quote_capture_chunks(workspace_id, batch_id, created_at ASC);
