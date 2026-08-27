ALTER TABLE plugin_onebound_capture_items
ADD COLUMN candidate_json TEXT NOT NULL DEFAULT '';

ALTER TABLE plugin_onebound_capture_items
ADD COLUMN review_status TEXT NOT NULL DEFAULT '' CHECK (review_status IN ('', 'pending', 'confirmed'));

ALTER TABLE plugin_onebound_capture_batches
ADD COLUMN sku_repull_state TEXT NOT NULL DEFAULT '';
