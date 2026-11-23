"""Read-only acceptance of the exact PR2082 source-A CI candidate."""
from pathlib import Path
import hashlib
import json
import subprocess
from datetime import datetime, timezone

from scripts.emergent_deployment_adapter import load_candidate_release_intent
from frontend_build_identity import _tracked_frontend_source, validate_frontend_reproducibility_proof

A = "aec203382b387c3812509084e77c2922aa860877"
N = "44122bcc986bd1dff1532b9ad62c0268e899a179"
TREE = "f133f04fd5759c57f5cce195e35ae03ba53ef6da"
ROOT = Path.cwd().resolve()
OUT = Path(__file__).resolve().parent


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


assert git("rev-parse", "HEAD") == A
assert git("rev-parse", "HEAD^{tree}") == TREE
assert git("rev-list", "--parents", "-1", A).split() == [A, N]
assert not git("status", "--porcelain=v1", "--untracked-files=normal")
assert not git("rev-list", "--full-history", f"{N}..{A}", "--", "release/release-intent-v5.json")
assert not git("diff", "--name-only", N, A, "--", "release/release-intent-v5.json")
candidate = OUT / "release-intent-candidate.json"
raw = candidate.read_bytes()
assert len(raw) == 1502688
assert hashlib.sha256(raw).hexdigest() == "59da5ee6a86f3bfa5a1b8446c6aacb8e180144bc15ccaca3e414e1ec23b151ab"
intent = load_candidate_release_intent(candidate, source_git_sha=A, source_base_git_sha=N)
source = _tracked_frontend_source(repo_root=ROOT, frontend_root=ROOT / "frontend")
assert source == intent["frontend_source"]
validate_frontend_reproducibility_proof(
    frontend_build=intent["frontend_build"], proof=intent["frontend_reproducibility"]
)
assert intent["branch"] == "hotfix/prod-snap-meta-final"
assert intent["frontend_build"]["toolchain"] == {"node": "22.23.2", "yarn": "1.22.22", "vite": "8.2.4"}
assert intent["client_environment"] == {"REACT_APP_BACKEND_URL": {"present": True, "value": "https://mezansalla.com"}}
result = {
    "kind": "pr2082_source_candidate_original_validator_acceptance",
    "accepted_at": datetime.now(timezone.utc).isoformat(),
    "accepted": True,
    "source_git_sha": A,
    "source_base_git_sha": N,
    "source_tree": TREE,
    "candidate_bytes": len(raw),
    "candidate_sha256": hashlib.sha256(raw).hexdigest(),
    "candidate_git_blob": git("hash-object", str(candidate)),
    "runtime_release_id": intent["runtime_identity"]["release_id"],
    "frontend_artifact_tree_sha256": intent["frontend_build"]["artifact_tree_sha256"],
    "build_meta": intent["frontend_build"]["build_meta"],
    "reproducibility_proof": intent["frontend_reproducibility"]["proof_file"],
    "frontend_source_records": source["file_count"],
    "backend_source_records": intent["backend_runtime_source"]["file_count"],
    "release_control_source_records": intent["release_control_source"]["file_count"],
    "original_candidate_loader": "PASS",
    "original_git_frontend_source_rederivation": "PASS",
    "original_backend_and_release_control_git_manifests": "PASS",
    "original_reproducibility_proof_validator": "PASS",
    "inherited_intent_and_full_history": "UNCHANGED",
    "artifact": {"id": 14815305161, "run_id": 45166179398, "zip_bytes": 432232,
                 "zip_sha256": "8963716397b59eccdd4319be1edb8fce3288adcabd57d7a025949400fe81e3f3"},
    "ci_source_gates_final": False,
    "actual_host_rehearsal": False,
    "lease_or_publication": False,
}
(OUT / "source-candidate-acceptance.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
