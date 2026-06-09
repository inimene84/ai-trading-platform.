#!/usr/bin/env bash
# ONE LINE for Hostinger browser terminal (root):
#   curl -fsSL "https://raw.githubusercontent.com/inimene84/ai-trading-platform./main/scripts/vps_bootstrap_and_deploy.sh" | bash
set -euo pipefail
exec bash <(curl -fsSL "https://raw.githubusercontent.com/inimene84/ai-trading-platform./main/scripts/vps_deploy_risk_reviewer_fix.sh")
