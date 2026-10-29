-- 起订量不再参与成本计算：一条货源按一件核算（到手 = 单价 + 国内运费）。
-- 旧数据里按 MOQ 摊销过运费，这里重算一次，保持页面与利润口径一致。
UPDATE product_sourcing_sources SET moq = 1 WHERE moq <> 1;

UPDATE product_sourcing_sources
SET landed_cost_cny = printf(
        '%.4f',
        CAST(price_cny AS REAL) + CAST(COALESCE(NULLIF(domestic_freight_cny, ''), '0') AS REAL)
    ),
    updated_at = datetime('now')
WHERE price_cny IS NOT NULL
  AND price_cny <> ''
  AND ABS(
        CAST(COALESCE(NULLIF(landed_cost_cny, ''), '0') AS REAL)
        - (CAST(price_cny AS REAL) + CAST(COALESCE(NULLIF(domestic_freight_cny, ''), '0') AS REAL))
      ) > 0.00005;
