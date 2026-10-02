import json
from pathlib import Path
root=Path("C:/Users/amasi/mz2-final-acceptance-evidence-20261002/docs/operations/MZ2-FINAL-B2-REBUILD-20261002")
path=root/"DEPLOYMENT-STATUS.json"
s=json.loads(path.read_text())
r=json.loads((root/"APP-PREPARE-OWNER-RECEIPT.json").read_text())
s["checked_at"]="2026-10-02T18:37:32Z"
s["prepare"]="PASS_OWNER_EXECUTION_ONCE"
s["lease"]="ACTIVE_MATCHING_EXACT_B2_IDENTITY; do not prepare again"
s["prepare_receipt"]="APP-PREPARE-OWNER-RECEIPT.json"
s["prepared_at"]=r["prepared_at"]
s["current_gate"]="PREPARED_NOT_PUBLISHED"
s["next_action"]="In the correct Emergent Production project, record the preceding Deployment Succeeded identity/time. Run unmodified production_release_guard.py prepublish from /app once. Only if ready_to_publish=true with the exact deployment/source/release ID, click Re-publish changes once immediately. No automatic retry. Preserve Cloud Build adapter logs if available. After a newer explicit Deployment Succeeded, run guard verify --url https://mezansalla.com once and return output; do not claim completed until all required post-deploy probes pass."
s["prepublish"]="NOT_EXECUTED_AS_OF_OWNER_PREPARE_RECEIPT"
s["deploy"]="NOT_EXECUTED_AS_OF_OWNER_PREPARE_RECEIPT"
path.write_text(json.dumps(s,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"recorded":s["current_gate"],"prepared_at":s["prepared_at"],"no_app_command_executed_by_agent":True}))
