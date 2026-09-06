#!/usr/bin/env sh
# Create the things ODD will not accept ingestion without, and write the
# collector configs that need their tokens.
#
#   ./deploy/odd-bootstrap.sh [platform-url] [--demo]
#
# Two separate reasons this script exists:
#
#   * push.py's data source. ODD rejects a DataEntityList whose
#     data_source_oddrn it has never seen. push.py registers this itself, so
#     the call here is only to fail early with a clear message.
#   * The collectors' tokens. `POST /ingestion/datasources` is guarded by a
#     filter that is always on, regardless of auth.ingestion.filter.enabled,
#     so odd-collector cannot register itself and dies at startup with a bare
#     500 until it is given a token minted here.
#
# Idempotent: a re-run reuses the token from the config it wrote last time.
# ODD reports an existing collector's token masked, so it cannot be read back
# from the platform -- see token_for for what happens when there is no local
# copy either.
set -eu

# `--demo` in any position, so the flag does not depend on remembering that
# the URL comes first.
DEMO=0
ARGS=""
for arg in "$@"; do
  if [ "$arg" = "--demo" ]; then DEMO=1; else ARGS="$arg"; fi
done

URL="${ARGS:-http://localhost:8080}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Where the collectors reach the source database. Inside compose this is a
# service or container name, which is also what ends up in the dataset ODDRNs
# -- so the push must be given the same value as ODD_PG_HOST or the tables fork
# into two catalog objects.
PG_HOST="${ODD_PG_HOST:-db}"
PG_PORT="${ODD_PG_PORT:-5432}"
PG_DB="${ODD_PG_DB:-erp}"
PG_USER="${ODD_PG_USER:-postgres}"
PG_PASSWORD="${ODD_PG_PASSWORD:-postgres}"
PLATFORM_INTERNAL="${ODD_PLATFORM_INTERNAL_URL:-http://odd-platform:8080}"

api() { curl -sS -X "$1" "$URL$2" -H 'Content-Type: application/json' -d "$3"; }

# The token for a collector, in the only order that actually works.
#
# ODD reports an existing collector's token **masked** (`******GvenQ0`), so it
# cannot be read back -- which the first version of this script assumed it
# could, and then fell through to creating a collector whose name was already
# taken and exited with an empty token. Three steps instead:
#
#   1. reuse the token already in the config we wrote last time. Non-destructive
#      and the common case on a re-run.
#   2. rotate it if the collector exists in ODD but we have no local copy. This
#      does break a running collector -- until it reloads the config this
#      script is about to rewrite, which is the point.
#   3. create the collector, first time only.
token_for() {
  name="$1"
  config="$2"

  if [ -f "$HERE/$config" ]; then
    local_token=$(sed -n 's/^token: *"\(.*\)"$/\1/p' "$HERE/$config" | head -1)
    case "$local_token" in
      ""|*"*"*) ;;
      *) printf '%s' "$local_token"; return 0 ;;
    esac
  fi

  id=$(curl -sS "$URL/api/collectors?page=1&size=1000" \
    | python3 -c "import sys,json
name=sys.argv[1]
for c in json.load(sys.stdin).get('items', []):
    if c.get('name') == name:
        print(c.get('id') or '')
        break" "$name")

  if [ -n "$id" ]; then
    echo "rotating the token for $name (it cannot be read back)" >&2
    api PUT "/api/collectors/$id/token" '' \
      | python3 -c "import sys,json; print((json.load(sys.stdin).get('token') or {}).get('value',''))"
    return 0
  fi

  api POST /api/collectors "{\"name\":\"$name\",\"description\":\"created by odd-bootstrap.sh\"}" \
    | python3 -c "import sys,json; print((json.load(sys.stdin).get('token') or {}).get('value',''))"
}

echo "platform: $URL"
curl -sS "$URL/actuator/health" | grep -q UP || { echo "platform is not UP"; exit 1; }

COLLECTOR_TOKEN="${ODD_COLLECTOR_TOKEN:-$(token_for erp-collector collector_config.yaml)}"
PROFILER_TOKEN="${ODD_PROFILER_TOKEN:-$(token_for erp-profiler profiler_config.yaml || true)}"
[ -n "$COLLECTOR_TOKEN" ] || exit 1

# The collector and the profiler describe the same database in different
# words: type `postgresql` vs `postgres`, and the credential key `user` vs
# `username`. Getting either wrong is a startup crash -- "Couldn't handle
# config. Reason 'postgres'" for the first, a pydantic "Field required" for
# the second -- so both are parameters here rather than a template anyone is
# expected to remember.
write_config() {
  cat > "$HERE/$1" <<YAML
# Written by deploy/odd-bootstrap.sh -- the token is an ODD collector secret.
default_pulling_interval: ${2}
token: "${3}"
platform_host_url: ${PLATFORM_INTERNAL}
${4}:
  - type: ${6}
    name: ${5}
    host: ${PG_HOST}
    port: ${PG_PORT}
    database: ${PG_DB}
    ${7}: ${PG_USER}
    password: ${PG_PASSWORD}
YAML
  chmod 600 "$HERE/$1"
  echo "wrote deploy/$1"
}

write_config collector_config.yaml 10 "$COLLECTOR_TOKEN" plugins erp_postgres postgresql user
[ -n "$PROFILER_TOKEN" ] && write_config profiler_config.yaml 360 "$PROFILER_TOKEN" profilers erp_profiler postgres username

# The demo sources, appended rather than written by write_config because each
# adapter spells its connection differently -- superset takes a `server` URL,
# mongodb a `protocol`. Behind a flag: these hosts only exist under
# `--profile demo`, and a plugin that cannot connect logs an error every cycle.
#
# The config file is gitignored because it carries the collector token, which
# is why these have to be generated rather than committed. Without this a
# fresh clone got Postgres and nothing else, and the demo chain the README
# documents never came up.
if [ "$DEMO" = "1" ]; then
  cat >> "$HERE/collector_config.yaml" <<YAML

  - type: postgresql
    name: dwh
    host: ${PG_HOST}
    port: ${PG_PORT}
    database: ${DWH_DB:-dwh}
    user: ${PG_USER}
    password: ${PG_PASSWORD}

  - type: mssql
    name: erp_mssql
    host: ${MSSQL_HOST:-mssql}
    port: ${MSSQL_PORT:-1433}
    database: ${MSSQL_DB:-erp}
    # Not sa. The adapter enumerates every table it can see and has no schema
    # filter, so this login's permissions are what keep CDC's bookkeeping out
    # of the catalogue -- see deploy/mssql-cdc.sql.
    user: ${MSSQL_USER:-odd_collector}
    password: "${MSSQL_PASSWORD:-C0llect!Reader}"

  - type: mongodb
    name: reference_mongo
    protocol: mongodb
    host: ${MONGO_HOST:-mongo}
    port: "${MONGO_PORT:-27017}"
    database: ${MONGO_DB:-reference}
    user: ${MONGO_USER:-root}
    password: ${MONGO_PASSWORD:-rootpass}

  - type: superset
    name: bi_superset
    server: ${SUPERSET_URL:-http://superset:8088}
    username: ${SUPERSET_USER:-admin}
    password: ${SUPERSET_PASSWORD:-admin}
YAML
  echo "added the demo sources (mssql, mongodb, superset)"
fi

cat <<EOF

next:
  docker compose up -d
  docker compose exec app python seed/seed.py
  docker compose exec app python core/runner.py --backfill-days 44       --odd-url http://odd-platform:8080

  # and for the second source, the CDC and the sync rules:
  ./deploy/odd-bootstrap.sh --demo
EOF
