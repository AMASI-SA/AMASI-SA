#!/usr/bin/env python3
"""Read-only inspection of the local v5 B branch: never deletes, resets or publishes."""
import json
import subprocess
from pathlib import Path

ROOT = Path("/app")
J = "e01d4d3920fc5bf4a7280757ca2030c30b4be9c8"
A = "e19a9987a503365ccc6bf6fadb35c22288075323"
B = "codex/mezan-v5-push-courier-unassign-after018-intent-20261103"
INTENT = "release/release-intent-v5.json"


def git(*args, cwd=ROOT, timeout=15):
    try:
        p = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, type(exc).__name__
    return (p.stdout.strip(), None) if p.returncode == 0 else (None, "git-returned-nonzero")


def main():
    host, _ = git("rev-parse", "HEAD")
    active, _ = git("branch", "--show-current")
    clean, _ = git("status", "--porcelain", "--untracked-files=no")
    prod, _ = git("rev-parse", "--verify", "refs/heads/hotfix/prod-snap-meta-final")
    local, _ = git("rev-parse", "--verify", "refs/heads/" + B)
    remote, remote_error = git("ls-remote", "--heads", "origin", "refs/heads/" + B)
    remote_sha = remote.split()[0] if remote else None
    report = {
        "mode": "READ_ONLY_NO_MUTATIONS",
        "host_branch": active,
        "host_head": host,
        "local_production_sha": prod,
        "expected_production_sha": J,
        "expected_source_sha": A,
        "host_tracked_changes": bool(clean),
        "local_B_branch_exists": bool(local),
        "local_B_sha": local,
        "remote_B_sha": remote_sha,
        "remote_check_error": remote_error,
        "B_branch_name": B,
    }
    if not local:
        report["classification"] = "LOCAL_B_NOT_PRESENT"
    elif local == A:
        report["classification"] = "LOCAL_B_AT_SOURCE_A_NOT_FROZEN"
    else:
        parents, _ = git("rev-list", "--parents", "-n", "1", local)
        changed, _ = git("diff", "--name-only", A, local)
        data_raw, _ = git("show", local + ":" + INTENT)
        try:
            data = json.loads(data_raw) if data_raw else None
        except (ValueError, TypeError):
            data = None
        matches = bool(isinstance(data, dict)
            and data.get("protocol_version") == 5
            and data.get("source_git_sha") == A
            and data.get("source_base_git_sha") == J
            and data.get("branch") == "hotfix/prod-snap-meta-final"
            and isinstance(data.get("runtime_identity"), dict)
            and data["runtime_identity"].get("source_git_sha") == A)
        one_parent = parents == (local + " " + A)
        only_intent = changed == INTENT
        report["local_B_parent_is_source_A"] = one_parent
        report["local_B_changes_only_intent"] = only_intent
        report["local_B_intent_matches_source_and_base"] = matches
        report["local_B_release_id"] = (
            data.get("runtime_identity", {}).get("release_id")
            if isinstance(data, dict) and isinstance(data.get("runtime_identity"), dict)
            else None
        )
        report["classification"] = ("LOCAL_B_SHAPE_VALID_REQUIRES_BUILD_VERIFICATION"
                                    if all((one_parent, only_intent, matches))
                                    else "LOCAL_B_UNEXPECTED_STOP")
    worktrees, _ = git("worktree", "list", "--porcelain")
    attached = []
    if worktrees:
        blocks = worktrees.split("\n\n")
        for block in blocks:
            parsed = {}
            for line in block.splitlines():
                if line.startswith("worktree "):
                    parsed["path"] = line[9:]
                elif line.startswith("branch "):
                    parsed["branch"] = line[7:]
            if parsed.get("branch") == "refs/heads/" + B:
                dirty, _ = git("status", "--porcelain", "--untracked-files=no",
                               cwd=Path(parsed["path"]))
                attached.append({
                    "B_worktree_exists": True,
                    "B_worktree_tracked_changes": bool(dirty),
                })
    report["local_B_worktrees"] = attached
    report["next_step"] = "STOP: provide this inspection result before building, pushing, merging or publishing"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
