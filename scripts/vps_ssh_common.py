"""Shared SSH connection settings for the VPS ops scripts.

Follows the same convention as scripts/ssh_vps_remote.sh and AGENTS.md:
configuration comes from environment variables, with the documented
defaults as fallback.

Env vars:
    SSH_HOST      VPS address (default: 72.60.18.113, same as ssh_vps_remote.sh)
    SSH_USER      SSH user (default: root)
    SSH_PORT      SSH port (default: 22)
    SSH_KEY_PATH  Private key file (default: ~/.ssh/id_vps_bot)
"""

from __future__ import annotations

import os
from pathlib import Path

SSH_HOST = os.getenv("SSH_HOST", "72.60.18.113")
SSH_USER = os.getenv("SSH_USER", "root")
SSH_PORT = os.getenv("SSH_PORT", "22")
SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", str(Path.home() / ".ssh" / "id_vps_bot"))

TARGET = f"{SSH_USER}@{SSH_HOST}"

_BASE_OPTS = [
    "-i", SSH_KEY_PATH,
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
]


# ssh argv without a remote command — for callers that append their own.
SSH_BASE = ["ssh", *_BASE_OPTS, "-p", SSH_PORT, TARGET]


def ssh_cmd(remote_command: str) -> list[str]:
    """Build an ssh argv that runs `remote_command` on the VPS."""
    return [*SSH_BASE, remote_command]


def scp_cmd(local_path: str, remote_path: str) -> list[str]:
    """Build an scp argv that copies a local file to `remote_path` on the VPS."""
    return ["scp", *_BASE_OPTS, "-P", SSH_PORT, str(local_path), f"{TARGET}:{remote_path}"]
