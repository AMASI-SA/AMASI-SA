"""Explicit, sanitized launch; no production process management or deployment."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys
from private_common import SOURCE_SHA, TOOL_ROOT, RuntimeBlocked, explicit_environment, load_config, load_export


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=("api", "worker"), required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    load_export(config)
    source = config["source_root"]
    head = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], timeout=15, text=True).strip()
    status = subprocess.check_output(["git", "-C", source, "status", "--porcelain", "--untracked-files=no"], timeout=15, text=True)
    if head != SOURCE_SHA or status.strip():
        raise RuntimeBlocked("clean_exact_candidate_required")
    os.chdir(TOOL_ROOT)
    os.execve(sys.executable, [sys.executable, "-B", str(TOOL_ROOT / "private_entry.py"), args.mode], explicit_environment(config, args.mode))


if __name__ == "__main__":
    try:
        main()
    except RuntimeBlocked as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(78) from None
    except Exception:
        print("private_runtime_launch_failed", file=sys.stderr)
        raise SystemExit(78) from None
