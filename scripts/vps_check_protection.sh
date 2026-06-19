#!/usr/bin/env bash
set -euo pipefail
docker cp scripts/vps_check_protection.py ai-trading-backend:/tmp/vps_check_protection.py
docker exec ai-trading-backend python3 /tmp/vps_check_protection.py
