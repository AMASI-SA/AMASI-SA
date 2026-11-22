cd /app && PYTHONDONTWRITEBYTECODE=1 GIT_TERMINAL_PROMPT=0 python -B - <<'PY'
import json
import os
import subprocess
from scripts import production_release_guard as g

EXPECTED = {
    "deployment_git_sha": "06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35",
    "source_git_sha": "063611ff163c90d80cdbf96446a28584fe429846",
    "release_id": "rg5-90a88a6e7ac6a65670274d6180be0338d689f64d91efbf5cdfbbf651ca182bf7",
    "protocol_version": 5,
}
ACTOR = "codex-operational-demand-2069-20261122"

def matches(value):
    return all(value.get(k) == v for k, v in EXPECTED.items())

def show(label, value):
    keys = (*EXPECTED, "actor", "prepared_at", "active",
            "incomplete_lease_directory", "ready_to_publish")
    print(label, json.dumps({k: value[k] for k in keys if k in value}), flush=True)

phase = "preflight"
try:
    if any(k in os.environ for k in (
        "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE", "GIT_DIR",
        "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    )):
        raise RuntimeError("Unexpected CI or Git routing environment")
    if str(g.REPO_ROOT) != "/app":
        raise RuntimeError("Original /app guard required")
    head = subprocess.run(
        ["git", "--no-optional-locks", "rev-parse", "HEAD"],
        text=True, capture_output=True, timeout=30,
        stdin=subprocess.DEVNULL,
    )
    if head.returncode or head.stdout.strip() != EXPECTED["deployment_git_sha"]:
        raise RuntimeError("Shared source changed")
    state = g.status()
    show("GUARD_BEFORE", state)
    if state != {"active": False}:
        raise RuntimeError("Existing or incomplete lease; stop without changing it")

    phase = "prepare"
    lease = g.prepare(ACTOR)
    if not matches(lease) or lease.get("actor") != ACTOR:
        raise RuntimeError("Prepared lease differs from this release")
    show("PREPARED", lease)

    phase = "prepublish"
    ready = g.prepublish()
    if ready.get("ready_to_publish") is not True or not matches(ready):
        raise RuntimeError("Prepublish identity mismatch")
    state = g.status()
    if state != {"active": True, **lease}:
        raise RuntimeError("Own prepared lease changed")
    show("PREPUBLISH_PASSED", ready)
    show("OWN_LEASE_ACTIVE", state)
except Exception as error:
    reason = str(error)
    if reason.startswith("git "):
        reason = "Original Git check failed; remote details omitted"
    print("STOP", phase, type(error).__name__, reason, flush=True)
    try:
        show("GUARD_AFTER_STOP", g.status())
    except Exception:
        print("GUARD_AFTER_STOP unavailable", flush=True)
    print("Do not publish. No lease was manually aborted or cleared.", flush=True)
    raise SystemExit(1)
PY
