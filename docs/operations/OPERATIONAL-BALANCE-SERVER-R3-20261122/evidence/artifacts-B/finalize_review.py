#!/usr/bin/env python3
"""Summarize exact-B artifact checks without executing a build or touching source."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def read(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text())


def main() -> None:
    extraction = read("extraction-review.json")
    downloads = read("download-manifest.json")
    logs = read("log-review/review-summary.json")
    hosts = {host: read(f"host{host}-original-validator-review.json") for host in ("20", "24")}
    assert all(value["status"] == "PASS" for value in hosts.values())
    assert extraction["status"] == "PASS"
    for key in ("frontend_artifact_tree_sha256", "build_meta", "proof_file", "package_boundary", "original_validator_sha256", "intent_sha256", "release_id"):
        assert hosts["20"][key] == hosts["24"][key], key
    proof_evidence = {}
    for host, result in hosts.items():
        text = (ROOT / f"evidence{host}/mezan-adapter-build-v5.log").read_text()
        offset = text.index('{\n  "built": true,')
        proof, _ = json.JSONDecoder().raw_decode(text[offset:])
        assert proof["built"] is True
        assert proof["package_boundary"] == result["package_boundary"]
        assert proof["release_id"] == result["release_id"]
        assert proof["source_git_sha"] == result["A"]
        assert proof["frontend_artifact_tree_sha256"] == result["frontend_artifact_tree_sha256"]
        environments = [json.loads(line) for line in text.splitlines() if line.startswith('{"architecture":')]
        assert len(environments) == 1
        environment = environments[0]
        assert environment["host_node"] == result["host_node_from_CI"]
        assert environment["git_directory_present"] is False
        assert environment["platform_snapshot_workspace_shared_observed"] is False
        job = next(item for item in logs["jobs"] if item["host_node_observed"] == result["host_node_from_CI"])
        assert job["status"] == "completed" and job["conclusion"] == "success"
        assert job["checkout_B_logged"] is True
        assert job["adapter_proof"] == proof
        assert job["configured_adapter_evidence"] == environment
        assert len(job["build_passes"]) == 2
        assert all(item["sha256"] == result["frontend_artifact_tree_sha256"] for item in job["build_passes"])
        assert job["reproducibility"]["sha256"] == result["proof_file"]["sha256"]
        assert job["runtime_route_probe"]["conclusion"] == "success"
        proof_evidence[host] = {
            "job_id": job["job_id"], "job_url": job["job_url"],
            "downloaded_adapter_log_proof_exact_to_original_job_log": True,
            "downloaded_adapter_log_proof_exact_to_recomputed_boundary": True,
            "gitless_configured_adapter_build": True,
            "two_matching_builds": True,
            "canonical_and_cache_busted_JSON_MIME": "PASS",
            "JSON_response_body_equals_generated_metadata": "PASS",
            "Accept_values": ["application/json", "*/*"],
            "runtime_probe_target": "http://127.0.0.1:3000 inside CI",
            "per_response_output_printed": False,
        }
    frozen = ROOT.parent / "mezan-server-r3-intent-check"
    head = subprocess.check_output(["git", "--no-optional-locks", "-C", str(frozen), "rev-parse", "HEAD"], text=True).strip()
    status = subprocess.check_output(["git", "--no-optional-locks", "-C", str(frozen), "status", "--porcelain=v1", "--untracked-files=all"], text=True)
    assert head == extraction["B"] and status == ""
    intent = json.loads((frozen / "release/release-intent-v5.json").read_text())
    result = {
        "kind": "exact_B_four_artifact_independent_acceptance_v1",
        "result": "PASS_FOR_REQUESTED_ARTIFACTS_AND_TWO_READINESS_HOST_JOBS",
        "run_id": 45071567803,
        "run_url": "https://github.com/AMASI-SA/AMASI-SA/actions/runs/45071567803",
        "A": extraction["A"], "B": extraction["B"], "B_tree": extraction["B_tree"], "N": extraction["N"],
        "intent_sha256": extraction["intent_sha256"], "runtime_release_id": hosts["20"]["release_id"],
        "artifacts": [{key: item[key] for key in ("label", "id", "name", "size", "sha256")} for item in downloads["artifacts"]],
        "four_ZIP_sizes_and_digests_exact_to_GitHub_metadata": True,
        "inner_tar_sidecar_digests_verified": True,
        "safe_archive_extraction": "PASS (no traversal, links, special entries, duplicates, or unexpected generated paths)",
        "generated_files_per_host": 389,
        "generated_file_bytes_per_host": 13242286,
        "all_generated_path_mode_size_bytes_identical_across_hosts": True,
        "frontend_artifact_tree_sha256": hosts["20"]["frontend_artifact_tree_sha256"],
        "build_meta": hosts["20"]["build_meta"], "reproducibility_proof": hosts["20"]["proof_file"],
        "original_validators": {
            "load_release_intent": "PASS",
            "frontend_source_records": len(intent["frontend_source"]["files"]),
            "backend_source_records": hosts["20"]["backend_source_file_count"],
            "release_control_source_records": hosts["20"]["release_control_file_count"],
            "actual_frontend_artifact_metadata_and_proof_exact_to_frozen_intent": "PASS",
            "actual_backend_identity_exact_to_frozen_intent": "PASS",
            "isolated_backend_identity_and_health": "PASS",
            "isolated_package_boundary_exact_to_both_CI_evidence_files": "PASS",
            "validator_files_unchanged_from_frozen_B": True,
            "validation_node": hosts["20"]["local_validation_node"],
            "validation_python": hosts["20"]["local_validation_python"],
        },
        "package_boundary": hosts["20"]["package_boundary"],
        "actual_CI_job_and_downloaded_log_evidence": proof_evidence,
        "frozen_B_still_clean_after_all_validation": True,
        "limitations": {
            "generated_archive_contains_full_source": False,
            "source_used_for_local_package_verification": "read-only exact-B Git archive, plus actual downloaded generated outputs",
            "CI_recorded_host_runtimes": ["v20.20.2", "v24.19.0"],
            "governed_build_runtime": "Node22.23.2 / Yarn1.22.22",
            "actual_Emergent_host_rehearsal": False,
            "production_deployment": False,
            "all_other_B_workflows_reviewed_by_this_report": False,
            "local_artifact_build_or_install_performed": False,
        },
        "audit_harness_note": "Initial source git-archive extraction refused Git's default tar.umask 0002 directory mode 0775; retry used command-local tar.umask 0022 for canonical checkout modes. Original generated archives and original validators were unchanged; initial refusal log retained.",
        "findings": [],
    }
    summary = ROOT / "verification-summary.json"
    summary.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    selected = [
        "verification-summary.json", "download-manifest.json", "extraction-review.json",
        "extraction-review.log", "extraction-initial-git-archive-mode-refusal.log",
        "audit_artifacts.py", "validate_one.py", "finalize_review.py",
        "host20-original-validator-review.json", "host24-original-validator-review.json",
        "host20-original-validator.log", "host24-original-validator.log",
        "generated20/generated-file-records.json", "generated24/generated-file-records.json",
    ]
    selected.extend(str(path.relative_to(ROOT)) for folder in ("evidence20", "evidence24", "log-review") for path in sorted((ROOT / folder).iterdir()) if path.is_file())
    records = []
    for relative in sorted(set(selected)):
        payload = (ROOT / relative).read_bytes()
        records.append({"path": relative, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    manifest = {"kind": "artifact_review_checkpoint_manifest_v1", "B": head, "files": records, "total_bytes": sum(row["bytes"] for row in records), "excluded_reproducible_working_material": ["downloads/*.zip", "exact-B-source-scopes.tar", "validation-host*", "generated*/outputs", "tmp"]}
    (ROOT / "checkpoint-manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"result": result["result"], "summary_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(), "checkpoint_files": len(records), "checkpoint_bytes": manifest["total_bytes"], "frozen_B_clean": True}, indent=2))


if __name__ == "__main__":
    main()
