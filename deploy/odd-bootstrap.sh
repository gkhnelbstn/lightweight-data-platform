#!/usr/bin/env sh
# Create the things ODD will not accept ingestion without, and write the
# collector configs that need their tokens.
#
#   ./deploy/odd-bootstrap.sh [platform-url]
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
# Idempotent: an existing collector keeps its token, which is read back rather
# than rotated -- rotating it would silently break a running collector.
set -eu

URL="${1:-http://localhost:8080}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Where the collectors reach the source database. Inside compose this is a
# service or container name, which is also what ends up in the dataset ODDRNs
# -- so push.py must be given the same value as ODD_PG_HOST or the tables fork
# into two catalog objects.
PG_HOST="${ODD_PG_HOST:-ldp-pg}"
PG_PORT="${ODD_PG_PORT:-5432}"
PG_DB="${ODD_PG_DB:-erp}"
PG_USER="${ODD_PG_USER:-postgres}"
PG_PASSWORD="${ODD_PG_PASSWORD:-postgres}"
PLATFORM_INTERNAL="${ODD_PLATFORM_INTERNAL_URL:-http://odd-platform:8080}"

api() { curl -sS -X "$1" "$URL$2" -H 'Content-Type: application/json' -d "$3"; }

token_for() {
  name="$1"
  existing=$(curl -sS "$URL/api/collectors?page=1&size=1000" \
    | python3 -c "import sys,json
name=sys.argv[1]
for c in json.load(sys.stdin).get('items', []):
    if c.get('name') == name:
        print((c.get('token') or {}).get('value') or '')
        break" "$name")
  # A listed collector reports its token masked, so a re-run cannot recover it.
  case "$existing" in
    ""|*"*"*) ;;
    *) printf '%s' "$existing"; return 0 ;;
  esac
  api POST /api/collectors "{\"name\":\"$name\",\"description\":\"created by odd-bootstrap.sh\"}" \
    | python3 -c "import sys,json; print((json.load(sys.stdin).get('token') or {}).get('value',''))"
}

echo "platform: $URL"
curl -sS "$URL/actuator/health" | grep -q UP || { echo "platform is not UP"; exit 1; }

COLLECTOR_TOKEN="${ODD_COLLECTOR_TOKEN:-$(token_for erp-collector ODD_COLLECTOR_TOKEN)}"
PROFILER_TOKEN="${ODD_PROFILER_TOKEN:-$(token_for erp-profiler ODD_PROFILER_TOKEN || true)}"
[ -n "$COLLECTOR_TOKEN" ] || exit 1

write_config() {
  cat > "$HERE/$1" <<YAML
# Written by deploy/odd-bootstrap.sh -- the token is an ODD collector secret.
default_pulling_interval: ${2}
token: "${3}"
platform_host_url: ${PLATFORM_INTERNAL}
${4}:
  - type: postgres
    name: ${5}
    host: ${PG_HOST}
    port: ${PG_PORT}
    database: ${PG_DB}
    username: ${PG_USER}
    password: ${PG_PASSWORD}
YAML
  chmod 600 "$HERE/$1"
  echo "wrote deploy/$1"
}

write_config collector_config.yaml 10 "$COLLECTOR_TOKEN" plugins erp_postgres
[ -n "$PROFILER_TOKEN" ] && write_config profiler_config.yaml 360 "$PROFILER_TOKEN" profilers erp_profiler

cat <<EOF

next:
  docker compose -f deploy/odd-compose.yaml up -d
  export ODD_PG_HOST=$PG_HOST     # must match the collector, or the tables fork
  python integrations/odd/push.py --url $URL --no-datasets
EOF
