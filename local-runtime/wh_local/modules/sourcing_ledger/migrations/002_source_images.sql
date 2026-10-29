-- 货源图片：一条货源可挂多张图（主图之外的自定义图片），支持用户增删与放大查看。
CREATE TABLE IF NOT EXISTS product_sourcing_source_images (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    product_key TEXT NOT NULL,
    source_id TEXT NOT NULL,
    image_url TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(workspace_id, source_id, image_url)
);

CREATE INDEX IF NOT EXISTS idx_sourcing_source_images_source
    ON product_sourcing_source_images(workspace_id, source_id, position);
