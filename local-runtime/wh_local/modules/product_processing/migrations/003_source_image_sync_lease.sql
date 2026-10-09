ALTER TABLE product_processing_source_images ADD COLUMN sync_claimed_at TEXT NOT NULL DEFAULT '';
ALTER TABLE product_processing_source_images ADD COLUMN sync_claim_token TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_product_processing_source_images_sync_lease
    ON product_processing_source_images (sync_status, sync_claimed_at);
