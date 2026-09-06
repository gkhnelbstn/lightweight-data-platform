#!/bin/bash
# Superset with the demo database already connected. Nothing here is a
# production pattern -- the admin password is in the file and the metadata
# database is the bundled SQLite -- but it makes the stack one command.
set -e
# One driver per source the demo charts: SQL Server for the ERP, Postgres for
# the warehouse mart. Neither ships in the image, and Superset reports a
# missing driver as "Connection failed, please check your connection settings"
# -- which sends you looking at the host and the password first.
pip install --no-cache-dir --quiet pymssql psycopg2-binary >/dev/null 2>&1 || true
superset db upgrade
superset fab create-admin --username admin --firstname Admin --lastname User \
  --email admin@superset.local --password admin || true
superset init
exec gunicorn --bind 0.0.0.0:8088 --workers 2 --worker-class gthread --threads 4 --timeout 120 "superset.app:create_app()"
