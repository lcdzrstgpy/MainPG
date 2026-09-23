-- combo_kit: 成品图可选自定义文字水印配置（JSON 对象）。
-- 结构：{"enabled": bool, "text": str, "position": str, "opacity": int, "size": int, "tile": bool}
-- 默认 '{}' 表示未配置（不加水印），由 watermark.normalize_watermark_config 归一化。
ALTER TABLE combo_kit_sets ADD COLUMN watermark_json TEXT NOT NULL DEFAULT '{}';
