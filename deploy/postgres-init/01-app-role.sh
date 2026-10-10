#!/bin/bash
set -euo pipefail

psql -v ON_ERROR_STOP=1 -v app_password="$SO_DB_APP_PASSWORD" \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE ROLE so_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT ALL ON SCHEMA public TO so_app;
SQL
