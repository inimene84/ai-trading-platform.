#!/usr/bin/env bash
# Deprecated: use scripts/hostinger_vps_apply.sh (full production deploy on main).
set -euo pipefail
echo "NOTE: vps_deploy_risk_reviewer_fix.sh is deprecated — running hostinger_vps_apply.sh."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/hostinger_vps_apply.sh"
