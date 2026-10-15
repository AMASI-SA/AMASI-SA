#!/usr/bin/env python3
"""Validate the ignored frontend build before release handoff."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from frontend_build_identity import (  # noqa: E402
    FrontendBuildIdentityError,
    read_frontend_build_identity,
    read_frontend_reproducibility_proof,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument(
        "--reviewed-intent-v5",
        action="store_true",
        help=(
            "validate source and expected artifact against the tracked v5 "
            "release intent instead of requiring a Git checkout"
        ),
    )
    args = parser.parse_args(argv)
    reviewed_intent = None
    try:
        if args.reviewed_intent_v5:
            from scripts.emergent_deployment_adapter import (
                DeploymentAdapterError,
                load_release_intent,
            )

            try:
                reviewed_intent = load_release_intent()
            except DeploymentAdapterError as exc:
                raise FrontendBuildIdentityError(
                    f"reviewed v5 release intent is invalid: {exc}"
                ) from exc
            if reviewed_intent["source_git_sha"] != args.expected_git_sha.strip().lower():
                raise FrontendBuildIdentityError(
                    "reviewed v5 release intent source differs from expected Git SHA"
                )
        identity = read_frontend_build_identity(
            expected_git_sha=args.expected_git_sha.strip().lower(),
            require_git_source=not args.reviewed_intent_v5,
        )
        reproducibility = read_frontend_reproducibility_proof(
            frontend_build=identity
        )
        if reviewed_intent is not None and (
            identity != reviewed_intent["frontend_build"]
            or reproducibility
            != reviewed_intent["frontend_reproducibility"]
        ):
            raise FrontendBuildIdentityError(
                "retained frontend evidence differs from reviewed v5 release intent"
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
