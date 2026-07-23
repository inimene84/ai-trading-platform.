#!/usr/bin/env bash
# Fix trading-VPS gaps: SSH /root perms, hermes-webui /root bind-mount,
# Kronos sidecar, and A0/Hermes quantumtrade MCP wiring.
#
# Run ON the trading VPS as root (Hostinger Browser Terminal is fine when SSH is broken):
#   bash scripts/vps_fix_hermes_kronos_gaps.sh
#
# Or paste from a cloned tree under PROJECT_DIR.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/ai-trading-platform-v3}"
HERMES_CONTAINER="${HERMES_CONTAINER:-hermes-webui}"
A0_CONTAINER="${A0_CONTAINER:-a0-instance}"
PUBKEY_LINE="${PUBKEY_LINE:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMPdc81hu58Qrgt5ODe8OvMJmqrM11GB848GmSqj1d7t valgutom@gmail.com}"

echo "=== 1) Restore /root SSH permissions ==="
chown root:root /root
chmod 700 /root
mkdir -p /root/.ssh
chown -R root:root /root/.ssh
chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
if ! grep -qxF "$PUBKEY_LINE" /root/.ssh/authorized_keys 2>/dev/null; then
  echo "$PUBKEY_LINE" >> /root/.ssh/authorized_keys
  echo "Appended cloud-agent pubkey to authorized_keys"
fi
stat -c '%U:%G %a %n' /root /root/.ssh /root/.ssh/authorized_keys

echo "=== 2) Recreate hermes-webui WITHOUT host /root bind-mount ==="
if ! docker ps -a --format '{{.Names}}' | grep -qx "$HERMES_CONTAINER"; then
  echo "WARN: $HERMES_CONTAINER not found — skip remount"
else
  python3 - "$HERMES_CONTAINER" <<'PY'
import json, os, subprocess, sys

name = sys.argv[1]
insp = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]
cfg, host = insp["Config"], insp["HostConfig"]
image = cfg["Image"]

# Keep mounts except exact host /root bind (breaks SSH ownership).
keep_mounts = []
dropped = []
for m in insp.get("Mounts") or []:
    src = (m.get("Source") or "").rstrip("/")
    dst = m.get("Destination") or ""
    mtype = m.get("Type") or "bind"
    if src == "/root":
        dropped.append(f"{src}->{dst}")
        continue
    if mtype == "bind":
        keep_mounts.append(("bind", m.get("Source"), dst, m.get("Mode") or "rw"))
    elif mtype == "volume":
        keep_mounts.append(("volume", m.get("Name") or src, dst, "rw"))

print("dropping mounts:", dropped or "(none matched /root)")
print("keeping mounts:", keep_mounts)

nets = list((insp.get("NetworkSettings") or {}).get("Networks") or {})
try:
    out = subprocess.check_output(["docker", "network", "ls", "--format", "{{.Name}}"], text=True)
    for n in out.splitlines():
        if "trading-net" in n and n not in nets:
            nets.append(n)
except Exception:
    pass

env = cfg.get("Env") or []
cmd = cfg.get("Cmd") or []
working_dir = cfg.get("WorkingDir") or ""
restart = (host.get("RestartPolicy") or {}).get("Name") or "unless-stopped"

port_args = []
for container_port, binds in (host.get("PortBindings") or {}).items():
    for b in binds or []:
        hp, hip = b.get("HostPort") or "", b.get("HostIp") or ""
        if hip and hp:
            port_args += ["-p", f"{hip}:{hp}:{container_port}"]
        elif hp:
            port_args += ["-p", f"{hp}:{container_port}"]
        else:
            port_args += ["-p", container_port]

has_hermes = any("/.hermes" in (dst or "") for _, _, dst, _ in keep_mounts)
if not has_hermes and os.path.isdir("/root/.hermes"):
    keep_mounts.append(("bind", "/root/.hermes", "/home/hermeswebui/.hermes", "rw"))

subprocess.check_call(["docker", "stop", name])
bak = f"{name}-pre-noroots-{os.getpid()}"
subprocess.check_call(["docker", "rename", name, bak])
print("renamed old container to", bak)

run = ["docker", "run", "-d", "--name", name, f"--restart={restart}"]
for mtype, src, dst, mode in keep_mounts:
    run += ["-v", f"{src}:{dst}" + ("" if mtype == "volume" else f":{mode}")]
for e in env:
    run += ["-e", e]
run += port_args
if working_dir:
    run += ["-w", working_dir]
if nets:
    run += ["--network", nets[0]]
# Use image defaults for entrypoint; pass original Cmd if present
run.append(image)
run += list(cmd)

print("creating", name, "image=", image, "nets=", nets)
subprocess.check_call(run)
for n in nets[1:]:
    subprocess.call(["docker", "network", "connect", n, name])

insp2 = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]
for m in insp2.get("Mounts") or []:
    print("mount", m.get("Source"), "->", m.get("Destination"))
    if (m.get("Source") or "").rstrip("/") == "/root":
        raise SystemExit("ERROR: /root mount still present")
print("OK: no host /root bind-mount")
# Keep backup container stopped for manual rollback: docker rename + start
subprocess.call(["docker", "rm", "-f", bak])
print("removed backup container", bak)
PY
fi

# Re-assert SSH perms after container recreate (entrypoint may have touched files under .hermes only now)
chown root:root /root && chmod 700 /root
chown -R root:root /root/.ssh
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys

echo "=== 3) Start Kronos sidecar ==="
cd "$PROJECT_DIR"
if [[ -f docker-compose.yml ]]; then
  # Prefer already-pulled tree; build/start kronos-infer service
  docker compose up -d --build kronos-infer 2>&1 | tail -30 || \
    docker compose -f docker-compose.prod.yml up -d --build kronos-infer 2>&1 | tail -30 || true
else
  echo "WARN: $PROJECT_DIR/docker-compose.yml missing"
fi
sleep 3
docker ps -a --filter name=kronos --format '{{.Names}} {{.Status}}' || true
curl -sf http://127.0.0.1:8002/health && echo || echo "WARN: Kronos /health on :8002 not ready yet (model pull may take minutes)"

echo "=== 4) Re-wire A0 + Hermes quantumtrade MCP ==="
if [[ -x "$PROJECT_DIR/scripts/vps_connect_a0_hermes_mcp.sh" ]]; then
  "$PROJECT_DIR/scripts/vps_connect_a0_hermes_mcp.sh" || true
elif [[ -f "$PROJECT_DIR/scripts/vps_connect_a0_hermes_mcp.sh" ]]; then
  bash "$PROJECT_DIR/scripts/vps_connect_a0_hermes_mcp.sh" || true
else
  echo "WARN: connect script missing — pull latest branch or configure MCP manually"
fi

echo "=== 5) Final checks ==="
curl -sf http://127.0.0.1:8001/health; echo
curl -sf http://127.0.0.1:8001/trading/loop/status; echo
docker ps --format '{{.Names}} {{.Status}}' | egrep 'ai-trading-backend|ai-trading-mcp|ai-trading-kronos|a0-instance|hermes-webui' || true
stat -c '%U:%G %a %n' /root /root/.ssh /root/.ssh/authorized_keys
echo "DONE — verify SSH from cloud agent: ssh root@\$SSH_HOST hostname"
