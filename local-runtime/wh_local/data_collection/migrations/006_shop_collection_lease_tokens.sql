ALTER TABLE shop_collection_batches
    ADD COLUMN lease_token TEXT NOT NULL DEFAULT '';

ALTER TABLE shop_collection_items
    ADD COLUMN lease_token TEXT NOT NULL DEFAULT '';
