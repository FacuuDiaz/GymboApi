#!/bin/sh
# Runs once, when the PostgreSQL volume is first initialized (official image
# behaviour). Creates the least-privilege role the API connects with: it owns
# the application database (so it can run migrations) but is NOT a superuser.
# The superuser (POSTGRES_SUPERUSER) is only for administration.
set -eu

createdb_flag="NOCREATEDB"
# Development/test only: pytest-django creates and drops a separate test database.
if [ "${APP_DB_CREATEDB:-false}" = "true" ]; then
    createdb_flag="CREATEDB"
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" -v db="$POSTGRES_DB" <<SQL
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEROLE ${createdb_flag};
ALTER DATABASE :"db" OWNER TO :"app_user";
ALTER SCHEMA public OWNER TO :"app_user";
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO :"app_user";
SQL
