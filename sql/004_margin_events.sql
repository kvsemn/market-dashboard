-- MARGIN_EVENTS_V1

WITH sales AS (
    SELECT
        s.*,
        lag(margin_gap_pp) OVER (
            PARTITION BY company_code, region_code, channel, product_code
            ORDER BY sales_date
        ) AS previous_gap
    FROM market.v_daily_sales s
),
episodes AS (
    SELECT *
    FROM sales
    WHERE margin_gap_pp <= -3
      AND previous_gap > -3
),
daily_offers AS (
    SELECT
        (p.observed_at AT TIME ZONE 'UTC')::date AS price_date,
        p.company_code,
        p.region_code,
        p.channel,
        p.product_code,
        avg(p.price_rub) AS price
    FROM market.prices p
    WHERE p.is_available
    GROUP BY 1, p.company_code, p.region_code, p.channel, p.product_code
)
INSERT INTO market.events (
    event_key, observed_at, event_type, severity,
    region_code, company_code, product_code, channel,
    title, evidence
)
SELECT
    concat(
        'margin:', s.company_code, ':', s.region_code, ':',
        s.channel, ':', s.product_code, ':', s.sales_date
    ),
    (s.sales_date + 1)::timestamp AT TIME ZONE 'UTC',
    'margin_below_plan',
    CASE WHEN s.margin_gap_pp <= -5
        THEN 'critical' ELSE 'warning' END,
    s.region_code,
    s.company_code,
    s.product_code,
    s.channel,
    'Маржа ниже плана на '
        || abs(s.margin_gap_pp)::text || ' п.п.',
    jsonb_build_object(
        'sales_date', s.sales_date,
        'actual_margin_pct', s.actual_margin_pct,
        'plan_margin_pct', s.plan_margin_pct,
        'margin_gap_pp', s.margin_gap_pp,
        'actual_revenue', s.actual_revenue,
        'actual_qty', s.actual_qty,
        'plan_qty', s.plan_qty,

        'unit_cost_change_pct', round(
            (
                (s.actual_cogs / NULLIF(s.actual_qty, 0))
                / NULLIF(s.plan_cogs / NULLIF(s.plan_qty, 0), 0)
                - 1
            ) * 100,
            2
        ),

        'client_offer_rub', round(client_offer.price, 2),
        'competitor_code', competitor.company_code,
        'competitor_name', competitor.company_name,
        'competitor_offer_rub', round(competitor.price, 2),

        'competitor_gap_pct', round(
            (
                competitor.price / NULLIF(client_offer.price, 0) - 1
            ) * 100,
            2
        ),

        'comparison_basis',
        'Средние цены предложения за тот же день, товар, регион и канал'
    )
FROM episodes s
LEFT JOIN daily_offers client_offer
    ON client_offer.price_date = s.sales_date
   AND client_offer.company_code = s.company_code
   AND client_offer.region_code = s.region_code
   AND client_offer.channel = s.channel
   AND client_offer.product_code = s.product_code
LEFT JOIN LATERAL (
    SELECT
        o.company_code,
        c.name AS company_name,
        o.price
    FROM daily_offers o
    JOIN market.companies c ON c.code = o.company_code
    WHERE c.role = 'competitor'
      AND o.price_date = s.sales_date
      AND o.region_code = s.region_code
      AND o.channel = s.channel
      AND o.product_code = s.product_code
    ORDER BY o.price, o.company_code
    LIMIT 1
) competitor ON TRUE
ON CONFLICT (event_key) DO NOTHING;

INSERT INTO market.hypotheses (
    event_id, hypothesis_code, explanation,
    supporting_evidence, verification_needed
)
SELECT
    id,
    'unit_cost_increase',
    'Снижение маржи может быть связано с ростом себестоимости '
    'единицы товара относительно плана.',
    jsonb_build_object(
        'unit_cost_change_pct', evidence->'unit_cost_change_pct',
        'margin_gap_pp', evidence->'margin_gap_pp',
        'sales_date', evidence->'sales_date'
    ),
    'Проверить закупочную стоимость и состав себестоимости, '
    'а также корректность плановых значений.'
FROM market.events
WHERE event_type = 'margin_below_plan'
  AND (evidence->>'unit_cost_change_pct')::numeric >= 5
ON CONFLICT (event_id, hypothesis_code) DO NOTHING;

INSERT INTO market.hypotheses (
    event_id, hypothesis_code, explanation,
    supporting_evidence, verification_needed
)
SELECT
    id,
    'competitive_price_pressure',
    'В тот же день конкурент предлагал сопоставимый товар '
    'минимум на 10% дешевле клиента. Возможное объяснение '
    'снижения маржи — дополнительные скидки под давлением '
    'конкуренции. Причинная связь не установлена.',
    jsonb_build_object(
        'competitor_name', evidence->'competitor_name',
        'competitor_gap_pct', evidence->'competitor_gap_pct',
        'margin_gap_pp', evidence->'margin_gap_pp',
        'sales_date', evidence->'sales_date'
    ),
    'Проверить фактические скидки, условия предложений, '
    'причины потери заказов и даты изменения коммерческих условий.'
FROM market.events
WHERE event_type = 'margin_below_plan'
  AND (evidence->>'competitor_gap_pct')::numeric <= -10
ON CONFLICT (event_id, hypothesis_code) DO NOTHING;
