#!/usr/bin/env bash
# Proves the declarative edge configuration actually loads and actually rejects.
#
# What this does show: APISIX parses deploy/edge/apisix.yaml, proxies to the API, caps
# the public verification body and enforces the public quota.
# What it does not show: TLS, mutual TLS for the device fleet, or a WAF - all three need
# real certificates and a real deployment, and remain outstanding in the assurance report.
set -euo pipefail
cd "$(dirname "$0")/.."

EDGE="${EDGE_BASE_URL:-http://127.0.0.1:9081}"
PUBLIC_VERIFY="${EDGE}/v1/public/verify"
QUOTA=30

docker compose --profile edge up -d apisix >/dev/null
for _ in $(seq 1 30); do
  if [[ "$(curl -s -o /dev/null -w '%{http_code}' "${EDGE}/healthz")" == "200" ]]; then
    break
  fi
  sleep 1
done

status=$(curl -s -o /dev/null -w '%{http_code}' "${EDGE}/healthz")
[[ "${status}" == "200" ]] || { echo "edge did not proxy to the API: ${status}"; exit 1; }
echo "edge proxies to the API"

oversized=$(python3 -c 'print("{\"x\":\"" + "a" * 8000 + "\"}")')
status=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${PUBLIC_VERIFY}" \
  -H 'content-type: application/json' --data-binary "${oversized}")
[[ "${status}" == "413" ]] || { echo "oversized public body was not capped: ${status}"; exit 1; }
echo "oversized public verification body rejected at the edge (413)"

throttled=0
for _ in $(seq 1 $((QUOTA + 10))); do
  status=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${PUBLIC_VERIFY}" \
    -H 'content-type: application/json' -d '{}')
  if [[ "${status}" == "429" ]]; then
    throttled=$((throttled + 1))
  fi
done
[[ "${throttled}" -gt 0 ]] || { echo "public quota was never enforced"; exit 1; }
echo "public verification quota enforced (${throttled} of $((QUOTA + 10)) requests refused)"
