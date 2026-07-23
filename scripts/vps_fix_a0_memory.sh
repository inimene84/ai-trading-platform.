#!/usr/bin/env bash
# Raise Agent Zero (a0-instance) memory ceiling and add host swap headroom.
# Targets UI forced re-login caused by cgroup OOM kills (not network).
#
# Run ON the trading VPS as root:
#   bash scripts/vps_fix_a0_memory.sh
#
# Defaults fit a ~15 GiB host: 12G container RAM + 14G memswap, 4G host swapfile.
set -euo pipefail

A0_CONTAINER="${A0_CONTAINER:-a0-instance}"
A0_COMPOSE_DIR="${A0_COMPOSE_DIR:-/docker/agent-zero}"
COMPOSE_FILE="${COMPOSE_FILE:-$A0_COMPOSE_DIR/docker-compose.yml}"
MEM_LIMIT="${MEM_LIMIT:-12G}"
MEMSWAP_LIMIT="${MEMSWAP_LIMIT:-14G}"
SWAPFILE="${SWAPFILE:-/swapfile-a0}"
SWAP_SIZE="${SWAP_SIZE:-4G}"

echo "=== 1) Host swap for OOM headroom ==="
if swapon --show | grep -q .; then
  echo "Swap already present:"
  swapon --show
else
  if [[ ! -f "$SWAPFILE" ]]; then
    echo "Creating $SWAPFILE ($SWAP_SIZE)..."
    fallocate -l "$SWAP_SIZE" "$SWAPFILE" || dd if=/dev/zero of="$SWAPFILE" bs=1M count=4096
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE"
  fi
  swapon "$SWAPFILE"
  if ! grep -qF "$SWAPFILE" /etc/fstab 2>/dev/null; then
    echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
  fi
  echo "Swap enabled:"
  swapon --show
fi
free -h

echo ""
echo "=== 2) Patch $COMPOSE_FILE ==="
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "ERROR: compose file not found: $COMPOSE_FILE" >&2
  exit 1
fi
cp -a "$COMPOSE_FILE" "${COMPOSE_FILE}.bak.$(date +%Y%m%d%H%M%S)"

python3 - "$COMPOSE_FILE" "$MEM_LIMIT" "$MEMSWAP_LIMIT" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
mem_limit = sys.argv[2]
memswap_limit = sys.argv[3]
text = path.read_text()

# deploy.resources.limits.memory (compose may ignore without swarm)
text = re.sub(
    r"(deploy:\s*\n(?:\s+.*\n)*?\s+limits:\s*\n(?:\s+.*\n)*?\s+memory:\s*)\S+",
    rf"\g<1>{mem_limit}",
    text,
    count=1,
)

if re.search(r"^\s*mem_limit:\s*", text, re.M):
    text = re.sub(r"^(\s*mem_limit:\s*)\S+", rf"\g<1>{mem_limit}", text, count=1, flags=re.M)
else:
    text = text.replace(
        "    networks:\n      - n8n_default\n    deploy:",
        f"    networks:\n      - n8n_default\n    mem_limit: {mem_limit}\n    memswap_limit: {memswap_limit}\n    deploy:",
    )

if re.search(r"^\s*memswap_limit:\s*", text, re.M):
    text = re.sub(
        r"^(\s*memswap_limit:\s*)\S+",
        rf"\g<1>{memswap_limit}",
        text,
        count=1,
        flags=re.M,
    )
elif f"mem_limit: {mem_limit}" in text and "memswap_limit:" not in text:
    text = text.replace(
        f"mem_limit: {mem_limit}",
        f"mem_limit: {mem_limit}\n    memswap_limit: {memswap_limit}",
        1,
    )

path.write_text(text)
print(f"Patched mem_limit={mem_limit} memswap_limit={memswap_limit}")
PY

echo ""
echo "=== 3) Live docker update + compose recreate ==="
# docker update units are lowercase g
MEM_DOCKER="${MEM_LIMIT%G}g"
MEMSWAP_DOCKER="${MEMSWAP_LIMIT%G}g"
docker update --memory "$MEM_DOCKER" --memory-swap "$MEMSWAP_DOCKER" "$A0_CONTAINER"
cd "$A0_COMPOSE_DIR"
docker compose up -d a0
sleep 8

python3 - <<PY
import json, subprocess
j = json.loads(subprocess.check_output(["docker", "inspect", "$A0_CONTAINER"]))[0]
m = j["HostConfig"]["Memory"]
s = j["HostConfig"]["MemorySwap"]
print(f"Live Memory={m/2**30:.1f}GiB MemorySwap={s/2**30:.1f}GiB Status={j['State']['Status']}")
PY

echo ""
echo "=== 4) Verify UI + cgroup OOM counters ==="
ok=0
for i in $(seq 1 36); do
  for port in 5173 8000; do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://127.0.0.1:${port}/" || true)
    if [[ "$code" =~ ^(200|302|401|403)$ ]]; then
      echo "A0 UI ready on :$port HTTP $code (try $i)"
      ok=1
      break 2
    fi
  done
  sleep 5
done
[[ "$ok" == "1" ]] || echo "WARN: A0 UI not responding yet — check: docker logs $A0_CONTAINER --tail 50"

docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}' "$A0_CONTAINER"
CID=$(docker inspect -f '{{.Id}}' "$A0_CONTAINER")
EVENTS="/sys/fs/cgroup/system.slice/docker-${CID}.scope/memory.events"
if [[ -f "$EVENTS" ]]; then
  echo "memory.events:"
  cat "$EVENTS"
fi

echo ""
echo "=== 5) Trading stack untouched check ==="
curl -sf --max-time 10 http://127.0.0.1:8001/health | python3 -m json.tool | head -20 || true
curl -sf --max-time 10 http://127.0.0.1:8001/api/trading/loop/status | python3 -m json.tool | head -25 || true

echo ""
echo "=== Host ==="
free -h
swapon --show
echo "DONE"
