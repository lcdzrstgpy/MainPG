ALTER TABLE profit_activity_settings ADD COLUMN pe_first_mile_rate NUMERIC(12, 4) NOT NULL DEFAULT 80;
ALTER TABLE profit_activity_settings ADD COLUMN pe_first_mile_fixed NUMERIC(12, 4) NOT NULL DEFAULT 0;
ALTER TABLE profit_activity_settings ADD COLUMN pe_domestic_fee NUMERIC(12, 4) NOT NULL DEFAULT 2.5;
ALTER TABLE profit_activity_settings ADD COLUMN pe_shipping_subsidy NUMERIC(12, 4) NOT NULL DEFAULT 21;
ALTER TABLE profit_activity_settings ADD COLUMN pe_refund_rate NUMERIC(10, 6) NOT NULL DEFAULT 0.05;

UPDATE profit_activity_settings
SET co_first_mile_rate = 70
WHERE CAST(co_first_mile_rate AS REAL) = 80;
