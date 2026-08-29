#!/usr/bin/env python3
"""Validate the ignored frontend build before release handoff."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from frontend_build_identity import (  # noqa: E402
    FrontendBuildIdentityError,
    read_frontend_build_identity,
    read_frontend_reproducibility_proof,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-git-sha", required=True)
    args = parser.parse_args()
    try:
        identity = read_frontend_build_identity(
            expected_git_sha=args.expected_git_sha.strip().lower(),
            require_git_source=True,
        )
        reproducibility = read_frontend_reproducibility_proof(
            frontend_build=identity
        )
    except FrontendBuildIdentityError as exc:
        print(f"FRONTEND_BUILD_REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "frontend_build": identity,
        "frontend_reproducibility": reproducibility,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
