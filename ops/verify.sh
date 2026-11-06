#!/usr/bin/env bash
# Post-deployment verification (run after every installation / upgrade).
#   FP_VERIFY_EMAIL=admin@firma.de ops/verify.sh https://factorypulse.firma.de
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T backend python -m app.cli check || true
# certificate from the internal CA created by ops/generate-tls-cert.sh (company PKI roots are in the system store)
if [ -z "${SSL_CERT_FILE:-}" ] && [ -f tls/ca.crt ]; then export SSL_CERT_FILE="$PWD/tls/ca.crt"; fi
python3 backend/scripts/verify_deployment.py "${1:-http://localhost:${FP_HTTP_PORT:-8080}}"
