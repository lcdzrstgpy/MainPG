ALTER TABLE profit_activity_settings
    ADD COLUMN activity_threshold_configured INTEGER NOT NULL DEFAULT 0;

UPDATE profit_activity_settings
SET activity_threshold_configured = CASE
        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
        ELSE 1
    END,
    activity_min_net_profit = CASE
        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
        ELSE activity_min_net_profit
    END,
    activity_profit_rate_threshold = CASE
        WHEN CAST(activity_min_net_profit AS REAL) = 8.0
         AND CAST(activity_profit_rate_threshold AS REAL) = 0.2 THEN 0
        ELSE activity_profit_rate_threshold
    END;
