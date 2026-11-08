BEGIN;
CREATE TABLE IF NOT EXISTS market.product_substitutes (
 product_code text REFERENCES market.products(code),
 substitute_code text REFERENCES market.products(code),
 rationale text NOT NULL,
 PRIMARY KEY(product_code,substitute_code), CHECK(product_code<>substitute_code)
);
CREATE TABLE IF NOT EXISTS market.assortment_config (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 origin_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS market.assortment_runs (
 period_at timestamptz PRIMARY KEY REFERENCES market.pipeline_runs(period_at)
);
CREATE TABLE IF NOT EXISTS market.assortment_observations (
 observed_at timestamptz NOT NULL,
 company_code text NOT NULL REFERENCES market.companies(code),
 region_code text NOT NULL REFERENCES market.regions(code),
 channel text NOT NULL,
 product_code text NOT NULL REFERENCES market.products(code),
 price_rub numeric(12,2) NOT NULL CHECK(price_rub>0),
 is_available boolean NOT NULL,
 source_label text NOT NULL,
 PRIMARY KEY(observed_at,company_code,region_code,channel,product_code)
);
COMMIT;
