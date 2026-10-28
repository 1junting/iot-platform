#!/bin/sh
set -eu
for service in exchange auth devices analytics collector edge1 edge2 edge3; do
  upper=$(echo "$service" | tr '[:lower:]' '[:upper:]')
  password=$(printenv "${upper}_DB_PASSWORD")
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE ${service}_app LOGIN PASSWORD '${password}';
CREATE DATABASE ${service} OWNER ${service}_app;
REVOKE ALL ON DATABASE ${service} FROM PUBLIC;
GRANT CONNECT ON DATABASE ${service} TO ${service}_app;
SQL
done
