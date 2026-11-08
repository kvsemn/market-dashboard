BEGIN;

CREATE OR REPLACE VIEW market.v_latest_prices AS
SELECT
    p.observed_at,
    p.region_code,
    r.name AS region_name,
    p.company_code,
    c.name AS company_name,
    c.role,
    p.channel,
    p.product_code,
    t.name AS product_name,
    t.category,
    t.brand,
    t.package_kg,
    p.price_rub,
    p.is_synthetic
FROM market.prices p
JOIN market.regions r ON r.code = p.region_code
JOIN market.companies c ON c.code = p.company_code
JOIN market.products t ON t.code = p.product_code
WHERE p.observed_at = (
    SELECT max(period_at) FROM market.pipeline_runs
)
AND p.is_available;

CREATE OR REPLACE VIEW market.v_price_comparison AS
SELECT
    client.observed_at,
    client.region_code,
    client.region_name,
    client.channel,
    client.product_code,
    client.product_name,
    client.category,
    client.company_code AS client_code,
    client.company_name AS client_name,
    client.price_rub AS client_price_rub,
    competitor.company_code AS competitor_code,
    competitor.company_name AS competitor_name,
    competitor.price_rub AS competitor_price_rub,
    round(
        (competitor.price_rub / client.price_rub - 1) * 100,
        2
    ) AS competitor_gap_pct
FROM market.v_latest_prices client
JOIN market.v_latest_prices competitor
    ON competitor.region_code = client.region_code
   AND competitor.product_code = client.product_code
   AND competitor.channel = client.channel
WHERE client.role = 'client'
  AND competitor.role = 'competitor';

CREATE OR REPLACE VIEW market.v_event_feed AS
SELECT
    e.id,
    e.observed_at,
    e.event_type,
    e.severity,
    e.region_code,
    r.name AS region_name,
    e.company_code,
    c.name AS company_name,
    e.product_code,
    p.name AS product_name,
    e.channel,
    e.title,
    e.evidence,
    e.is_synthetic,
    COALESCE(
        (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'explanation', h.explanation,
                    'verification_needed', h.verification_needed
                )
                ORDER BY h.id
            )
            FROM market.hypotheses h
            WHERE h.event_id = e.id
        ),
        '[]'::jsonb
    ) AS hypotheses
FROM market.events e
JOIN market.regions r ON r.code = e.region_code
LEFT JOIN market.companies c ON c.code = e.company_code
LEFT JOIN market.products p ON p.code = e.product_code;

COMMIT;
