import hashlib
import json
import subprocess
from scripts import production_release_guard as g

J = "9b4fb716b430c32073b5ea7abaeaff62646d9633"

def git(*args):
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-C", str(g.REPO_ROOT), *args],
        text=True, timeout=20,
    ).strip()

if git("rev-parse", "HEAD") != J or git("status", "--porcelain", "--untracked-files=no"):
    raise SystemExit("STOP: expected clean current production J")

state = g.status()
print("RELEASE_GUARD", json.dumps({
    "active": state.get("active"),
    "incomplete_lease_directory": state.get("incomplete_lease_directory", False),
}), flush=True)
if state != {"active": False}:
    raise SystemExit("STOP: original guard is not fully inactive")

intent = json.loads(git("show", f"{J}:{g.RELEASE_INTENT_RELATIVE_PATH}"))
expected = intent["frontend_build"]["build_meta"]
print("EXPECTED_METADATA", json.dumps(expected), flush=True)

matched = 0
for accept in ("application/json", "*/*"):
    for cache_bust in (False, True):
        label = f"/build-meta.json accept={accept} cache_bust={cache_bust}"
        response = g._fetch_production(
            g.PRODUCTION_ORIGIN, "/build-meta.json",
            cache_bust=cache_bust, accept=accept, timeout=30,
            allowed_statuses=frozenset({200, 403}),
        )
        print("METADATA_PROBE", json.dumps({
            "accept": accept, "cache_bust": cache_bust,
            "http": response["status"],
            "content_type": response["headers"].get("content-type"),
            "server": response["headers"].get("server"),
            "cf_mitigated": response["headers"].get("cf-mitigated"),
            "bytes": len(response["body"]),
            "sha256": hashlib.sha256(response["body"]).hexdigest(),
        }), flush=True)
        if response["status"] == 200:
            g._assert_build_meta_response(response, label=label)
            g._assert_public_bytes(response, expected=expected, label=label)
            matched += 1

print("METADATA_RESULT", json.dumps({"validated": matched, "required": 4}), flush=True)
if matched != 4:
    raise SystemExit("STOP: metadata diagnosis did not validate all four probes")
