#!/usr/bin/env bash
set -euo pipefail

compose=(docker compose -f docker-compose.yml -f deploy/nonprod/docker-compose.local.yml)
"${compose[@]}" config --quiet
"${compose[@]}" up --build -d

for url in \
  http://localhost:19000/health/ready \
  http://localhost:13476/healthz \
  http://localhost:19090/-/ready; do
  curl --fail --retry 20 --retry-delay 2 --retry-connrefused "$url" >/dev/null
done

"${compose[@]}" exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --describe --topic taxstamp.events.v1
printf '%s\n' 'Disposable stack validation succeeded. Tear down with: docker compose -f docker-compose.yml -f deploy/nonprod/docker-compose.local.yml down -v'
