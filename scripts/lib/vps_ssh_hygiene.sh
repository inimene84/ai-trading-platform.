#!/usr/bin/env bash
# Shared SSH StrictModes fix for Hostinger VPS (run as root).
vps_ssh_hygiene() {
  local pubkey="${CLOUD_AGENT_PUBKEY:-ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMPdc81hu58Qrgt5ODe8OvMJmqrM11GB848GmSqj1d7t valgutom@gmail.com}"
  chown root:root /root
  chmod 700 /root
  mkdir -p /root/.ssh
  chmod 700 /root/.ssh
  touch /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  grep -vF "$pubkey" /root/.ssh/authorized_keys > /tmp/ak_clean || true
  printf '%s\n' "$pubkey" >> /tmp/ak_clean
  mv /tmp/ak_clean /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
}
