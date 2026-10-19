"""Read-only Intent provenance proof, shared by all release gates.

The caller MUST authenticate production_base using the existing reviewed release
identity contract. This function proves ancestry, not external approval. It must
never receive an anchor selected by a candidate-controlled policy file.
Checkpoints are on that anchor's first-parent Production spine. Candidate history
is checked across ALL parents, without Git path simplification.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess

INTENT = "release/release-intent-v5.json"


class IntentHistoryError(RuntimeError):
    pass


def verify_intent_history(repo: Path, production_base: str, candidate: str) -> None:
    for sha in (production_base, candidate):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise IntentHistoryError("full immutable Git SHA required")

    def git(*args: str) -> bytes:
        try:
            return subprocess.run(
                ["git", "-C", os.fspath(repo), *args], check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1"},
            ).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise IntentHistoryError("Git history/object proof unavailable") from exc

    if git("rev-parse", "--is-shallow-repository").strip() != b"false":
        raise IntentHistoryError("shallow history is not accepted")
    if git("for-each-ref", "--format=%(refname)", "refs/replace").strip():
        raise IntentHistoryError("replacement history is not accepted")
    graft = Path(os.fsdecode(git("rev-parse", "--git-path", "info/grafts").strip()))
    if not graft.is_absolute():
        graft = repo / graft
    if graft.exists():
        raise IntentHistoryError("grafted history is not accepted")
    git("merge-base", "--is-ancestor", production_base, candidate)
    # Explicitly traverse the complete reachable graph, including trusted-side
    # parents, to reject missing commits even if path simplification would hide it.
    git("rev-list", "--parents", candidate)
    spine = git("rev-list", "--first-parent", "--reverse", production_base).decode().splitlines()
    checkpoints = {sha: index for index, sha in enumerate(spine)}
    records = git("rev-list", "--reverse", "--topo-order", "--parents", candidate,
                  "--not", production_base).decode().splitlines()
    entries: dict[str, tuple[bytes, bytes]] = {}

    def entry(sha: str) -> tuple[bytes, bytes]:
        if sha not in entries:
            raw = git("ls-tree", "-z", sha, "--", INTENT)
            rows = raw.rstrip(b"\0").split(b"\0")
            if len(rows) != 1 or b"\t" not in rows[0]:
                raise IntentHistoryError("Intent missing or ambiguous")
            meta, path = rows[0].split(b"\t", 1)
            mode, kind, oid = meta.split()
            if path != INTENT.encode() or mode != b"100644" or kind != b"blob":
                raise IntentHistoryError("Intent must retain regular non-executable file mode/type")
            # Compare actual bytes as well as the exact tree entry.
            entries[sha] = (meta, git("cat-file", "blob", oid.decode()))
        return entries[sha]

    if entry(candidate) != entry(production_base):
        raise IntentHistoryError("candidate Intent differs from approved Production entry")
    proven: dict[str, int] = {}

    def parent_anchor(sha: str) -> int:
        if sha in proven:
            return proven[sha]
        if sha in checkpoints:
            entry(sha)
            return checkpoints[sha]
        raise IntentHistoryError("candidate lineage enters through an unapproved checkpoint")

    for row in records:
        sha, *parents = row.split()
        if len(parents) not in (1, 2):
            raise IntentHistoryError("unsupported root or multi-parent candidate commit")
        anchors = [parent_anchor(p) for p in parents]
        current = entry(sha)
        parent_entries = [entry(p) for p in parents]
        if all(current == previous for previous in parent_entries):
            proven[sha] = max(anchors)
            continue
        if (len(parents) == 2 and parents[1] in checkpoints
                and checkpoints[parents[1]] > anchors[0]
                and current == parent_entries[1]):
            # Only the second parent may supply a changed Intent; no backward
            # checkpoint import and no hidden edits anywhere on the first side.
            proven[sha] = checkpoints[parents[1]]
            continue
        raise IntentHistoryError("unapproved Intent transition in candidate history")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--production-base", required=True)
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args()
    try:
        verify_intent_history(args.repo, args.production_base, args.candidate)
    except IntentHistoryError as exc:
        parser.exit(1, f"Intent history rejected: {exc}\n")


if __name__ == "__main__":
    main()
