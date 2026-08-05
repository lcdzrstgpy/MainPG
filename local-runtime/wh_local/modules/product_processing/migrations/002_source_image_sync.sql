ALTER TABLE product_processing_source_images ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE product_processing_source_images ADD COLUMN sync_error TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_product_processing_source_images_sync_status
    ON product_processing_source_images (sync_status);
