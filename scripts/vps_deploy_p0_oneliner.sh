#!/usr/bin/env bash
# Deprecated: use scripts/hostinger_vps_apply.sh (deploys main, not a feature branch).
set -euo pipefail
echo "NOTE: vps_deploy_p0_oneliner.sh is deprecated — running hostinger_vps_apply.sh on main."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/hostinger_vps_apply.sh"
