import ast
import gzip
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

root = Path("C:/Users/amasi/mz2-final-acceptance-evidence-20261002/docs/operations/MZ2-FINAL-B2-REBUILD-20261002")
attachment = Path("C:/Users/amasi/.codex/attachments/39fba821-c90e-4b67-95eb-fa18611c389e/نص ملصق.txt")
raw = attachment.read_bytes()
text = raw.decode("utf-8-sig")
decoder = json.JSONDecoder()
objects = []
for match in re.finditer(r"(?m)^\{", text):
    try:
        value, _ = decoder.raw_decode(text[match.start():])
    except ValueError:
        continue
    if isinstance(value, dict):
        objects.append(value)
final = next(x for x in objects if x.get("result") == "PRODUCTION_SOURCE_MATCHED_AND_V5_REHEARSAL_PASS")
built = next(x for x in objects if x.get("built") is True)
intent = json.loads(Path("C:/Users/amasi/mz2-final-b2-rebuild-20261002/release/release-intent-v5.json").read_text())
assert final["deployment_git_sha"] == "78dcf31af73581ceba3677c464c657a0b9b2c4fc"
assert final["TREE"] == "806e935c8ecc3268f98096cd2b477050d235c99e"
assert final["source_A2"] == intent["source_git_sha"]
assert final["release_id"] == built["release_id"] == intent["runtime_identity"]["release_id"]
assert final["status"] == "clean" and final["release_guard"]["active"] is False
assert built["frontend_artifact_tree_sha256"] == intent["frontend_build"]["artifact_tree_sha256"]
assert built["package_boundary"]["frontend"]["build_meta"]["sha256"] == intent["frontend_build"]["build_meta"]["sha256"]
assert built["package_boundary"]["backend"]["health_verified"] is True
assert built["package_boundary"]["backend"]["isolated_verified"] is True
archive = root / "APP-V5-REHEARSAL-OWNER-TRANSCRIPT.txt.gz"
archive.write_bytes(gzip.compress(raw, mtime=0))
assert gzip.decompress(archive.read_bytes()) == raw
proof = {
    "provenance": "Owner-supplied actual /app terminal transcript; parsed and compared to immutable local B2 Intent. Not direct agent terminal execution.",
    "raw_sha256": hashlib.sha256(raw).hexdigest(),
    "raw_bytes": len(raw),
    "lossless_archive": archive.name,
    "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "final": final,
    "adapter": built,
    "warnings": "Temporary missing release_identity.json occurred before adapter materialization. Final equality and isolated health verification passed. No suppression or code change.",
    "production_deployed": False,
}
(root / "APP-V5-REHEARSAL-PROOF.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
status_path = root / "DEPLOYMENT-STATUS.json"
status = json.loads(status_path.read_text())
status["app_rehearsal"] = proof["final"]
status["app_rehearsal_proof"] = "APP-V5-REHEARSAL-PROOF.json"
status["current_gate"] = "APP_V5_REHEARSAL_PASS_AWAIT_PREPARE_EXECUTION"
status["next_action"] = "Execute prepare_exact_b2_once.py via owner-operated /app channel. Calls repository prepare once after exact source/branch/clean/inactive-guard checks; no prepublish/publish. Return compact prepared identity. Stop on failure, no retry."
status["prepare"] = "NOT_EXECUTED_AS_OF_OWNER_REHEARSAL_RESULT"
status["lease"] = "INACTIVE_AS_OF_OWNER_REHEARSAL_RESULT"
status["checked_at"] = proof["verified_at"]
status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
ast.parse((root / "prepare_exact_b2_once.py").read_text())
print(json.dumps({"proof_validation": "PASS", "prepare_helper_syntax": "PASS_NOT_EXECUTED", "raw_sha256": proof["raw_sha256"], "result": final["result"]}))

