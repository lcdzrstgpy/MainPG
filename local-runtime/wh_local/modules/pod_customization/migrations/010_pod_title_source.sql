-- Distinguishes AI-generated listing titles from user-entered manual titles.
-- Manual titles bypass AI copy validation, do not consume billing, and are
-- written verbatim into the Dianxiaomi export.
ALTER TABLE pod_customization_style_titles
    ADD COLUMN source TEXT NOT NULL DEFAULT 'ai'
    CHECK (source IN ('ai', 'manual'));