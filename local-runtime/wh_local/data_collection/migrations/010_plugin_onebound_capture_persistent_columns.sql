ALTER TABLE plugin_onebound_capture_batches
ADD COLUMN page_url TEXT NOT NULL DEFAULT '';

ALTER TABLE plugin_onebound_capture_batches
ADD COLUMN total_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE plugin_onebound_capture_batches
ADD COLUMN error_message TEXT NOT NULL DEFAULT '';

ALTER TABLE plugin_onebound_capture_items
ADD COLUMN source_title TEXT NOT NULL DEFAULT '';

ALTER TABLE plugin_onebound_capture_items
ADD COLUMN error_message TEXT NOT NULL DEFAULT '';
