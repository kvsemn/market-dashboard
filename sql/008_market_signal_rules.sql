BEGIN;
SET LOCAL lock_timeout='10s';
CREATE TABLE IF NOT EXISTS market.signal_rules (
    event_type text PRIMARY KEY CHECK (event_type IN ('market_share_change','price_position_change')),
    warning_pp numeric NOT NULL CHECK (warning_pp > 0),
    critical_pp numeric NOT NULL CHECK (critical_pp >= warning_pp),
    enabled boolean NOT NULL DEFAULT true,
    enabled_from timestamptz NOT NULL DEFAULT now()
);
INSERT INTO market.signal_rules(event_type,warning_pp,critical_pp)
VALUES ('market_share_change',2,4),('price_position_change',5,10)
ON CONFLICT DO NOTHING;
COMMENT ON TABLE market.signal_rules IS
'Пороги абсолютного изменения в процентных пунктах. enabled_from исключает старую историю при первом включении.';
COMMIT;
SELECT * FROM market.signal_rules ORDER BY event_type;
