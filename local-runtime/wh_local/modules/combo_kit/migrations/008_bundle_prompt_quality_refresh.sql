-- combo_kit: 刷新「套装(bundle)」里仍为旧内置默认的辅助词。
-- 背景：旧默认场景词只有「放在真实台面上 + 少量道具」，没有构图与道具数量约束，
-- 与商品处理四宫格的实际产出差距明显（主体居中、背景单调、道具过少、商品被裁切）。
-- 新默认词补上：黄金分割构图（x≈62% / 38%）、至少 4 件道具、不得复用参考图背景、
-- 白底图居中并占 78%-85%、手部仅在解剖正确时允许。
-- 只替换与旧默认逐字相同的值，用户自定义过的文案不受影响。
UPDATE combo_kit_prompts
SET image_prompts_json = REPLACE(
        REPLACE(
            REPLACE(
                image_prompts_json,
                '"lifestyle scene: the complete bundled set placed on a real home surface in a modern American living room or game-night table, minimal natural props (glass, snack, small plant, linen) that never cover the products, unified soft window light, every member product clearly visible"',
                '"lifestyle scene: the complete bundled set on a real home surface beside a window (warm wooden dining table or living-room sideboard), placed on the RIGHT golden line at x≈62% - never dead centre. Surround it with at least four source-compatible props (a mug, a folded linen napkin, a small potted plant, a wooden tray) that sit behind or beside the set and never cover it. Build this set fresh: never reuse a backdrop, surface or prop from the reference photos. Unified soft window light, no hands in this frame, one small kraft tag with a single English word allowed, every member product clearly visible"'
            ),
            '"second lifestyle scene: the complete bundled set used in a different real home setting (sunlit dining table or cozy reading corner), relaxed natural arrangement, realistic adult hands allowed (Caucasian / Western European, light-to-medium skin tone), consistent soft light, every member product clearly recognizable"',
            '"second lifestyle scene: the complete bundled set used in a different real home setting (sunlit kitchen island or cosy reading corner) at a different time of day, placed on the LEFT golden line at x≈38% with a tighter crop. At least four different props (an open book, a ceramic bowl, a folded throw, a floor plant) that never cover the products; invent a new set, never a crop of the first scene. Realistic adult hands allowed only if anatomically correct (Caucasian / Western European, light-to-medium skin tone, no face), consistent soft light, every member product clearly recognizable"'
        ),
        '"complete bundled set on a clean white background, full product visible, balanced layout, professional studio shot"',
        '"complete bundled set on a pure white seamless background (#FFFFFF to #FAFAFA), centred and filling 78%-85% of the frame with even clear margins, no props, no room, no horizon, no text, professional studio lighting with a soft contact shadow"'
    )
WHERE set_id IN (SELECT set_id FROM combo_kit_sets WHERE generation_mode = 'bundle');
