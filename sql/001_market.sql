BEGIN;

CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE IF NOT EXISTS market.regions (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS market.companies (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('client', 'competitor')),
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS market.products (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    brand TEXT NOT NULL,
    package_kg NUMERIC(10,3) NOT NULL CHECK (package_kg > 0),
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS market.presence (
    company_code TEXT NOT NULL REFERENCES market.companies(code),
    region_code TEXT NOT NULL REFERENCES market.regions(code),
    channel TEXT NOT NULL CHECK (
        channel IN ('wholesale', 'distribution', 'retail')
    ),
    first_seen_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (company_code, region_code, channel)
);

CREATE TABLE IF NOT EXISTS market.prices (
    observed_at TIMESTAMPTZ NOT NULL,
    company_code TEXT NOT NULL,
    region_code TEXT NOT NULL,
    channel TEXT NOT NULL,
    product_code TEXT NOT NULL REFERENCES market.products(code),
    price_rub NUMERIC(12,2) NOT NULL CHECK (price_rub > 0),
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (
        observed_at, company_code, region_code, channel, product_code
    ),

    FOREIGN KEY (company_code, region_code, channel)
        REFERENCES market.presence(company_code, region_code, channel)
);

COMMENT ON COLUMN market.prices.price_rub IS
'Цена за упаковку товара в рублях без НДС и доставки.
Сравнение только внутри одинакового товара и канала.
В первой модели одинаковые условия оплаты и партии.';

CREATE INDEX IF NOT EXISTS prices_comparison_idx
ON market.prices (
    region_code, product_code, channel, observed_at DESC
);

CREATE TABLE IF NOT EXISTS market.pipeline_runs (
    period_at TIMESTAMPTZ PRIMARY KEY,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    generator_version TEXT NOT NULL,
    prices_written INTEGER NOT NULL CHECK (prices_written >= 0)
);

CREATE TABLE IF NOT EXISTS market.events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    observed_at TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (
        severity IN ('info', 'warning', 'critical')
    ),
    region_code TEXT NOT NULL REFERENCES market.regions(code),
    company_code TEXT REFERENCES market.companies(code),
    product_code TEXT REFERENCES market.products(code),
    channel TEXT CHECK (
        channel IN ('wholesale', 'distribution', 'retail')
    ),
    title TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS events_recent_idx
ON market.events (observed_at DESC, severity);

CREATE TABLE IF NOT EXISTS market.hypotheses (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES market.events(id),
    hypothesis_code TEXT NOT NULL,
    explanation TEXT NOT NULL,
    supporting_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    verification_needed TEXT NOT NULL,
    UNIQUE (event_id, hypothesis_code)
);

COMMIT;
