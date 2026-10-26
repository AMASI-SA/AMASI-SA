"""REVIEW ONLY: default validates; --prepare stages files, never starts services.

Requires an already extracted, digest-verified CI bundle. Does not download,
extract, install, import application code, access Mongo or alter supervisor.
Do not run until the publisher reviews the final artifact and this script.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import stat
import subprocess

SOURCE = "b381a5929b6541ec8e243572700c9504290b7274"
DEPLOYMENT = "11a52dd933d7269cd80046219dcd93d5a6e62297"
HOST = "agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3"
ORIGIN = "https://salla-analytics.preview.emergentagent.com"
OLD = Path("/app/.worktrees/mz2-supplier-editor-fix-20261025")
TARGET = Path("/root/mezan-preview-supplier-20261026")
CONF = Path("/etc/supervisor/conf.d/supervisord.conf")
CONF_SHA = "2633d21f27509783d575c86827f5a0bff46a430ce65c29bba43cd0ca5642ac78"
AUTH = ("auth.py", "mfa_security.py", "email_otp_policy.py",
        "email_otp_security.py", "passkey_security.py")


def require(ok, label):
    if not ok:
        raise RuntimeError(label)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(["git", "-C", "/app", *args])


def guard():
    result = subprocess.check_output(
        ["python", "-B", "scripts/production_release_guard.py", "status"], cwd="/app")
    require(json.loads(result).get("active") is False, "release lease active")
    require(git("rev-parse", "HEAD").decode().strip() == DEPLOYMENT, "HEAD changed")
    require(git("rev-parse", "origin/hotfix/prod-snap-meta-final").decode().strip()
            == DEPLOYMENT, "remote tracking target changed; fetch separately")


def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def assignment(tree, name):
    found = [n.value for n in tree.body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    require(len(found) == 1, "adapter assignment shape changed")
    return found[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    require(socket.gethostname() == HOST, "unexpected host")
    require(os.getuid() == 0, "expected private root-owned runtime")
    guard()
    bundle_arg = Path(args.bundle_dir)
    require(bundle_arg.is_absolute() and not bundle_arg.is_symlink(), "unsafe bundle path")
    bundle = bundle_arg.resolve(strict=True)
    require(bundle.is_relative_to(Path("/root")), "bundle must be staged outside /app")
    require(not bundle.is_relative_to(TARGET), "input overlaps output")
    require(not TARGET.exists() and not TARGET.is_symlink(), "target already exists")
    require(digest(CONF.read_bytes()) == CONF_SHA, "supervisor changed; review again")
    manifest_bytes = (bundle / "manifest.json").read_bytes()
    require(digest(manifest_bytes) == args.manifest_sha256, "manifest digest mismatch")
    manifest = json.loads(manifest_bytes)
    require(isinstance(manifest, dict) and manifest, "expected path-to-digest manifest")
    for name, expected in manifest.items():
        require(isinstance(name, str) and isinstance(expected, str), "invalid manifest entry")
        rel = PurePosixPath(name)
        require(not rel.is_absolute() and ".." not in rel.parts
                and str(rel) == name and "\\" not in name, "unsafe member path")
        require(rel.parts[0] in {"source", "frontend"}, "unexpected manifest root")
        path = bundle.joinpath(*rel.parts)
        require(path.resolve().is_relative_to(bundle) and not path.is_symlink(), "unsafe member")
        require(path.is_file() and digest(path.read_bytes()) == expected, "member digest mismatch")
    actual = set()
    for top in ("source", "frontend"):
        for path in (bundle / top).rglob("*"):
            require(not path.is_symlink(), "symlink in bundle")
            if path.is_file():
                is_empty_frontend_template = path.relative_to(bundle).as_posix() == "source/frontend/.env"
                if is_empty_frontend_template:
                    require(all(not line.strip() or line.lstrip().startswith("#")
                                for line in path.read_text().splitlines()),
                            "tracked frontend env template contains values")
                require((path.name != ".env" or is_empty_frontend_template)
                        and path.name not in {"release_identity.json", "preview-session-signing.secret"},
                        "unexpected runtime secret or Production identity in artifact")
                actual.add(path.relative_to(bundle).as_posix())
    require(actual == set(manifest), "manifest membership differs from artifact")
    expected_source = {}
    for row in git("ls-tree", "-rz", SOURCE).split(b"\0"):
        if not row:
            continue
        info, name = row.split(b"\t", 1)
        mode, kind, oid = info.split()
        require(kind == b"blob" and mode in (b"100644", b"100755"), "unsupported Git entry")
        expected_source[name.decode()] = oid.decode()
    actual_source = {}
    for name in manifest:
        if name.startswith("source/"):
            data = (bundle / name).read_bytes()
            actual_source[name.removeprefix("source/")] = hashlib.sha1(
                b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    require(actual_source == expected_source, "complete source differs from reviewed Git tree")
    meta = json.loads((bundle / "frontend/build/preview-meta.json").read_text())
    require(meta.get("environment") == "preview" and meta.get("api_origin") == ORIGIN
            and meta.get("source_git_sha") == SOURCE
            and meta.get("external_network") == "denied"
            and meta.get("automatic_workers") == "disabled"
            and meta.get("contains_production_release_identity") is False, "Preview metadata mismatch")
    require(meta.get("index_sha256") == digest((bundle / "frontend/build/index.html").read_bytes()),
            "index digest mismatch")
    old_bundle = OLD / "bundle"
    adapter = (old_bundle / "preview_password_runtime.py").read_text()
    require(digest(adapter.encode()) == (old_bundle / "adapter.sha256").read_text().strip(),
            "old adapter digest mismatch")
    hashes = ast.literal_eval(assignment(ast.parse(adapter), "HASHES"))
    require(set(hashes) == set(AUTH), "auth hash contract changed")
    for name in (*AUTH, "browser_security.py", "server.py"):
        data = (bundle / "source/backend" / name).read_bytes()
        require(data == git("show", SOURCE + ":backend/" + name), "source differs from approved Git")
        if name in AUTH:
            require(digest(data) == hashes[name]
                    and data == (old_bundle / "source/backend" / name).read_bytes(), "auth compatibility changed")
    key = old_bundle / "preview-session-signing.secret"
    env = old_bundle / "source/backend/.env"
    for path in (key, env):
        require(path.is_file() and not path.is_symlink() and path.stat().st_uid == 0,
                "private training input missing or changed")
    require(stat.S_IMODE(key.stat().st_mode) == 0o600, "session key mode changed")
    old_source = json.loads((old_bundle / "frontend/build/preview-meta.json").read_text())["source_git_sha"]
    old_root = str(old_bundle)
    new_root = str(TARGET / "bundle")
    require(adapter.count(old_root) == 1 and adapter.count(old_source) == 1,
            "adapter relocation requires review")
    new_adapter = adapter.replace(old_root, new_root).replace(old_source, SOURCE)
    serve = (OLD / "serve.py").read_text()
    require(serve.count(old_root) == 1, "serve relocation requires review")
    new_serve = serve.replace(old_root, new_root)
    # Parsing only: never import either wrapper or application during preparation.
    compile(new_adapter, "preview_password_runtime.py", "exec")
    compile(new_serve, "serve.py", "exec")
    total = sum((bundle / name).stat().st_size for name in manifest)
    require(shutil.disk_usage("/root").free > total + 200 * 1024 * 1024,
            "insufficient free-space reserve for preserved old runtime")
    if not args.prepare:
        print(json.dumps({"validated_only": True, "source_git_sha": SOURCE,
                          "files": len(manifest), "payload_bytes": total}))
        return
    guard()
    require(digest(CONF.read_bytes()) == CONF_SHA, "supervisor changed before staging")
    TARGET.mkdir(mode=0o700)
    target_bundle = TARGET / "bundle"
    target_bundle.mkdir(mode=0o700)
    # On failure preserve partial evidence. Never remove or overwrite a runtime.
    for name in manifest:
        dest = target_bundle / name
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_write(dest, (bundle / name).read_bytes())
    private_write(target_bundle / "manifest.json", manifest_bytes)
    private_write(target_bundle / "preview_password_runtime.py", new_adapter.encode())
    private_write(target_bundle / "adapter.sha256", (digest(new_adapter.encode()) + "\n").encode())
    private_write(target_bundle / "preview-session-signing.secret", key.read_bytes())
    private_write(target_bundle / "source/backend/.env", env.read_bytes())
    private_write(TARGET / "serve.py", new_serve.encode())
    private_write(TARGET / "supervisor.before", CONF.read_bytes())
    for name, expected in manifest.items():
        require(digest((target_bundle / name).read_bytes()) == expected,
                "staged member readback mismatch")
    require((target_bundle / "preview-session-signing.secret").read_bytes() == key.read_bytes(),
            "session key copy mismatch")
    require((target_bundle / "source/backend/.env").read_bytes() == env.read_bytes(), "training env copy mismatch")
    require(digest(CONF.read_bytes()) == CONF_SHA, "supervisor changed during staging")
    private_write(TARGET / "PREPARED.json", json.dumps({
        "source_git_sha": SOURCE, "deployment_git_sha": DEPLOYMENT,
        "manifest_sha256": args.manifest_sha256, "supervisor_before_sha256": CONF_SHA,
        "serve_sha256": digest(new_serve.encode()), "adapter_sha256": digest(new_adapter.encode()),
        "services_changed": False, "database_accessed": False,
    }, indent=2).encode())
    print(json.dumps({"staged": str(TARGET), "services_changed": False, "database_accessed": False}))


if __name__ == "__main__":
    main()
