#!/usr/bin/env python3
"""Run original frozen-B validators on one exact source + downloaded output copy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
A = "dd3a9ea29fce28e2fa87332cff4a6d46f776e0d3"
B = "9a500a21013a08f750009be656362481986eb208"
N = "02cd99e5b2b68c95341c87590abcf572c885e697"
RUNTIME = "rg5-b2aa207d2f63107c64d9c0b66ce5cc567aa5ebb074753cf889227de4c448e008"
INTENT_SHA = "0e0c7a6167dae598450358db6a1399b080cb7c69e176d28c5c31a0585ed287dc"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    started = time.monotonic()
    host = sys.argv[1]
    assert host in ("20", "24")
    repo = ROOT / f"validation-host{host}"
    evidence = ROOT / f"evidence{host}"
    assert not (repo / ".git").exists()
    assert not (repo / "frontend/node_modules").exists()
    assert digest(repo / "release/release-intent-v5.json") == INTENT_SHA
    frozen = ROOT.parent / "mezan-server-r3-intent-check"
    original_paths = [
        "scripts/emergent_deployment_adapter.py", "backend/frontend_build_identity.py",
        "backend/release_protocol_v5.py", "backend/release_identity.py",
    ]
    original_hashes = {}
    for relative in original_paths:
        actual = digest(repo / relative)
        assert actual == digest(frozen / relative), relative
        original_hashes[relative] = actual
    adapter = runpy.run_path(str(repo / "scripts/emergent_deployment_adapter.py"), run_name="artifact_original_adapter")
    assert adapter["REPO_ROOT"] == repo
    intent = adapter["load_release_intent"]()
    assert intent["source_git_sha"] == A and intent["source_base_git_sha"] == N
    assert intent["runtime_identity"]["release_id"] == RUNTIME
    equal = adapter["exact_json_equal"]
    from frontend_build_identity import read_frontend_build_identity, read_frontend_reproducibility_proof
    from release_protocol_v5 import validate_backend_runtime_source_manifest, validate_release_control_source_manifest
    from release_identity import read_release_identity, release_health_payload

    frontend = read_frontend_build_identity(expected_git_sha=A, require_git_source=False)
    assert equal(frontend, intent["frontend_build"]), "frontend artifact vs intent"
    proof = read_frontend_reproducibility_proof(frontend_build=frontend)
    assert equal(proof, intent["frontend_reproducibility"]), "proof vs intent"
    backend_source = validate_backend_runtime_source_manifest(
        intent["backend_runtime_source"], backend_root=repo / "backend",
        source_git_sha=A, source_base_git_sha=N, allow_derived_bytecode=False,
    )
    control_source = validate_release_control_source_manifest(
        intent["release_control_source"], repo_root=repo,
        source_git_sha=A, source_base_git_sha=N, allow_missing_github=False,
    )
    raw_identity = json.loads((repo / "backend/release_identity.json").read_text())
    assert equal(raw_identity, intent["runtime_identity"]), "generated backend identity vs intent"
    identity = read_release_identity()
    assert identity["verified_identity_available"] is True
    assert identity["release_id"] == RUNTIME
    health = release_health_payload()
    adapter["_assert_isolated_backend_health"](identity, health)
    environment = json.loads((evidence / "mezan-adapter-environment-v5.json").read_text())
    expected_host = {"20": "v20.20.2", "24": "v24.19.0"}[host]
    assert environment["host_node"] == expected_host
    assert environment["host_yarn"] == "1.22.22"
    assert environment["platform_snapshot_workspace_shared_observed"] is False
    boundary = adapter["verify_package_boundaries"](intent)
    observed_boundary = json.loads((evidence / "mezan-package-boundary-v5.json").read_text())
    assert equal(boundary, observed_boundary), "local reconstructed boundary differs from original CI boundary"
    result = {
        "kind": "exact_B_actual_artifact_original_validator_review_v1", "status": "PASS",
        "host_node_from_CI": expected_host, "run_id": 45071567803,
        "A": A, "B": B, "N": N, "release_id": RUNTIME,
        "intent_sha256": INTENT_SHA, "original_validator_sha256": original_hashes,
        "original_load_release_intent": "PASS",
        "actual_frontend_build_identity_exact_to_intent": "PASS",
        "actual_reproducibility_exact_to_intent": "PASS",
        "actual_backend_identity_exact_to_intent": "PASS",
        "backend_source_manifest_exact": "PASS", "backend_source_file_count": len(backend_source["files"]),
        "release_control_source_manifest_exact": "PASS", "release_control_file_count": len(control_source["files"]),
        "isolated_backend_identity_health": "PASS", "package_boundary_exact_to_CI": "PASS",
        "frontend_artifact_tree_sha256": frontend["artifact_tree_sha256"],
        "build_meta": frontend["build_meta"], "proof_file": proof["proof_file"],
        "package_boundary": boundary,
        "local_validation_node": subprocess.check_output(["node", "--version"], text=True).strip(),
        "local_validation_python": sys.version.split()[0],
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "source_reconstruction": "exact B Git source plus downloaded generated bytes; no build or install",
        "actual_Emergent_host_rehearsal": False, "production_deployment": False,
    }
    output = ROOT / f"host{host}-original-validator-review.json"
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
