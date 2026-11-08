SET LOCAL TIME ZONE 'UTC';
SELECT pg_advisory_xact_lock(710030);
INSERT INTO market.assortment_config(singleton,origin_at)
SELECT true,coalesce((SELECT period_at FROM market.pipeline_runs ORDER BY period_at DESC OFFSET 1 LIMIT 1),max(period_at))
FROM market.pipeline_runs HAVING max(period_at) IS NOT NULL
ON CONFLICT DO NOTHING;
INSERT INTO market.products(code,name,category,brand,package_kg)
SELECT 'ALT_'||code,name||' — демо-аналог',category,'ДемоАналог',package_kg
FROM market.products WHERE code NOT LIKE 'ALT_%'
ON CONFLICT DO NOTHING;
INSERT INTO market.product_substitutes(product_code,substitute_code,rationale)
SELECT code,'ALT_'||code,
 'Демо-сопоставление по назначению и массе упаковки. Состав, характеристики и условия применения требуют проверки; взаимозаменяемость не подтверждена.'
FROM market.products WHERE code NOT LIKE 'ALT_%'
ON CONFLICT DO NOTHING;

DO $$
DECLARE point timestamptz; origin timestamptz;
BEGIN
 SELECT origin_at INTO origin FROM market.assortment_config;
 FOR point IN SELECT p.period_at FROM market.pipeline_runs p
 WHERE p.period_at>=origin AND NOT EXISTS (
  SELECT 1 FROM market.assortment_runs r WHERE r.period_at=p.period_at
 ) ORDER BY p.period_at LOOP
  INSERT INTO market.assortment_observations
  SELECT observed_at,company_code,region_code,channel,product_code,price_rub,is_available,
         'Синтетический генератор цен'
  FROM market.prices WHERE observed_at=point;

  -- One deterministic example per region; all other assortment data is copied unchanged.
  WITH competitors AS (
   SELECT p.region_code,p.channel,p.company_code,min(p.product_code) AS product_code,
     row_number() OVER (PARTITION BY p.region_code,p.channel ORDER BY p.company_code) AS rn
   FROM market.prices p JOIN market.companies c ON c.code=p.company_code
   WHERE p.observed_at=point AND c.role='competitor'
   GROUP BY p.region_code,p.channel,p.company_code
  )
  UPDATE market.assortment_observations a
  SET is_available=CASE WHEN x.rn=1 THEN point>origin ELSE point=origin END,
      source_label='Демо-сценарий изменения ассортимента'
  FROM competitors x
  WHERE a.observed_at=point AND a.region_code=x.region_code AND a.channel=x.channel
    AND a.company_code=x.company_code AND a.product_code=x.product_code AND x.rn IN (1,2);

  WITH competitors AS (
   SELECT p.region_code,p.channel,p.company_code,min(p.product_code) AS product_code,
     row_number() OVER (PARTITION BY p.region_code,p.channel ORDER BY p.company_code) AS rn
   FROM market.prices p JOIN market.companies c ON c.code=p.company_code
   WHERE p.observed_at=point AND c.role='competitor'
   GROUP BY p.region_code,p.channel,p.company_code
  )
  INSERT INTO market.assortment_observations
  SELECT point,p.company_code,p.region_code,p.channel,'ALT_'||p.product_code,
         round(p.price_rub*0.92,2),point>origin,'Демо-сценарий появления заменителя'
  FROM competitors x JOIN market.prices p
   ON p.observed_at=point AND p.company_code=x.company_code AND p.region_code=x.region_code
    AND p.channel=x.channel AND p.product_code=x.product_code
  WHERE x.rn=3;
  INSERT INTO market.assortment_runs VALUES(point);
 END LOOP;
END $$;

WITH history AS (
 SELECT a.*,lag(is_available) OVER w AS previous_available,
        lag(observed_at) OVER w AS previous_at
 FROM market.assortment_observations a
 WINDOW w AS (PARTITION BY company_code,region_code,channel,product_code ORDER BY observed_at)
), changes AS (
 SELECT h.*,s.product_code AS base_product,
   CASE WHEN NOT h.is_available THEN 'assortment_removed'
        WHEN s.product_code IS NOT NULL THEN 'substitute_added'
        ELSE 'assortment_added' END AS kind
 FROM history h JOIN market.companies c ON c.code=h.company_code
 LEFT JOIN market.product_substitutes s ON s.substitute_code=h.product_code
 WHERE c.role='competitor' AND h.previous_available IS NOT NULL
   AND h.previous_available<>h.is_available
   AND h.observed_at=h.previous_at+interval '1 hour'
)
INSERT INTO market.events(
 event_key,observed_at,event_type,severity,region_code,company_code,product_code,channel,title,evidence
)
SELECT concat('assortment:',company_code,':',region_code,':',channel,':',h.product_code,':',
              coalesce(base_product,''),':',extract(epoch FROM observed_at)::bigint),
 observed_at,kind,'warning',region_code,company_code,coalesce(base_product,h.product_code),channel,
 CASE kind WHEN 'assortment_removed' THEN 'Товар исчез из доступного ассортимента: '
 WHEN 'substitute_added' THEN 'Появился потенциальный заменитель: '
 ELSE 'Товар появился в доступном ассортименте: ' END||p.name,
 jsonb_build_object('offered_product_code',h.product_code,'offered_product_name',p.name,
 'previous_available',previous_available,'is_available',is_available,'previous_observed_at',previous_at,
 'price_rub',price_rub,'source_label',source_label,'is_demo',true,
 'basis','Изменение явного признака доступности между соседними часовыми снимками. Не отсутствие ответа источника.')
FROM changes h JOIN market.products p ON p.code=h.product_code
ON CONFLICT(event_key) DO NOTHING;
