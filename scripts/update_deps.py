#!/usr/bin/env python3
"""
Keep the AWS SDK current.

New AWS services, operations and parameters ship in botocore releases. This bumps
the pinned boto3/botocore in requirements/agent.txt and requirements/console.txt to
the latest release and regenerates requirements/agent.lock.txt (resolved for the
agent's linux/arm64 Python 3.12 runtime). Run weekly by
.github/workflows/update-aws-sdk.yml; also usable by hand:

    python scripts/update_deps.py              # bump boto3/botocore + relock
    python scripts/update_deps.py --lock-only  # just regenerate the lock (after editing agent.txt)

Requires `uv` (pip install uv).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINNED_FILES = [ROOT / "requirements/agent.txt", ROOT / "requirements/console.txt"]
AGENT_REQUIREMENTS = ROOT / "requirements/agent.txt"
AGENT_LOCK = ROOT / "requirements/agent.lock.txt"


def latest_sdk_versions(fetch=None) -> dict:
    """{"boto3": x, "botocore": y} for the newest boto3 release and the botocore it requires."""
    fetch = fetch or (lambda url: json.load(urllib.request.urlopen(url, timeout=30)))
    info = fetch("https://pypi.org/pypi/boto3/json")["info"]
    botocore = next((m.group(1) for req in info.get("requires_dist") or []
                     if (m := re.match(r"botocore\s*.*?>=\s*([0-9][0-9.]*)", req))), None)
    if not botocore:
        raise RuntimeError("Could not determine the botocore version boto3 requires.")
    return {"boto3": info["version"], "botocore": botocore}


def current_pins(text: str) -> dict:
    return {name: m.group(1) for name in ("boto3", "botocore")
            if (m := re.search(rf"^{name}==([^\s#]+)", text, re.MULTILINE))}


def apply_pins(text: str, versions: dict) -> str:
    for name, version in versions.items():
        text = re.sub(rf"^{name}==[^\s#]+", f"{name}=={version}", text, flags=re.MULTILINE)
    return text


def relock() -> None:
    uv = shutil.which("uv")
    if not uv:
        sys.exit("uv is required to regenerate the agent lock: pip install uv")
    subprocess.run([
        uv, "pip", "compile", str(AGENT_REQUIREMENTS.relative_to(ROOT)),
        "--python-platform", "aarch64-manylinux2014", "--python-version", "3.12",
        "--generate-hashes", "--no-header", "--annotation-style", "line", "-q",
        "-o", str(AGENT_LOCK.relative_to(ROOT)),
    ], check=True, cwd=ROOT)


def github_output(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lock-only", action="store_true", help="Only regenerate requirements/agent.lock.txt")
    args = p.parse_args()

    if args.lock_only:
        relock()
        print("Regenerated requirements/agent.lock.txt")
        return

    latest = latest_sdk_versions()
    before = current_pins(AGENT_REQUIREMENTS.read_text())
    if before == latest:
        print(f"AWS SDK already current: boto3 {latest['boto3']}, botocore {latest['botocore']}")
        github_output(changed="false")
        return

    for path in PINNED_FILES:
        path.write_text(apply_pins(path.read_text(), latest))
    relock()
    summary = (f"boto3 {before.get('boto3')} -> {latest['boto3']}, "
               f"botocore {before.get('botocore')} -> {latest['botocore']}")
    print(f"Updated {summary}")
    github_output(changed="true", boto3=latest["boto3"], botocore=latest["botocore"], summary=summary)


if __name__ == "__main__":
    main()
