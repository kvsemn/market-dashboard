#!/bin/bash
set -euo pipefail

psql --username "$POSTGRES_USER" --dbname postgres --set ON_ERROR_STOP=1 \
  --set app_password="$APP_DB_PASSWORD" \
  --set airflow_password="$AIRFLOW_DB_PASSWORD" <<'SQL'
CREATE ROLE dashboard_app LOGIN PASSWORD :'app_password';
CREATE DATABASE dashboard OWNER dashboard_app;
CREATE ROLE airflow LOGIN PASSWORD :'airflow_password';
CREATE DATABASE airflow OWNER airflow;
REVOKE ALL ON DATABASE dashboard FROM PUBLIC;
REVOKE ALL ON DATABASE airflow FROM PUBLIC;
SQL
