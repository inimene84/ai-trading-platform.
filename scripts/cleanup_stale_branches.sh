#!/usr/bin/env bash
# Delete stale remote branches already merged into main (run locally with push access).
set -euo pipefail
STALE=(
  feat/p1-p2-strategy-llm-overhaul
  cursor/observability-env-vars-f7d8
  cursor/fix-all-live-trading-f7d8
  cursor/fix-grafana-datasource-f7d8
  cursor/fix-sl-tp-hedge-guard-33c0
  cursor/hostinger-vps-fixes-df88
  cursor/p2-validation-routes-f7d8
  cursor/ssh-key-test-f7d8
  add-task-list-ui
)
for b in "${STALE[@]}"; do
  if git ls-remote --exit-code --heads origin "$b" >/dev/null 2>&1; then
    echo "Deleting origin/$b"
    git push origin --delete "$b" || echo "  skip (no permission or already gone)"
  fi
done
echo "Done. Review unmerged branches manually before deleting:"
git branch -r --no-merged origin/main
