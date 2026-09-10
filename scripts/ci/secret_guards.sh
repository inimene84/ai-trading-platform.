#!/usr/bin/env bash
# Fail CI when known burned control-plane markers re-enter product / CI files.
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

denylist="scripts/ci/forbidden_substrings.txt"
if [[ ! -f "$denylist" ]]; then
  echo "missing $denylist" >&2
  exit 2
fi

token_needle="$(sed -n '3p' "$denylist")"
ip_needle="$(sed -n '4p' "$denylist")"

fail=0

echo "scanning working tree for burned admin fallback"
token_hits="$(git grep -nF -- "$token_needle" -- \
  ':!scripts/ci/forbidden_substrings.txt' \
  ':!.gitleaks.toml' \
  || true)"
if [[ -n "$token_hits" ]]; then
  echo "FORBIDDEN admin fallback present:" >&2
  echo "$token_hits" >&2
  fail=1
fi

echo "scanning product/CI paths for published VPS address"
ip_hits="$(git grep -nF -- "$ip_needle" -- \
  '.github' \
  '.env.example' \
  'AGENTS.md' \
  'README.md' \
  'DEPLOYMENT.md' \
  'frontend' \
  'backend' \
  'Dockerfile.backend' \
  'docker-compose.yml' \
  'docker-compose.prod.yml' \
  'nginx.conf' \
  || true)"
if [[ -n "$ip_hits" ]]; then
  echo "FORBIDDEN VPS address in product/CI files:" >&2
  echo "$ip_hits" >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "secret_guards: refuse merge until markers are removed and credentials rotated." >&2
  exit 1
fi

echo "secret_guards: clean"
