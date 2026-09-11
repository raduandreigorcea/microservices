#!/bin/bash
# Runs once, when Postgres first initialises an empty data directory.
# Each data-owning service gets its own role and schema so it can't read
# the other's tables. gateway and app_service store nothing, so no role.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<-SQL
    CREATE ROLE "${USER_SERVICE_DB_USER}"
        LOGIN PASSWORD '${USER_SERVICE_DB_PASSWORD}';
    CREATE ROLE "${SCRAPER_SERVICE_DB_USER}"
        LOGIN PASSWORD '${SCRAPER_SERVICE_DB_PASSWORD}';

    CREATE SCHEMA user_service
        AUTHORIZATION "${USER_SERVICE_DB_USER}";
    CREATE SCHEMA scraper_service
        AUTHORIZATION "${SCRAPER_SERVICE_DB_USER}";

    GRANT CONNECT ON DATABASE "${POSTGRES_DB}"
        TO "${USER_SERVICE_DB_USER}", "${SCRAPER_SERVICE_DB_USER}";

    -- lets a service write "users" instead of "user_service.users"
    ALTER ROLE "${USER_SERVICE_DB_USER}" SET search_path = user_service;
    ALTER ROLE "${SCRAPER_SERVICE_DB_USER}" SET search_path = scraper_service;

    -- nothing should live in the shared schema
    REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL

echo "database roles and schemas created"
