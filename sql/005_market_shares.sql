BEGIN;

CREATE TABLE IF NOT EXISTS market.daily_market_totals (
    market_date DATE NOT NULL,
    region_code TEXT NOT NULL REFERENCES market.regions(code),
    channel TEXT NOT NULL,
    product_code TEXT NOT NULL REFERENCES market.products(code),

    total_revenue NUMERIC(16,2) NOT NULL
        CHECK (total_revenue > 0),

    model_version TEXT NOT NULL,
    assumptions JSONB NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (
        market_date, region_code, channel, product_code
    )
);

CREATE TABLE IF NOT EXISTS market.daily_market_sales (
    market_date DATE NOT NULL,
    region_code TEXT NOT NULL,
    channel TEXT NOT NULL,
    product_code TEXT NOT NULL,

    participant_key TEXT NOT NULL,
    company_code TEXT REFERENCES market.companies(code),
    participant_role TEXT NOT NULL CHECK (
        participant_role IN ('client', 'competitor', 'other')
    ),

    revenue NUMERIC(16,2) NOT NULL CHECK (revenue >= 0),
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (
        market_date, region_code, channel,
        product_code, participant_key
    ),

    FOREIGN KEY (
        market_date, region_code, channel, product_code
    )
    REFERENCES market.daily_market_totals (
        market_date, region_code, channel, product_code
    ),

    CHECK (
        (
            participant_role = 'other'
            AND company_code IS NULL
            AND participant_key = '__other__'
        )
        OR
        (
            participant_role IN ('client', 'competitor')
            AND company_code IS NOT NULL
            AND participant_key = company_code
        )
    )
);

COMMENT ON TABLE market.daily_market_totals IS
'Синтетический объем рынка в рублях без НДС.
Модель v1: четыре суммарные плановые выручки клиентов
для того же товара, региона, канала и дня.
Не является оценкой фактического объема рынка.';

COMMENT ON TABLE market.daily_market_sales IS
'Распределение модельного рынка между клиентами,
конкурентами и прочими участниками.
Выручка клиентов берется из синтетических продаж.
Для конкурентов выручка распределяется моделью.';

CREATE OR REPLACE VIEW market.v_market_shares AS
SELECT
    s.market_date,
    s.region_code,
    r.name AS region_name,
    s.channel,
    s.product_code,
    p.name AS product_name,
    p.category,
    s.participant_key,
    s.company_code,
    CASE
        WHEN s.participant_role = 'other' THEN 'Прочий рынок'
        ELSE c.name
    END AS participant_name,
    s.participant_role,
    s.revenue,
    t.total_revenue,
    100.0 * s.revenue / t.total_revenue AS share_pct,
    t.model_version,
    t.assumptions,
    s.is_synthetic
FROM market.daily_market_sales s
JOIN market.daily_market_totals t
    USING (market_date, region_code, channel, product_code)
JOIN market.regions r ON r.code = s.region_code
JOIN market.products p ON p.code = s.product_code
LEFT JOIN market.companies c ON c.code = s.company_code;

CREATE OR REPLACE VIEW market.v_market_balance AS
SELECT
    t.market_date,
    t.region_code,
    t.channel,
    t.product_code,
    t.total_revenue,
    COALESCE(sum(s.revenue), 0) AS allocated_revenue,
    t.total_revenue - COALESCE(sum(s.revenue), 0)
        AS difference_rub
FROM market.daily_market_totals t
LEFT JOIN market.daily_market_sales s
    USING (market_date, region_code, channel, product_code)
GROUP BY
    t.market_date, t.region_code, t.channel,
    t.product_code, t.total_revenue;

COMMIT;
