#!/usr/bin/env bash
# End-to-end check: serve fixtures, scrape them with Prometheus, run every
# dashboard query and evaluate every alert rule.
# Usage: PROMETHEUS_BIN=/path/to/prometheus test/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PROMETHEUS_BIN=${PROMETHEUS_BIN:-prometheus}
PORT=${PROM_PORT:-19091}
TMP=$(mktemp -d)
python3 -I test/fixture_server.py test/fixtures 18998 &
FIX=$!
"$PROMETHEUS_BIN" --config.file=test/prometheus.yml --storage.tsdb.path="$TMP" \
  --web.listen-address=127.0.0.1:$PORT --log.level=warn &
PROM=$!
trap 'kill $FIX $PROM 2>/dev/null || true; rm -rf "$TMP"' EXIT
echo "waiting 45s for a few scrapes..."
sleep 45
down=$(curl -s "http://127.0.0.1:$PORT/api/v1/targets" | python3 -c 'import json,sys; t=json.load(sys.stdin)["data"]["activeTargets"]; print(sum(1 for x in t if x["health"]!="up"))')
[ "$down" = "0" ] || { echo "$down scrape targets are down"; exit 1; }
python3 scripts/check_dashboards.py --prometheus "http://127.0.0.1:$PORT"
curl -s "http://127.0.0.1:$PORT/api/v1/rules" | python3 -c '
import json, sys
groups = json.load(sys.stdin)["data"]["groups"]
bad = [(g["name"], r["name"], r.get("lastError")) for g in groups for r in g["rules"] if r.get("health") != "ok"]
n = sum(len(g["rules"]) for g in groups)
print(f"{n} alert rules evaluated, {len(bad)} unhealthy")
for b in bad: print("  ", b)
sys.exit(1 if bad else 0)'
