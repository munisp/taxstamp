#!/usr/bin/env bash
# Runs every mandatory gate and writes the raw output to evidence/.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p evidence
VENV=.venv/bin

run() {
  local name="$1"
  shift
  echo "== ${name}"
  if "$@" >"evidence/${name}.log" 2>&1; then
    echo "   pass -> evidence/${name}.log"
  else
    echo "   FAIL -> evidence/${name}.log"
    return 1
  fi
}

status=0
run lint "${VENV}/ruff" check src tests scripts || status=1
run format "${VENV}/ruff" format --check src tests scripts || status=1
run type "${VENV}/mypy" || status=1
run security "${VENV}/bandit" -q -c pyproject.toml -r src || status=1
run audit-deps "${VENV}/pip-audit" -r requirements.txt --strict || status=1
run tests "${VENV}/pytest" -q --cov --cov-report=term-missing || status=1
run import-check "${VENV}/python" -c "import taxstamp.api.app, taxstamp.worker.main, taxstamp.cli" || status=1
if [[ -n "${TAXSTAMP_STORAGE_ENCRYPTION_ENV_FILE:-}" && -n "${TAXSTAMP_STORAGE_ENCRYPTION_ATTESTATION:-}" ]]; then
  run storage-encryption "${VENV}/python" scripts/check_storage_encryption.py \
    --env-file "${TAXSTAMP_STORAGE_ENCRYPTION_ENV_FILE}" \
    --attestation "${TAXSTAMP_STORAGE_ENCRYPTION_ATTESTATION}" \
    --output evidence/storage-encryption.json || status=1
else
  echo "== storage-encryption"
  echo "   skipped: set TAXSTAMP_STORAGE_ENCRYPTION_ENV_FILE and TAXSTAMP_STORAGE_ENCRYPTION_ATTESTATION to enforce"
fi
exit "${status}"
