#!/usr/bin/env python3
"""Read-only artifact-byte audit; writes only inside this evidence directory."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent
FROZEN = ROOT.parent / "mezan-server-r3-intent-check"
N = "02cd99e5b2b68c95341c87590abcf572c885e697"
A = "dd3a9ea29fce28e2fa87332cff4a6d46f776e0d3"
B = "9a500a21013a08f750009be656362481986eb208"
B_TREE = "2cbd2842ed2506642a1321a47e35f69a766fcf89"
INTENT_SHA = "0e0c7a6167dae598450358db6a1399b080cb7c69e176d28c5c31a0585ed287dc"
RUN = 45071567803


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(FROZEN), *args])


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")


def normalized(member: tarfile.TarInfo) -> str:
    name = member.name.rstrip("/")
    p = PurePosixPath(name)
    assert name and not p.is_absolute() and ".." not in p.parts, name
    assert "\\" not in name and name == p.as_posix(), name
    assert member.isfile() or member.isdir(), (name, member.type)
    assert not getattr(member, "sparse", None), name
    assert member.mode in (0o644, 0o755), (name, oct(member.mode))
    return name


def permitted_generated(name: str, directory: bool) -> bool:
    if name == "frontend/build" or name.startswith("frontend/build/"):
        return True
    return not directory and name in {
        "frontend/.release/reproducible-build.json",
        "backend/release_identity.json",
        "release/release-intent-v5.json",
    }


def safe_extract(archive: Path, destination: Path, *, generated: bool) -> list[dict]:
    assert destination.is_relative_to(ROOT)
    destination.mkdir(parents=True, exist_ok=True)
    assert not destination.is_symlink()
    records = []
    seen = set()
    total = 0
    with tarfile.open(archive, "r:*") as handle:
        for item in handle:
            name = normalized(item)
            assert name not in seen, name
            seen.add(name)
            assert len(seen) <= 10000
            if generated:
                assert permitted_generated(name, item.isdir()), name
            else:
                assert PurePosixPath(name).parts[0] in {"backend", "frontend", "scripts", ".github", "release"}, name
            total += item.size
            assert total <= 256 * 1024 * 1024
            target = destination / name
            current = destination
            for part in PurePosixPath(name).parts[:-1]:
                current /= part
                assert not current.is_symlink(), str(current)
                current.mkdir(mode=0o755, exist_ok=True)
                assert current.is_dir(), str(current)
            assert not target.is_symlink(), name
            if item.isdir():
                target.mkdir(mode=0o755, exist_ok=True)
                assert target.is_dir()
                target.chmod(0o755)
                continue
            source = handle.extractfile(item)
            assert source is not None
            data = source.read()
            assert len(data) == item.size
            if target.exists():
                assert target.is_file() and target.read_bytes() == data, name
            else:
                with target.open("xb") as stream:
                    stream.write(data)
            target.chmod(item.mode)
            records.append({"path": name, "bytes": len(data), "sha256": sha(data), "mode": f"{item.mode:04o}"})
    return sorted(records, key=lambda row: row["path"])


def frozen_snapshot() -> dict:
    return {
        "head": git("rev-parse", "HEAD").decode().strip(),
        "tree": git("rev-parse", "HEAD^{tree}").decode().strip(),
        "parent": git("rev-parse", "HEAD^").decode().strip(),
        "status_porcelain": git("status", "--porcelain=v1", "--untracked-files=all").decode(),
    }


def main() -> None:
    before = frozen_snapshot()
    assert before == {"head": B, "tree": B_TREE, "parent": A, "status_porcelain": ""}, before
    assert git("diff", "--name-only", A, B).decode().splitlines() == ["release/release-intent-v5.json"]
    assert git("rev-parse", A + "^").decode().strip() == N
    intent_bytes = (FROZEN / "release/release-intent-v5.json").read_bytes()
    assert sha(intent_bytes) == INTENT_SHA
    assert intent_bytes == git("show", B + ":release/release-intent-v5.json")
    metadata = json.loads((ROOT / "download-manifest.json").read_text())
    assert metadata["run_id"] == RUN and metadata["B"] == B
    assert len(metadata["artifacts"]) == 4
    for entry in metadata["artifacts"]:
        assert entry["head_sha"] == B and entry["run_id"] == RUN
        actual = Path(entry["zip_path"]).read_bytes()
        assert len(actual) == entry["size"] and sha(actual) == entry["sha256"]
        for member in entry["members"]:
            data = (ROOT / entry["label"] / member["path"]).read_bytes()
            assert len(data) == member["bytes"] and sha(data) == member["sha256"]

    outputs = {}
    inner = {}
    for host in ("20", "24"):
        folder = ROOT / f"generated{host}"
        archive = folder / "mezan-generated-release-v5.tar.gz"
        sidecar = (folder / "mezan-generated-release-v5.sha256").read_text()
        match = re.fullmatch(r"([0-9a-f]{64})  mezan-generated-release-v5\.tar\.gz\n", sidecar)
        assert match and match[1] == sha(archive.read_bytes())
        records = safe_extract(archive, folder / "outputs", generated=True)
        outputs[host] = records
        write_json(folder / "generated-file-records.json", records)
        assert (folder / "outputs/release/release-intent-v5.json").read_bytes() == intent_bytes
        inner[host] = {
            "archive_sha256": match[1], "sidecar_verified": True,
            "file_count": len(records), "total_file_bytes": sum(row["bytes"] for row in records),
            "records_sha256": sha(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()),
            "exact_frozen_B_intent": True, "safe_extraction": "PASS",
        }
    assert outputs["20"] == outputs["24"], "host generated file bytes/path/modes differ"

    source_archive = ROOT / "exact-B-source-scopes.tar"
    with source_archive.open("wb") as stream:
        subprocess.run([
            "git", "--no-optional-locks", "-c", "tar.umask=0022", "-C", str(FROZEN), "archive", "--format=tar", B,
            "backend", "frontend", "scripts", ".github", "release",
        ], stdout=stream, check=True)
    source_records = None
    for host in ("20", "24"):
        reconstruction = ROOT / f"validation-host{host}"
        current_records = safe_extract(source_archive, reconstruction, generated=False)
        if source_records is None:
            source_records = current_records
        else:
            assert source_records == current_records
        # Only overlay downloaded generated members. Existing tracked intent must match.
        for record in outputs[host]:
            source = ROOT / f"generated{host}/outputs" / record["path"]
            target = reconstruction / record["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            assert not target.is_symlink()
            if target.exists():
                assert target.is_file() and target.read_bytes() == source.read_bytes(), str(target)
            else:
                shutil.copyfile(source, target)
            target.chmod(int(record["mode"], 8))
        assert not (reconstruction / ".git").exists()
        assert not (reconstruction / "frontend/node_modules").exists()
    after = frozen_snapshot()
    assert before == after, "frozen source changed during audit"
    result = {
        "kind": "exact_B_generated_artifact_extraction_v1", "status": "PASS",
        "A": A, "B": B, "B_tree": B_TREE, "N": N, "run_id": RUN,
        "intent_sha256": INTENT_SHA, "zip_count": 4, "zip_digests_reverified": True,
        "hosts": inner, "host_generated_files_identical": True,
        "source_reconstruction": {
            "source": "read-only git archive of exact B; not supplied inside generated artifact",
            "git_archive_tar_umask": "0022 (canonical checkout file modes 0644/0755)",
            "scopes": ["backend", "frontend", "scripts", ".github", "release"],
            "archive_sha256": sha(source_archive.read_bytes()),
            "file_count": len(source_records or []), "unmodified_frozen_source": True,
        },
        "frozen_before": before, "frozen_after": after,
    }
    write_json(ROOT / "extraction-review.json", result)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
