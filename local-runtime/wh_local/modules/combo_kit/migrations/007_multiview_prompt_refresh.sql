-- combo_kit: 刷新「单品多视角」套装中仍为旧内置默认的辅助词。
-- 背景：旧默认词含 "interior / package structure clearly readable"（容器/包装类商品措辞），
-- 且未约束「一张图只画一个视角」，导致同一商品的多视角参考图被生成为 2×2 拼贴 / 一图多只。
-- 只替换与旧默认逐字相同的值，用户自定义过的文案不受影响。
UPDATE combo_kit_prompts
SET image_prompts_json = REPLACE(
        REPLACE(
            REPLACE(
                image_prompts_json,
                '"this same product on a clean white background, full product visible with the interior / package structure clearly readable, balanced layout, professional studio shot"',
                '"this same product on a clean white background, one single view only, the whole product visible with a clear complete outline, balanced layout, professional studio shot, no duplicate copies of the product"'
            ),
            '"lifestyle scene: this same product placed and used in a real home environment, minimal natural props that never cover the product, soft window light, realistic adult hands allowed, product unchanged in design, color and material"',
            '"lifestyle scene: this same product placed and used in a real home environment, only one single copy of the product in the frame, minimal natural props that never cover the product, soft window light, realistic adult hands allowed, product unchanged in design, color and material"'
        ),
        '"second lifestyle scene: the same product in a different real home setting and usage moment, relaxed natural arrangement, realistic adult hands allowed, consistent soft light, product unchanged in design, color and material"',
        '"second lifestyle scene: the same product in a different real home setting and usage moment, only one single copy of the product in the frame, relaxed natural arrangement, realistic adult hands allowed, consistent soft light, product unchanged in design, color and material"'
    )
WHERE set_id IN (SELECT set_id FROM combo_kit_sets WHERE generation_mode = 'multiview');
