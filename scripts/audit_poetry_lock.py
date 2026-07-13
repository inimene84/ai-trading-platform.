#!/usr/bin/env python3
"""Run pip-audit against every package pinned in poetry.lock."""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "poetry.lock"


def main() -> int:
    text = LOCK.read_text(encoding="utf-8")
    pkgs = re.findall(
        r'\[\[package\]\]\s+name = "([^"]+)"\s+version = "([^"]+)"',
        text,
    )
    if not pkgs:
        print("No packages found in poetry.lock", file=sys.stderr)
        return 1

    req_body = "\n".join(f"{name}=={version}" for name, version in pkgs)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(req_body)
        req_path = f.name

    print(f"Auditing {len(pkgs)} packages from poetry.lock ...")
    result = subprocess.run(
        ["pip-audit", "-r", req_path],
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if output:
        print(output)
    if err:
        print(err)
    Path(req_path).unlink(missing_ok=True)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
