#!/usr/bin/env bash
# Shared SSH StrictModes fix for Hostinger VPS (run as root).
# Does NOT rewrite authorized_keys unless CLOUD_AGENT_PUBKEY is explicitly set.
vps_ssh_hygiene() {
  chown root:root /root
  chmod 700 /root
  mkdir -p /root/.ssh
  chmod 700 /root/.ssh
  touch /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys

  if [[ -n "${CLOUD_AGENT_PUBKEY:-}" ]]; then
    local pubkey="$CLOUD_AGENT_PUBKEY"
    if ! grep -qF "$pubkey" /root/.ssh/authorized_keys; then
      printf '%s\n' "$pubkey" >> /root/.ssh/authorized_keys
    fi
    chmod 600 /root/.ssh/authorized_keys
  fi
}
