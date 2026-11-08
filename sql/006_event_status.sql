BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE market.events
    ADD COLUMN IF NOT EXISTS workflow_status text
        NOT NULL DEFAULT 'new'
        CONSTRAINT events_workflow_status_check
        CHECK (workflow_status IN ('new', 'in_progress', 'closed')),
    ADD COLUMN IF NOT EXISTS status_updated_at timestamptz,
    ADD COLUMN IF NOT EXISTS status_version integer
        NOT NULL DEFAULT 0;

COMMIT;

SELECT workflow_status, count(*)
FROM market.events
GROUP BY workflow_status
ORDER BY workflow_status;
