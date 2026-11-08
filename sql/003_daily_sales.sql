BEGIN;

CREATE TABLE IF NOT EXISTS market.daily_sales (
    sales_date DATE NOT NULL,
    company_code TEXT NOT NULL,
    region_code TEXT NOT NULL,
    channel TEXT NOT NULL,
    product_code TEXT NOT NULL REFERENCES market.products(code),

    actual_qty INTEGER NOT NULL CHECK (actual_qty >= 0),
    actual_revenue NUMERIC(16,2) NOT NULL
        CHECK (actual_revenue >= 0),
    actual_cogs NUMERIC(16,2) NOT NULL
        CHECK (actual_cogs >= 0),

    plan_qty INTEGER NOT NULL CHECK (plan_qty >= 0),
    plan_revenue NUMERIC(16,2) NOT NULL
        CHECK (plan_revenue >= 0),
    plan_cogs NUMERIC(16,2) NOT NULL
        CHECK (plan_cogs >= 0),

    source_prices_until TIMESTAMPTZ NOT NULL,
    generator_version TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (
        sales_date, company_code, region_code, channel, product_code
    ),

    FOREIGN KEY (company_code, region_code, channel)
        REFERENCES market.presence(company_code, region_code, channel),

    CHECK (
        actual_qty > 0
        OR (actual_revenue = 0 AND actual_cogs = 0)
    ),
    CHECK (
        plan_qty > 0
        OR (plan_revenue = 0 AND plan_cogs = 0)
    )
);

COMMENT ON TABLE market.daily_sales IS
'Продажи за завершенный день UTC.
Количество — упаковки товара. Суммы — рубли без НДС.
COGS — себестоимость проданного товара, не все расходы компании.
План фиксируется генератором отдельно от факта.';

CREATE INDEX IF NOT EXISTS daily_sales_filter_idx
ON market.daily_sales (
    region_code, company_code, product_code, sales_date DESC
);

CREATE OR REPLACE VIEW market.v_daily_sales AS
WITH calculated AS (
    SELECT
        s.*,
        s.actual_revenue - s.actual_cogs AS actual_gross_profit,
        s.plan_revenue - s.plan_cogs AS plan_gross_profit,

        100.0 * (s.actual_revenue - s.actual_cogs)
            / NULLIF(s.actual_revenue, 0) AS actual_margin_raw,

        100.0 * (s.plan_revenue - s.plan_cogs)
            / NULLIF(s.plan_revenue, 0) AS plan_margin_raw

    FROM market.daily_sales s
)
SELECT
    s.sales_date,
    s.company_code,
    c.name AS company_name,
    c.role,
    s.region_code,
    r.name AS region_name,
    s.channel,
    s.product_code,
    p.name AS product_name,
    p.category,

    s.actual_qty,
    s.plan_qty,
    s.actual_revenue,
    s.plan_revenue,
    s.actual_cogs,
    s.plan_cogs,
    s.actual_gross_profit,
    s.plan_gross_profit,

    round(s.actual_margin_raw, 2) AS actual_margin_pct,
    round(s.plan_margin_raw, 2) AS plan_margin_pct,

    round(
        s.actual_margin_raw - s.plan_margin_raw, 2
    ) AS margin_gap_pp,

    s.actual_revenue - s.plan_revenue AS revenue_gap_rub,

    s.actual_gross_profit - s.plan_gross_profit
        AS gross_profit_gap_rub,

    round(
        s.actual_revenue / NULLIF(s.actual_qty, 0), 2
    ) AS actual_average_price_rub,

    s.is_synthetic,
    s.source_prices_until,
    s.generated_at

FROM calculated s
JOIN market.companies c ON c.code = s.company_code
JOIN market.regions r ON r.code = s.region_code
JOIN market.products p ON p.code = s.product_code;

COMMIT;
