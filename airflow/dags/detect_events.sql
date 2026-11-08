SET TIME ZONE 'UTC';

SELECT pg_advisory_xact_lock(710011);

-- Новые конкуренты. Первоначальное заполнение рынка
-- не считаем появлением новых игроков.
INSERT INTO market.events (
    event_key, observed_at, event_type, severity,
    region_code, company_code, channel, title, evidence
)
SELECT
    concat('new:', p.company_code, ':', p.region_code, ':', p.channel),
    p.first_seen_at,
    'new_competitor',
    'warning',
    p.region_code,
    p.company_code,
    p.channel,
    'Новый конкурент: ' || c.name,
    jsonb_build_object(
        'first_seen_at', p.first_seen_at,
        'basis', 'Первое присутствие в синтетических наблюдениях'
    )
FROM market.presence p
JOIN market.companies c ON c.code = p.company_code
WHERE c.role = 'competitor'
  AND p.first_seen_at > (
      SELECT min(period_at) FROM market.pipeline_runs
  )
  AND EXISTS (
      SELECT 1 FROM market.prices x
      WHERE x.company_code = p.company_code
        AND x.region_code = p.region_code
        AND x.channel = p.channel
  )
ON CONFLICT (event_key) DO NOTHING;

-- Сравниваем последовательные наблюдения.
-- За последние сутки ищем изменения от 5%.
WITH changes AS (
    SELECT
        p.*,
        lag(price_rub) OVER w AS previous_price,
        lag(observed_at) OVER w AS previous_at
    FROM market.prices p
    JOIN market.companies c ON c.code = p.company_code
    WHERE c.role = 'competitor'
    WINDOW w AS (
        PARTITION BY company_code, region_code, channel, product_code
        ORDER BY observed_at
    )
),
significant AS (
    SELECT *,
        round(
            (price_rub / previous_price - 1) * 100, 2
        ) AS change_pct
    FROM changes
    WHERE previous_price IS NOT NULL
      AND observed_at >= (
          SELECT max(period_at) - interval '24 hours'
          FROM market.pipeline_runs
      )
      AND abs(price_rub / previous_price - 1) >= 0.05
)
INSERT INTO market.events (
    event_key, observed_at, event_type, severity,
    region_code, company_code, product_code, channel,
    title, evidence
)
SELECT
    concat(
        'price:', company_code, ':', region_code, ':',
        channel, ':', product_code, ':',
        extract(epoch FROM observed_at)::bigint
    ),
    observed_at,
    CASE WHEN change_pct < 0
        THEN 'price_decrease' ELSE 'price_increase' END,
    CASE WHEN abs(change_pct) >= 10
        THEN 'critical' ELSE 'warning' END,
    region_code,
    company_code,
    product_code,
    channel,
    CASE WHEN change_pct < 0
        THEN 'Снижение цены на '
        ELSE 'Повышение цены на '
    END || abs(change_pct)::text || '%',
    jsonb_build_object(
        'previous_price_rub', previous_price,
        'current_price_rub', price_rub,
        'change_pct', change_pct,
        'previous_observed_at', previous_at,
        'price_basis', 'RUB/package, excluding tax and delivery'
    )
FROM significant
ON CONFLICT (event_key) DO NOTHING;

-- Это возможное объяснение, не установленная причина.
INSERT INTO market.hypotheses (
    event_id, hypothesis_code, explanation,
    supporting_evidence, verification_needed
)
SELECT
    id,
    'temporary_promotion',
    'Возможно временное снижение цены в рамках акции. '
    'Одного изменения цены недостаточно для подтверждения.',
    jsonb_build_object(
        'observed_change_pct', evidence->'change_pct',
        'causal_evidence_available', false
    ),
    'Проверить условия акции, срок действия, размер партии '
    'и последующий возврат цены к прежнему уровню.'
FROM market.events
WHERE event_type = 'price_decrease'
ON CONFLICT (event_id, hypothesis_code) DO NOTHING;


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

-- MARKET_SIGNALS_V1
-- Daily client share; only adjacent completed UTC days and the same model version.
WITH history AS (
    SELECT s.*,
        lag(s.share_pct) OVER w AS previous_share,
        lag(s.market_date) OVER w AS previous_date,
        lag(s.model_version) OVER w AS previous_model,
        lag(b.difference_rub) OVER w AS previous_balance,
        b.difference_rub AS current_balance
    FROM market.v_market_shares s
    JOIN market.v_market_balance b
        USING (market_date,region_code,channel,product_code)
    JOIN market.signal_rules rule ON rule.event_type='market_share_change'
    WHERE s.participant_role='client' AND rule.enabled
      AND s.market_date >= (rule.enabled_from AT TIME ZONE 'UTC')::date - 2
      AND s.market_date < (
          SELECT (max(period_at) AT TIME ZONE 'UTC')::date FROM market.pipeline_runs
      )
    WINDOW w AS (PARTITION BY s.company_code,s.region_code,s.channel,s.product_code
                 ORDER BY s.market_date)
), changes AS (
    SELECT h.*, h.share_pct-h.previous_share AS delta_pp, rule.warning_pp, rule.critical_pp
    FROM history h
    JOIN market.signal_rules rule ON rule.event_type='market_share_change'
    WHERE h.previous_date=h.market_date-1
      AND h.previous_model=h.model_version
      AND h.previous_balance=0 AND h.current_balance=0
      AND (h.market_date+1)::timestamp AT TIME ZONE 'UTC' >= rule.enabled_from
)
INSERT INTO market.events (
    event_key,observed_at,event_type,severity,region_code,company_code,
    product_code,channel,title,evidence,is_synthetic
)
SELECT concat('share:',company_code,':',region_code,':',channel,':',product_code,':',market_date),
    (market_date+1)::timestamp AT TIME ZONE 'UTC', 'market_share_change',
    CASE WHEN abs(delta_pp)>=critical_pp THEN 'critical' ELSE 'warning' END,
    region_code,company_code,product_code,channel,
    CASE WHEN delta_pp<0 THEN 'Снижение расчетной доли на ' ELSE 'Рост расчетной доли на ' END
        || round(abs(delta_pp),2)::text || ' п.п.',
    jsonb_build_object(
        'previous_date',previous_date,'market_date',market_date,
        'previous_share_pct',round(previous_share,2),'current_share_pct',round(share_pct,2),
        'share_change_pp',round(delta_pp,2),'model_version',model_version,
        'warning_threshold_pp',warning_pp,'critical_threshold_pp',critical_pp,
        'basis','Доля по выручке в синтетической модели; не оценка фактического рынка'
    ), true
FROM changes WHERE abs(delta_pp)>=warning_pp
ON CONFLICT (event_key) DO NOTHING;

-- Price position = (client price / lowest available competitor price - 1) * 100.
-- Positive delta means the client's premium increased, i.e. position worsened.
WITH offers AS (
    SELECT p.* FROM market.prices p
    JOIN market.signal_rules rule ON rule.event_type='price_position_change'
    WHERE rule.enabled AND p.is_available AND p.price_rub>0
      AND p.observed_at >= rule.enabled_from - interval '1 hour'
      AND p.observed_at <= (SELECT max(period_at) FROM market.pipeline_runs)
), cheapest AS (
    SELECT DISTINCT ON (p.observed_at,p.region_code,p.channel,p.product_code)
        p.observed_at,p.region_code,p.channel,p.product_code,
        p.company_code AS competitor_code,c.name AS competitor_name,p.price_rub AS competitor_price
    FROM offers p JOIN market.companies c ON c.code=p.company_code
    WHERE c.role='competitor'
    ORDER BY p.observed_at,p.region_code,p.channel,p.product_code,p.price_rub,p.company_code
), positions AS (
    SELECT p.*, r.competitor_code,r.competitor_name,r.competitor_price,
        100*(p.price_rub/r.competitor_price-1) AS premium_pct
    FROM offers p JOIN market.companies c ON c.code=p.company_code
    JOIN cheapest r USING (observed_at,region_code,channel,product_code)
    WHERE c.role='client'
), history AS (
    SELECT p.*,
        lag(p.observed_at) OVER w AS previous_at,
        lag(p.premium_pct) OVER w AS previous_premium,
        lag(p.price_rub) OVER w AS previous_client_price,
        lag(p.competitor_price) OVER w AS previous_competitor_price,
        lag(p.competitor_code) OVER w AS previous_competitor_code,
        lag(p.competitor_name) OVER w AS previous_competitor_name
    FROM positions p
    WINDOW w AS (PARTITION BY company_code,region_code,channel,product_code ORDER BY observed_at)
), changes AS (
    SELECT h.*, h.premium_pct-h.previous_premium AS delta_pp,rule.warning_pp,rule.critical_pp
    FROM history h JOIN market.signal_rules rule ON rule.event_type='price_position_change'
    WHERE h.observed_at=h.previous_at+interval '1 hour'
      AND h.observed_at>=rule.enabled_from
)
INSERT INTO market.events (
    event_key,observed_at,event_type,severity,region_code,company_code,
    product_code,channel,title,evidence,is_synthetic
)
SELECT concat('position:',company_code,':',region_code,':',channel,':',product_code,':',
              extract(epoch FROM observed_at)::bigint),
    observed_at,'price_position_change',
    CASE WHEN abs(delta_pp)>=critical_pp THEN 'critical' ELSE 'warning' END,
    region_code,company_code,product_code,channel,
    CASE WHEN delta_pp>0 THEN 'Ухудшение ценовой позиции на ' ELSE 'Улучшение ценовой позиции на ' END
        || round(abs(delta_pp),2)::text || ' п.п.',
    jsonb_build_object(
        'previous_observed_at',previous_at,
        'previous_premium_pct',round(previous_premium,2),'current_premium_pct',round(premium_pct,2),
        'position_change_pp',round(delta_pp,2),
        'previous_client_price_rub',previous_client_price,'client_price_rub',price_rub,
        'previous_competitor_price_rub',previous_competitor_price,'competitor_price_rub',competitor_price,
        'previous_competitor_code',previous_competitor_code,'previous_competitor_name',previous_competitor_name,
        'competitor_code',competitor_code,'competitor_name',competitor_name,
        'warning_threshold_pp',warning_pp,'critical_threshold_pp',critical_pp,
        'basis','(Цена клиента / минимальная доступная цена конкурента − 1) × 100. Рост означает ухудшение.'
    ),true
FROM changes WHERE abs(delta_pp)>=warning_pp
ON CONFLICT (event_key) DO NOTHING;
