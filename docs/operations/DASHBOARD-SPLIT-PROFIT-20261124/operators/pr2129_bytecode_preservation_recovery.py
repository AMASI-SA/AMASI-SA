#!/usr/bin/env python3
"""PR2129 bounded recovery from one pinned step-78 BLOCKED attempt.

DRAFT: independent review, durable checkpoint and fresh host gates are required.
Starts at exact D. Preserves exactly one inherited cache directory by atomic
same-filesystem no-replace rename, then runs the unchanged strict validator,
canonical adapter and full final checks. No source install, old helper rerun,
clone build, lease creation, publication, service change, deletion or rollback.
Original failed receipts/backups remain read-only. New receipts are private.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path("/app")
BRANCH = "hotfix/prod-snap-meta-final"
J = "30c970807c05e291e2f48b35c84155b271fb5f09"
A = "0b39c456ea17d6e80f824f44b57563764441216c"
B = "ebaae98290f25db5580d888601d236431961d104"
TREE = "d50681f64d3387eb4b6f2137658be9cb11c62bd8"
RELEASE_ID = "rg5-37ff4cc0a52caf321810a8dce1cb6c8f1c27dd8e2f9e5f0a57944ffc90efaf5a"
D = "b651eacf5ded173c4f3224d195ef874b8bcea312"
# START is the historical J baseline used by unchanged lineage/capacity helpers.
# Every current checkout assertion in the new recovery flow explicitly uses D.
START = J
EXPECTED_UNTRACKED = 432
ALLOW_RUNTIME_APPEND = True
OLD_STAGE = Path("/tmp/mezan-pr2122-profit-release-live-mkbhvonf")
OLD_CONTROL = Path("/tmp/mezan-pr2129-operator-o1l3bv1c")
CLONE = OLD_STAGE / "rehearsal"
QUARANTINE = None
GIT_OPTIONS = ["-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "core.hooksPath=/dev/null"]
RUNTIME_PATH = ".emergent/stripped_env_names"
RUNTIME_IDENTITY = (66329, 169292, 420)
LIVE_DB = ".worktrees/p01-acceptance-state-20260919/mongo"
LIVE_LOG = ".worktrees/p01-acceptance-state-20260919/mongo.log"
LIVE_SCOPE = {
    "dbpath": LIVE_DB,
    "db_descendants_policy": "live_writer_managed_no_operator_access",
    "process": {
        "pid": 310, "start_ticks": 197045,
        "cmdline_sha256": "3afd2534eb5355d9b66e77ed088d8d3db819115b34d0e8d2901e052468cd2832",
        "exe": "/usr/bin/mongod", "exe_identity": [3145747, 86633, 493, 0, 0],
    },
    "directory_identities": {
        ".worktrees": [66329, 131957, 493, 0, 0],
        ".worktrees/p01-acceptance-state-20260919": [66329, 133807, 448, 0, 0],
        LIVE_DB: [66329, 133808, 493, 0, 0],
    },
    "logpath": LIVE_LOG,
    "log_identity": [66329, 400929, 420, 0, 0],
    "log_policy": "independent_same_inode_append_only",
}
CLEANUP = (
    "frontend/build", "frontend/.release", "backend/release_identity.json",
    "frontend/node_modules",
)
STAGE: Path | None = None
STEP = "startup"
RECEIPT: dict = {
    "task": "pr2129_bounded_bytecode_preservation_recovery", "status": "RUNNING",
    "initial_head": D, "historical_initial_head": J, "source_sha": A, "intent_sha": B,
    "deployment_sha": D, "tree": TREE, "release_id": RELEASE_ID,
    "external_precondition": "operator verified completed successful exact-D CI",
    "steps": [], "backups": [],
}
ENV = os.environ.copy()
ENV["PYTHONDONTWRITEBYTECODE"] = "1"
ENV["GIT_TERMINAL_PROMPT"] = "0"
ENV["GIT_OPTIONAL_LOCKS"] = "0"


class Blocked(RuntimeError):
    pass


ORIGINAL_HELPER_SHA = '900cbe56a5d40e304c8975e559d4d4f77c5f4d9b0a9b77168d349d30d83a1780'
ORIGINAL_ENVELOPE_PIN = {'bytes': 1589, 'sha256': 'bbeb52d3573a8be9f1b7af30234e03121e9ac9dad1846616a5d8f5da517054a9'}
FAILED_PINS = {'control/bytecode-observation.json': {'bytes': 1783,
                                       'sha256': 'b7edee3210c0769f688cfba35a5ca3ef9ee5a145fde6a566dd3295cf9c8f884d'},
 'control/execution-envelope.json': {'bytes': 1589,
                                     'sha256': 'bbeb52d3573a8be9f1b7af30234e03121e9ac9dad1846616a5d8f5da517054a9'},
 'control/rehearsal-state.json': {'bytes': 500,
                                  'sha256': '14e535cdeef83c8bd13c84ad8c37d3718492103e31c1a1519d340afa888746a5'},
 'control/rehearsal.stderr': {'bytes': 0,
                              'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'control/rehearsal.stdout': {'bytes': 102,
                              'sha256': '761204d9a70e74ab1d443f43bf6d155a0887603f32e49dacf317a96ceb2767f4'},
 'control/run-reviewed-rehearsal.py': {'bytes': 3865,
                                       'sha256': 'acf6fab67c1611a0666566938e407790a7be30c506c8ff8f823470c01a089aa0'},
 'control/runner-launch.json': {'bytes': 339,
                                'sha256': 'f2e9cedf98d9cc0a9cd92e49724b074eb7c5308e7cb3013c1eae3a3e207d942b'},
 'control/runner-launch.log': {'bytes': 0,
                               'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/11-foreign-paths.log': {'bytes': 0,
                                'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/12-foreign-paths.log': {'bytes': 0,
                                'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/13-fetch-production.log': {'bytes': 200,
                                   'sha256': '27574c58cbd7d9e63d4e217f1e95905b3d6d0aa12f0075e6c087dad4054f28ca'},
 'stage/14-remote-head.log': {'bytes': 0,
                              'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/15-fetched-head.log': {'bytes': 0,
                               'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/16-deployment-tree.log': {'bytes': 0,
                                  'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/17-intent-tree.log': {'bytes': 0,
                              'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/18-intent-parents.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/19-deployment-parents.log': {'bytes': 0,
                                     'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/20-base-ancestry.log': {'bytes': 0,
                                'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/21-intent-ancestry.log': {'bytes': 0,
                                  'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/22-initial-base-ancestry.log': {'bytes': 0,
                                        'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/23-source-intent-history.log': {'bytes': 0,
                                        'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/24-source-intent-diff.log': {'bytes': 0,
                                     'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/25-intent-only-diff.log': {'bytes': 0,
                                   'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/26-unchanged-release-controls.log': {'bytes': 0,
                                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/27-tracked-generated-targets.log': {'bytes': 0,
                                            'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/28-current-tracked-paths.log': {'bytes': 0,
                                        'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/29-incoming-paths.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/30-origin-url.log': {'bytes': 0,
                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/31-clone-deployment.log': {'bytes': 313,
                                   'sha256': '15b5c58c08bc54f3c7149134e8b9f87d1d3343de8e9df09ced3301b27a2ac6b9'},
 'stage/32-current-head.log': {'bytes': 0,
                               'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/33-current-branch.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/34-tracked-status.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/35-deployment-tree.log': {'bytes': 0,
                                  'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/36-intent-tree.log': {'bytes': 0,
                              'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/37-intent-parents.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/38-deployment-parents.log': {'bytes': 0,
                                     'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/39-base-ancestry.log': {'bytes': 0,
                                'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/40-intent-ancestry.log': {'bytes': 0,
                                  'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/41-initial-base-ancestry.log': {'bytes': 0,
                                        'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/42-source-intent-history.log': {'bytes': 0,
                                        'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/43-source-intent-diff.log': {'bytes': 0,
                                     'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/44-intent-only-diff.log': {'bytes': 0,
                                   'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/45-unchanged-release-controls.log': {'bytes': 0,
                                             'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/46-tracked-generated-targets.log': {'bytes': 0,
                                            'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/47-clone-untracked.log': {'bytes': 0,
                                  'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/48-validate-intent.log': {'bytes': 214,
                                  'sha256': 'c2181e1f6e2da9de5f3b0d31f05bbd091b8bba122ddafa64c68a06285ecb7173'},
 'stage/49-owned-clone-adapter.log': {'bytes': 217306,
                                      'sha256': 'bd2fb78726627170a4eeaea91de6f42d49784cb495b0c4d7e58e675abcdc9fa0'},
 'stage/50-owned-clone-verify-frontend.log': {'bytes': 134106,
                                              'sha256': '915bb596638766b0aa7daf1e2640f1bee03b26c6276d76bb8d954ab0debfec63'},
 'stage/51-owned-clone-verify-packages.log': {'bytes': 1278,
                                              'sha256': '304b8eab5f7fa61532e65fec0e9577e552f835ce815ea3f2fc52922ba1958efc'},
 'stage/52-owned-clone-full-proof.log': {'bytes': 21,
                                         'sha256': 'd954b4155a758de6093843990bac4251c123b3a7a6f290056e6c24da72d2e9ef'},
 'stage/53-current-head.log': {'bytes': 0,
                               'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/54-current-branch.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/55-tracked-status.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/56-current-head.log': {'bytes': 0,
                               'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/57-current-branch.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/58-tracked-status.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/59-foreign-paths.log': {'bytes': 0,
                                'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/60-foreign-paths.log': {'bytes': 0,
                                'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/61-fetch-production.log': {'bytes': 109,
                                   'sha256': 'a134441c1111df75237fe8e4fcbf52db1937d1433e4c875c433f32ef9528e3ef'},
 'stage/62-remote-head.log': {'bytes': 0,
                              'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/63-fetched-head.log': {'bytes': 0,
                               'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/64-current-tracked-paths.log': {'bytes': 0,
                                        'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/65-incoming-paths.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/66-source-install-paths.log': {'bytes': 0,
                                       'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/67-current-head.log': {'bytes': 0,
                               'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/68-current-branch.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/69-tracked-status.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/70-foreign-paths.log': {'bytes': 0,
                                'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/71-foreign-paths.log': {'bytes': 0,
                                'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/72-current-tracked-paths.log': {'bytes': 0,
                                        'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/73-incoming-paths.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/74-fast-forward.log': {'bytes': 0,
                               'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/75-current-head.log': {'bytes': 0,
                               'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/76-current-branch.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/77-tracked-status.log': {'bytes': 0,
                                 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'stage/78-validate-intent.log': {'bytes': 1748,
                                  'sha256': 'ef39b21930fe7d11c0d63a4c4b118db06565ecddf827f6f0893519f948633e87'},
 'stage/backend--release_identity.json-manifest.json': {'bytes': 156,
                                                        'sha256': '53cd0d560311650f73691169f8ddf712012d20ad90964030bf5f4abd01b43398'},
 'stage/frontend--.release-manifest.json': {'bytes': 231,
                                            'sha256': '1617373d85829db3290f54808c798e5cec7270dcd680702a4b8b4591c4f4ee00'},
 'stage/frontend--build-manifest.json': {'bytes': 71434,
                                         'sha256': '103223057daf8a8cb0d78580091985ba8f6bda79cf7bf09e95ed4b4868de7408'},
 'stage/frontend--node_modules-manifest.json': {'bytes': 13994997,
                                                'sha256': 'a1103751a5cf3c9e02608b417b4bd7a63dcf49ba570c29e92fa3de0204d4b4cd'},
 'stage/operator-helper.py': {'bytes': 43577,
                              'sha256': '900cbe56a5d40e304c8975e559d4d4f77c5f4d9b0a9b77168d349d30d83a1780'},
 'stage/receipt.json': {'bytes': 19530,
                        'sha256': '1a134232998dc0edb7cf91e23a179d87bcebcd80330f61bf31e16cc4adaea854'}}
FOREIGN_PIN = {'bytes': 46225140, 'sha256': 'caec17a1c18ccb3cd4252d4024b27652774fef66d5c13fc21ecc2255ff92c2b4'}
PARENT_IDENTITIES = {'.git': [66329, 131139, 493], 'backend': [66329, 138008, 493]}
CACHE_REL = 'backend/__pycache__'
CACHE_RECORDS = {'backend/__pycache__': {'mode': 493, 'type': 'directory'},
 'backend/__pycache__/frontend_build_identity.cpython-311.pyc': {'bytes': 39622,
                                                                 'mode': 420,
                                                                 'sha256': '489ba93d9df88a1e4f9a7e703f5ab838f93cf60e5f6f60fe10b7eee45361b67b',
                                                                 'type': 'file'},
 'backend/__pycache__/release_protocol_v5.cpython-311.pyc': {'bytes': 80941,
                                                             'mode': 420,
                                                             'sha256': 'c1c09df55f54c1692c24af99f4001907d6717ff06f72b95c21ec58209962143d',
                                                             'type': 'file'}}
CACHE_IDENTITIES = {'backend/__pycache__': [66329, 138446, 493, 0, 0],
 'backend/__pycache__/frontend_build_identity.cpython-311.pyc': [66329, 138447, 420, 0, 0],
 'backend/__pycache__/release_protocol_v5.cpython-311.pyc': [66329, 138448, 420, 0, 0]}
CACHE_MTIMES = {'backend/__pycache__/frontend_build_identity.cpython-311.pyc': 1795618956497225697,
 'backend/__pycache__/release_protocol_v5.cpython-311.pyc': 1795618956509225697}
EXTERNAL_EVIDENCE = {'blocked_zip_sha256': '391c9eed3d8c9295cbabd4a70ecb8d550cbaf193f1de81814d9af1713f81bc6b',
 'cache_observations_zip_sha256': '78910062b49b97549c2b9c8171e5968c00df4d45647a7fb09dc7d0d148bf8af2',
 'checkpoint_commit': '0de0352250ae05a5375da56c1e498d18a6a32aa7',
 'runtime_identity_observation_sha256': '279e677bac2da5af0f20af9816c154d647e6644adcc03488e54254d2b50f75c4',
 'scope': 'Metadata bindings only; full foreign inventory and backup contents remain on host and must pass '
          'current checks.'}

def require(condition: bool, code: str) -> None:
    if not condition:
        raise Blocked(code)


def private_json(path: Path, payload: object) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fsync_directory(path.parent)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_receipt() -> None:
    if STAGE is not None:
        private_json(STAGE / "receipt.json", RECEIPT)


def terminate_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # The leader may already have exited while descendants remain alive.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def run(name: str, argv: list[str], *, cwd: Path = ROOT,
        environment: dict | None = None, timeout: int = 120,
        capture: bool = False) -> bytes:
    global STEP
    STEP = name
    row = {"step": name, "status": "RUNNING", "timeout_seconds": timeout}
    RECEIPT["steps"].append(row)
    save_receipt()
    # Never print subprocess output or inherited environment to the terminal.
    output = tempfile.TemporaryFile() if STAGE is None else os.fdopen(
        os.open(STAGE / (str(len(RECEIPT["steps"])) + "-" + name + ".log"),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
    started = time.monotonic()
    process = None
    try:
        with output:
            process = subprocess.Popen(
                argv, cwd=cwd, env=environment or ENV, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture else output, stderr=output,
                start_new_session=True,
            )
            try:
                data, _ = process.communicate(timeout=timeout)
            except BaseException:
                terminate_group(process)
                raise
            row["exit_code"] = process.returncode
            require(process.returncode == 0, name + "_FAILED")
            row["status"] = "PASS"
            return data or b""
    finally:
        if row["status"] != "PASS":
            row["status"] = "FAILED"
        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save_receipt()


def git(name: str, *args: str, repo: Path = ROOT) -> bytes:
    return run(name, ["git", *GIT_OPTIONS, "-C", str(repo), *args], capture=True)


def text_git(name: str, *args: str, repo: Path = ROOT) -> str:
    return git(name, *args, repo=repo).decode("utf-8").strip()


def assert_checkout(expected: str, *, repo: Path = ROOT) -> None:
    require(text_git("current-head", "rev-parse", "HEAD", repo=repo) == expected, "HEAD_CHANGED")
    require(text_git("current-branch", "symbolic-ref", "--short", "HEAD", repo=repo) == BRANCH,
            "WRONG_BRANCH")
    require(not git("tracked-status", "status", "--porcelain=v1", "--untracked-files=no", repo=repo),
            "TRACKED_WORKTREE_DIRTY")


def fetch_expected() -> None:
    git("fetch-production", "fetch", "--no-tags", "--no-auto-maintenance", "origin", BRANCH)
    require(text_git("remote-head", "rev-parse", "origin/" + BRANCH) == D,
            "REMOTE_HEAD_CHANGED")
    require(text_git("fetched-head", "rev-parse", "FETCH_HEAD") == D,
            "FETCH_HEAD_CHANGED")


def file_fingerprint(path: Path) -> dict:
    before = path.lstat()
    result = {"mode": stat.S_IMODE(before.st_mode)}
    if stat.S_ISLNK(before.st_mode):
        value = os.fsencode(os.readlink(path))
        result.update(type="symlink", bytes=len(value), sha256=hashlib.sha256(value).hexdigest())
    elif stat.S_ISDIR(before.st_mode):
        result.update(type="directory")
    elif stat.S_ISREG(before.st_mode):
        digest = hashlib.sha256()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            require(os.fstat(stream.fileno()).st_ino == before.st_ino, "UNTRACKED_FILE_RACED")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        result.update(type="file", bytes=before.st_size, sha256=digest.hexdigest())
    else:
        raise Blocked("UNSUPPORTED_UNTRACKED_ENTRY")
    after = path.lstat()
    require((before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns),
            "UNTRACKED_ENTRY_CHANGED_DURING_READ")
    return result


def live_identity(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]


def assert_live_write_disjoint(*paths: Path) -> None:
    for path in paths:
        require(path.is_absolute(), "LIVE_SCOPE_WRITE_PATH_NOT_ABSOLUTE")
        for candidate in (path, path.resolve(strict=False)):
            for protected in (ROOT / LIVE_DB, ROOT / LIVE_LOG):
                require(candidate != protected and candidate not in protected.parents
                        and protected not in candidate.parents, "WRITE_OVERLAPS_LIVE_FOREIGN_SCOPE")


def assert_live_scope() -> None:
    """Read process/root metadata only; never query, stop, or traverse the DB."""
    process = LIVE_SCOPE["process"]
    proc = Path("/proc") / str(process["pid"])

    def start_ticks() -> int:
        value = (proc / "stat").read_text()
        require(value.split("(", 1)[0].strip() == str(process["pid"]), "LIVE_PROCESS_PID_CHANGED")
        fields = value[value.rfind(")") + 2:].split()
        require(len(fields) > 19 and fields[0] not in {"Z", "X", "x"}, "LIVE_PROCESS_NOT_RUNNING")
        return int(fields[19])

    try:
        require(start_ticks() == process["start_ticks"], "LIVE_PROCESS_START_CHANGED")
        command = (proc / "cmdline").read_bytes()
        require(hashlib.sha256(command).hexdigest() == process["cmdline_sha256"], "LIVE_PROCESS_COMMAND_CHANGED")
        arguments = command.rstrip(b"\0").split(b"\0")
        position = arguments.index(b"--dbpath")
        require(arguments[position + 1] == os.fsencode(ROOT / LIVE_DB), "LIVE_PROCESS_DBPATH_CHANGED")
        require(os.readlink(proc / "exe") == process["exe"], "LIVE_PROCESS_EXECUTABLE_CHANGED")
        for info in ((proc / "exe").stat(), Path(process["exe"]).lstat()):
            require(stat.S_ISREG(info.st_mode) and live_identity(info) == process["exe_identity"],
                    "LIVE_PROCESS_EXECUTABLE_IDENTITY_CHANGED")
        for relative, expected in LIVE_SCOPE["directory_identities"].items():
            path = ROOT / relative
            real_ancestors(path, ROOT)
            info = path.lstat()
            require(stat.S_ISDIR(info.st_mode) and live_identity(info) == expected,
                    "LIVE_DIRECTORY_IDENTITY_CHANGED")
        real_ancestors(ROOT / LIVE_LOG, ROOT)
        log = (ROOT / LIVE_LOG).lstat()
        require(stat.S_ISREG(log.st_mode) and live_identity(log) == LIVE_SCOPE["log_identity"],
                "LIVE_LOG_IDENTITY_CHANGED")
        require(start_ticks() == process["start_ticks"], "LIVE_PROCESS_START_CHANGED")
    except (OSError, ValueError, IndexError) as exc:
        raise Blocked("LIVE_SCOPE_IDENTITY_UNAVAILABLE") from exc
    assert_live_write_disjoint(ROOT / ".git", ROOT / RUNTIME_PATH, *(ROOT / name for name in CLEANUP))
    if STAGE is not None:
        assert_live_write_disjoint(STAGE)


def live_log_fingerprint(prefix_bytes: int | None = None) -> dict:
    """Hash a bounded prefix; appends during this read are allowed, replacement is not."""
    path = ROOT / LIVE_LOG
    real_ancestors(path, ROOT)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and live_identity(before) == LIVE_SCOPE["log_identity"],
            "LIVE_LOG_IDENTITY_CHANGED")
    length = before.st_size if prefix_bytes is None else prefix_bytes
    require(type(length) is int and 0 <= length <= before.st_size, "LIVE_LOG_TRUNCATED")
    digest, left = hashlib.sha256(), length
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        require(stat.S_ISREG(opened.st_mode) and live_identity(opened) == LIVE_SCOPE["log_identity"]
                and opened.st_size >= length, "LIVE_LOG_OPEN_IDENTITY_CHANGED")
        while left:
            value = stream.read(min(1024 * 1024, left))
            require(bool(value), "LIVE_LOG_TRUNCATED_DURING_READ")
            digest.update(value)
            left -= len(value)
        after_fd = os.fstat(stream.fileno())
    after = path.lstat()
    require(all(stat.S_ISREG(info.st_mode) and live_identity(info) == LIVE_SCOPE["log_identity"]
                and info.st_size >= length for info in (after_fd, after)), "LIVE_LOG_CHANGED_DURING_READ")
    return {"type": "file", "mode": stat.S_IMODE(before.st_mode), "bytes": length,
            "sha256": digest.hexdigest()}


def untracked_snapshot() -> dict:
    assert_live_scope()
    def names(*arguments):
        return sorted(os.fsdecode(value).rstrip("/") for value in
                      git("foreign-paths", "ls-files", "--others", *arguments, "-z").split(b"\0") if value)
    entries = names("--exclude-standard")
    ignored = names("--ignored", "--exclude-standard", "--directory")
    require(not any(name == LIVE_DB or name.startswith(LIVE_DB + "/") or name == LIVE_LOG for name in entries),
            "LIVE_SCOPE_NOT_IGNORED")
    require(all(any(name == scope or scope.startswith(name + "/") for name in ignored)
                for scope in (LIVE_DB, LIVE_LOG)), "LIVE_SCOPE_MISSING_FROM_IGNORED_INVENTORY")
    records = {}

    def generated(relative):
        return any(relative == item or relative.startswith(item + "/") for item in CLEANUP)

    def visit(path: Path) -> None:
        relative = path.relative_to(ROOT).as_posix()
        require(relative != "." and ".." not in Path(relative).parts, "UNSAFE_FOREIGN_PATH")
        if generated(relative):
            return  # Preserved independently by verified generated-output backups.
        if relative.startswith(LIVE_DB + "/"):
            return  # No reads or writes inside the one reviewed live dbpath.
        real_ancestors(path, ROOT)
        if relative == LIVE_DB:
            info = path.lstat()
            require(stat.S_ISDIR(info.st_mode) and live_identity(info) == LIVE_SCOPE["directory_identities"][LIVE_DB],
                    "LIVE_DIRECTORY_IDENTITY_CHANGED")
            records[relative] = {"type": "live_directory", "identity": live_identity(info)}
            return
        if relative == LIVE_LOG:
            records[relative] = live_log_fingerprint()
            return
        records[relative] = file_fingerprint(path)
        if records[relative]["type"] == "directory":
            for child in sorted(path.iterdir()):
                visit(child)

    for relative in entries:
        require(not any(relative == item or relative.startswith(item + "/")
                        or item.startswith(relative + "/") for item in CLEANUP),
                "FOREIGN_NONIGNORED_OVERLAPS_ADAPTER_CLEANUP")
    kept_ignored = [name for name in ignored if not generated(name)]
    for relative in sorted(set(entries + kept_ignored)):
        visit(ROOT / relative)
    require(LIVE_DB in records and LIVE_LOG in records, "LIVE_SCOPE_ROOTS_NOT_RECORDED")
    assert_live_scope()
    return {"git_entries": entries, "ignored_entries": kept_ignored, "records": records}


def assert_no_lease(guard: object) -> None:
    require(not os.path.lexists(guard.LEASE_DIR), "ACTIVE_OR_INCOMPLETE_LEASE_DIRECTORY")
    require(not os.path.lexists(guard.LEASE_PATH), "ACTIVE_LEASE")


def preserve_foreign(before: dict) -> None:
    after = untracked_snapshot()
    private_json(STAGE / "foreign-latest.json", after)
    require(after["git_entries"] == before["git_entries"]
            and after["ignored_entries"] == before["ignored_entries"], "FOREIGN_PATH_SET_CHANGED")
    require(set(after["records"]) == set(before["records"]), "FOREIGN_RECORD_SET_CHANGED")
    for name, record in before["records"].items():
        actual = after["records"].get(name)
        if name == LIVE_LOG:
            require(actual["bytes"] >= record["bytes"] and live_log_fingerprint(record["bytes"]) == record,
                    "LIVE_LOG_EXISTING_PREFIX_CHANGED")
            continue
        if not (ALLOW_RUNTIME_APPEND and name == RUNTIME_PATH):
            require(actual == record, "FOREIGN_ENTRY_CHANGED")
            continue
        info = (ROOT / name).lstat()
        require(stat.S_ISREG(info.st_mode)
                and (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)) == RUNTIME_IDENTITY,
                "RUNTIME_METADATA_IDENTITY_CHANGED")
        if actual == record:
            continue
        require(record["type"] == actual.get("type") == "file"
                and actual["mode"] == record["mode"] and actual["bytes"] >= record["bytes"],
                "RUNTIME_METADATA_NOT_APPEND_ONLY")
        descriptor = os.open(ROOT / name, os.O_RDONLY | os.O_NOFOLLOW)
        digest, left = hashlib.sha256(), record["bytes"]
        with os.fdopen(descriptor, "rb") as stream:
            while left:
                value = stream.read(min(1024 * 1024, left))
                require(bool(value), "RUNTIME_METADATA_TRUNCATED")
                digest.update(value)
                left -= len(value)
        require(digest.hexdigest() == record["sha256"], "RUNTIME_METADATA_PREFIX_CHANGED")
    RECEIPT["foreign_preservation"] = {
        "nonignored_paths": len(before["git_entries"]), "ignored_paths": len(before["ignored_entries"]),
        "records": len(before["records"]),
        "identical_entries": sum(after["records"][name] == record for name, record in before["records"].items()),
        "append_only_path": RUNTIME_PATH if after["records"].get(RUNTIME_PATH) != before["records"].get(RUNTIME_PATH) else None,
        "live_db_root_identity_preserved": LIVE_DB,
        "live_db_descendant_bytes_or_paths_compared": False,
        "live_log_identity_and_original_prefix_preserved": LIVE_LOG,
        "live_log_original_prefix_bytes": before["records"][LIVE_LOG]["bytes"],
        "live_log_original_prefix_sha256": before["records"][LIVE_LOG]["sha256"],
        "live_log_latest_observed_bytes": after["records"][LIVE_LOG]["bytes"],
    }
    save_receipt()


def generated_manifest(root: Path) -> dict:
    records = {}
    def visit(path):
        key = path.relative_to(root).as_posix()
        records[key] = file_fingerprint(path)
        if records[key]["type"] == "directory":
            for child in sorted(path.iterdir()):
                visit(child)
    visit(root)
    return records


def reject_generated_mounts(root: Path) -> None:
    # Device numbers alone do not identify nested mounts on OverlayFS.
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        point = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), line.split()[4])
        require(point != str(root) and not point.startswith(str(root).rstrip("/") + "/"),
                "GENERATED_TARGET_CONTAINS_MOUNT")


def sync_backup_tree(root: Path, records: dict) -> None:
    for name, item in records.items():
        if item["type"] == "file":
            descriptor = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    directories = [root / name for name, item in records.items() if item["type"] == "directory"]
    for path in sorted(directories, key=lambda value: len(value.parts), reverse=True) + [root.parent]:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def validate_intent(repo: Path) -> None:
    code = (
        "from scripts.emergent_deployment_adapter import load_release_intent; "
        "i=load_release_intent(); "
        f"assert i['source_git_sha']=={A!r}; "
        f"assert i['source_base_git_sha']=={J!r}; "
        f"assert i['runtime_identity']['release_id']=={RELEASE_ID!r}; "
        "print('UNCHANGED_INTENT_VALIDATOR_PASS')"
    )
    run("validate-intent", [sys.executable, "-c", code], cwd=repo, environment=build_environment(repo))


def interrupt(signum: int, frame: object) -> None:
    raise Blocked("INTERRUPTED")


def real_ancestors(path: Path, root: Path) -> None:
    require(root == ROOT or root == STAGE or root == CLONE or root == OLD_STAGE or root == OLD_CONTROL, "UNEXPECTED_ANCESTOR_ROOT")
    require(root.is_dir() and not root.is_symlink(), "UNSAFE_PARENT_ROOT")
    parent = path.parent
    while parent != root:
        require(root in parent.parents, "PATH_ESCAPES_ROOT")
        require(parent.is_dir() and not parent.is_symlink(), "SYMLINK_OR_MISSING_PARENT")
        parent = parent.parent


def free_space(path: Path) -> dict:
    value = os.statvfs(path)
    return {"bytes": value.f_bavail * value.f_frsize, "inodes": value.f_favail,
            "fragment_bytes": value.f_frsize, "device": path.stat().st_dev}


def storage_measure(roots: list[Path], target: Path) -> dict:
    """Measure without following links; hard-linked foreign extents are not reclaimable.

    Required bytes are normalized to the target allocation unit. Reclaim bytes
    are an estimate for this filesystem, not a promise about OverlayFS/shared
    extents or concurrent writers. Actual adapter success remains the gate.
    """
    block = os.statvfs(target).f_frsize
    device = target.stat().st_dev
    records = {}
    def visit(path):
        if not os.path.lexists(path):
            return
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode),
                "UNSUPPORTED_STORAGE_ENTRY")
        key = (info.st_dev, info.st_ino)
        item = records.setdefault(key, {"seen": 0, "links": info.st_nlink, "device": info.st_dev,
            "allocated": info.st_blocks * 512, "bytes": info.st_size, "directory": stat.S_ISDIR(info.st_mode)})
        item["seen"] += 1
        if stat.S_ISDIR(info.st_mode):
            for child in path.iterdir():
                visit(child)
    for root in roots:
        visit(root)
    # Copying a file may not retain sparsity or hard-link sharing. Charge each
    # destination entry, and never count storage behind a symlink target.
    required = sum(max(block, ((row["bytes"] + block - 1) // block) * block, row["allocated"]) * row["seen"]
                   for row in records.values())
    reclaimable = [row for row in records.values() if row["device"] == device
                   and (row["directory"] or row["seen"] >= row["links"])]
    return {"required_bytes": required, "entries": sum(row["seen"] for row in records.values()),
            "allocated_bytes": sum(row["allocated"] for row in records.values()),
            "reclaimable_bytes": sum(row["allocated"] for row in reclaimable),
            "reclaimable_inodes": len(reclaimable), "target_fragment_bytes": block}


def validate_lineage(repo: Path) -> None:
    require(text_git("deployment-tree", "rev-parse", D + "^{tree}", repo=repo) == TREE, "DEPLOYMENT_TREE_MISMATCH")
    require(text_git("intent-tree", "rev-parse", B + "^{tree}", repo=repo) == TREE, "INTENT_TREE_MISMATCH")
    require(text_git("intent-parents", "show", "-s", "--format=%P", B, repo=repo).split() == [A], "INTENT_PARENT_MISMATCH")
    if D != B:
        require(text_git("deployment-parents", "show", "-s", "--format=%P", D, repo=repo).split() == [J, B],
                "DEPLOYMENT_PARENTS_MISMATCH")
    git("base-ancestry", "merge-base", "--is-ancestor", J, A, repo=repo)
    git("intent-ancestry", "merge-base", "--is-ancestor", B, D, repo=repo)
    git("initial-base-ancestry", "merge-base", "--is-ancestor", START, J, repo=repo)
    require(not git("source-intent-history", "rev-list", "--full-history", J + ".." + A,
                    "--", "release/release-intent-v5.json", repo=repo), "SOURCE_CHANGED_PRIOR_INTENT")
    require(not git("source-intent-diff", "diff", "--name-only", J, A,
                    "--", "release/release-intent-v5.json", repo=repo), "SOURCE_INTENT_BYTES_CHANGED")
    require(git("intent-only-diff", "diff", "--name-only", "-z", A, D, "--", repo=repo)
            == b"release/release-intent-v5.json\0", "NON_INTENT_CHANGE_AFTER_A")
    controls = ("scripts/production_release_guard.py", "scripts/emergent_deployment_adapter.py",
                "scripts/frontend_release_toolchain.py", "scripts/verify_frontend_build.py",
                "frontend/scripts/build-entry.cjs", "frontend/scripts/build-release-frontend.cjs")
    require(not git("unchanged-release-controls", "diff", "--name-only", "-z", J, D, "--", *controls, repo=repo),
            "ORIGINAL_RELEASE_CONTROLS_CHANGED")
    require(not git("tracked-generated-targets", "ls-tree", "-r", "--name-only", "-z", D, "--", *CLEANUP, repo=repo),
            "GENERATED_TARGET_TRACKED")


def check_app_capacity(new_outputs: dict, *, include_source_install: bool) -> None:
    free = free_space(ROOT)
    old = storage_measure([ROOT / name for name in CLEANUP], ROOT)
    source_bytes = source_entries = 0
    if include_source_install:
        changed = [os.fsdecode(value) for value in git("source-install-paths", "diff", "--name-only", "--no-renames", "-z", START, D).split(b"\0") if value]
        for relative in changed:
            source = CLONE / relative
            if os.path.lexists(source):
                info = source.lstat()
                require(stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode), "UNEXPECTED_SOURCE_INSTALL_TYPE")
                source_bytes += max(free["fragment_bytes"], info.st_blocks * 512,
                                    ((info.st_size + free["fragment_bytes"] - 1) // free["fragment_bytes"]) * free["fragment_bytes"])
                source_entries += 1
        require(free["bytes"] >= source_bytes and free["inodes"] >= source_entries,
                "APP_CAPACITY_INSUFFICIENT_FOR_FAST_FORWARD")
    required = new_outputs["required_bytes"] + source_bytes
    available = free["bytes"] + old["reclaimable_bytes"]
    row = {"phase": "before-ff" if include_source_install else "before-app-adapter",
           "fresh_free": free, "current_generated": old, "clone_measured_generated": new_outputs,
           "source_install_upper_bound_bytes": source_bytes, "required_bytes": required,
           "available_after_adapter_cleanup_estimate_bytes": available,
           "calculation": "fresh free + measured reclaimable inherited outputs versus measured clone output allocation + changed-source bound",
           "limitations": "No assumed old capacity; shared/OverlayFS extents and concurrent activity can affect actual free space; adapter must still pass."}
    RECEIPT.setdefault("capacity_checks", []).append(row)
    save_receipt()
    require(available >= required and free["inodes"] + old["reclaimable_inodes"] >= new_outputs["entries"] + source_entries,
            f"APP_REBUILD_CAPACITY_INSUFFICIENT_REQUIRED_{required}_AVAILABLE_{available}")


def build_environment(repo: Path) -> dict:
    environment = ENV.copy()
    for name in tuple(environment):
        if name.startswith("GITHUB_") or name in {"MEZAN_RELEASE_BOOTSTRAP_INTENT", "PYTHONHOME", "PYTHONPATH"}:
            environment.pop(name)
    environment.update(REACT_APP_BACKEND_URL="https://mezansalla.com", CI="false",
                       XDG_CACHE_HOME=str(STAGE / "cache"), TMPDIR=str(STAGE / "tmp"),
                       PYTHONPATH=str(repo / "backend") + ":" + str(repo))
    return environment


def run_original_adapter(repo: Path, label: str) -> None:
    assert_live_scope()
    assert_live_write_disjoint(*(repo / name for name in CLEANUP))
    environment = build_environment(repo)
    RECEIPT.setdefault("adapter_runs", []).append({"label": label, "repo": str(repo),
        "started_capacity": {"repo": free_space(repo), "owned_stage": free_space(STAGE)}})
    save_receipt()
    run(label + "-adapter", ["yarn", "build"], cwd=repo / "frontend", environment=environment, timeout=2400)
    run(label + "-verify-frontend", [sys.executable, "scripts/verify_frontend_build.py",
        "--expected-git-sha", A, "--reviewed-intent-v5"], cwd=repo, environment=environment, timeout=180)
    run(label + "-verify-packages", [sys.executable, "scripts/emergent_deployment_adapter.py", "verify-packages"],
        cwd=repo, environment=environment, timeout=240)
    intent = json.loads((repo / "release/release-intent-v5.json").read_text())
    identity = json.loads((repo / "backend/release_identity.json").read_text())
    require(identity == intent["runtime_identity"] and identity["release_id"] == RELEASE_ID
            and identity["source_git_sha"] == A, "RUNTIME_IDENTITY_MISMATCH")
    run(label + "-full-proof", [sys.executable, "-c",
        "from frontend_build_identity import read_frontend_build_identity, read_frontend_reproducibility_proof; "
        "from scripts.emergent_deployment_adapter import load_release_intent; "
        "i=load_release_intent(); f=read_frontend_build_identity(expected_git_sha=i['source_git_sha']); "
        "assert f==i['frontend_build']; "
        "assert read_frontend_reproducibility_proof(frontend_build=f)==i['frontend_reproducibility']; "
        "print('CANONICAL_PROOF_PASS')"], cwd=repo, environment=environment, timeout=180)
    assert_live_scope()
    RECEIPT["adapter_runs"][-1].update(status="PASS", finished_capacity={"repo": free_space(repo), "owned_stage": free_space(STAGE)})
    save_receipt()


def pinned_bytes(path: Path, expected: dict) -> bytes:
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), "UNSAFE_PINNED_EVIDENCE")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino), "PINNED_EVIDENCE_RACED")
        value = stream.read(expected["bytes"] + 1)
        finished = os.fstat(stream.fileno())
    after = path.lstat()
    fields = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_uid,
                           item.st_gid, item.st_size, item.st_mtime_ns)
    require(fields(info) == fields(opened) == fields(finished) == fields(after),
            "PINNED_EVIDENCE_CHANGED")
    require(len(value) == expected["bytes"] and hashlib.sha256(value).hexdigest() == expected["sha256"],
            "PINNED_EVIDENCE_HASH_MISMATCH")
    return value


def assert_recovery_parents() -> None:
    for relative, expected in PARENT_IDENTITIES.items():
        path = ROOT / relative
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and path.resolve(strict=True) == path
                and live_identity(info)[:3] == expected, "RECOVERY_PARENT_CHANGED")
    require((ROOT / ".git").stat().st_dev == (ROOT / "backend").stat().st_dev
            == CACHE_IDENTITIES[CACHE_REL][0], "QUARANTINE_FILESYSTEM_CHANGED")


def mapped_foreign_baseline(before: dict) -> dict:
    require(set(before) == {"git_entries", "ignored_entries", "records"}, "FOREIGN_BASELINE_SCHEMA_CHANGED")
    require(len(before["git_entries"]) == EXPECTED_UNTRACKED
            and len(before["ignored_entries"]) == 48 and len(before["records"]) == 185033,
            "FOREIGN_BASELINE_COUNTS_CHANGED")
    require(before["ignored_entries"].count(CACHE_REL) == 1
            and not any(name == CACHE_REL or name.startswith(CACHE_REL + "/")
                        for name in before["git_entries"]), "CACHE_IGNORED_SCOPE_CHANGED")
    cache_keys = {
        name for name in before["records"]
        if name.startswith("backend/") and ("__pycache__" in Path(name).parts
                                            or Path(name).suffix.lower() in {".pyc", ".pyo"})
    }
    require(cache_keys == set(CACHE_RECORDS), "CACHE_BASELINE_PATH_SET_CHANGED")
    require({name: before["records"][name] for name in cache_keys} == CACHE_RECORDS,
            "CACHE_BASELINE_CONTENT_CHANGED")
    require([name for name in before["ignored_entries"]
             if name == CACHE_REL or name.startswith(CACHE_REL + "/")] == [CACHE_REL],
            "CACHE_IGNORED_ENTRY_CHANGED")
    return {
        "git_entries": list(before["git_entries"]),
        "ignored_entries": [name for name in before["ignored_entries"] if name != CACHE_REL],
        "records": {name: record for name, record in before["records"].items() if name not in CACHE_RECORDS},
    }


def inspect_preserved_cache(directory: Path) -> dict:
    real_ancestors(directory, ROOT)
    if QUARANTINE is not None and directory == QUARANTINE:
        container = directory.parent
        require(container.parent == ROOT / ".git" and container.resolve(strict=True) == container
                and stat.S_IMODE(container.lstat().st_mode) == 0o700
                and live_identity(container.lstat()) == RECEIPT["cache_quarantine"]["quarantine_parent_identity"],
                "QUARANTINE_CONTAINER_CHANGED")
    require(directory.resolve(strict=True) == directory, "CACHE_ROOT_NOT_REAL")
    root_info = directory.lstat()
    require(stat.S_ISDIR(root_info.st_mode)
            and live_identity(root_info) == CACHE_IDENTITIES[CACHE_REL], "CACHE_DIRECTORY_IDENTITY_CHANGED")
    expected_names = sorted(Path(name).name for name in CACHE_RECORDS if name != CACHE_REL)
    require(sorted(path.name for path in directory.iterdir()) == expected_names, "CACHE_CHILD_SET_CHANGED")
    records, metadata = {}, {}
    for relative in sorted(CACHE_RECORDS):
        path = directory if relative == CACHE_REL else directory / Path(relative).name
        info = path.lstat()
        require(live_identity(info) == CACHE_IDENTITIES[relative], "CACHE_ENTRY_IDENTITY_CHANGED")
        if relative != CACHE_REL:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and info.st_mtime_ns == CACHE_MTIMES[relative], "CACHE_FILE_METADATA_CHANGED")
        records[relative] = file_fingerprint(path)
        after = path.lstat()
        require(live_identity(after) == CACHE_IDENTITIES[relative], "CACHE_ENTRY_IDENTITY_CHANGED")
        if relative != CACHE_REL:
            require(after.st_mtime_ns == CACHE_MTIMES[relative] and after.st_nlink == 1,
                    "CACHE_FILE_METADATA_CHANGED")
        metadata[relative] = {"identity": live_identity(after), "mtime_ns": after.st_mtime_ns,
                              "ctime_ns": after.st_ctime_ns, "atime_ns": after.st_atime_ns,
                              "links": after.st_nlink, "bytes": after.st_size}
    require(records == CACHE_RECORDS, "CACHE_BYTES_CHANGED")
    require(sorted(path.name for path in directory.iterdir()) == expected_names
            and live_identity(directory.lstat()) == CACHE_IDENTITIES[CACHE_REL], "CACHE_CHANGED_DURING_INSPECTION")
    return {"records": records, "metadata": metadata}


def rename_directory_noreplace(source: Path, destination: Path) -> None:
    require(source.name == "__pycache__" and destination.name == "backend-__pycache__",
            "UNEXPECTED_RENAME_BASENAME")
    require(not os.path.lexists(destination), "QUARANTINE_DESTINATION_EXISTS")
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    require(renameat2 is not None, "ATOMIC_NOREPLACE_RENAME_UNAVAILABLE")
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    source_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        destination_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            source_parent = source.parent.lstat()
            destination_parent = destination.parent.lstat()
            require(live_identity(os.fstat(source_fd)) == live_identity(source_parent)
                    and live_identity(os.fstat(destination_fd)) == live_identity(destination_parent),
                    "RENAME_PARENT_RACED")
            require(source_parent.st_dev == destination_parent.st_dev, "RENAME_CROSSES_FILESYSTEM")
            require(live_identity(source.lstat()) == CACHE_IDENTITIES[CACHE_REL], "RENAME_SOURCE_CHANGED")
            result = renameat2(source_fd, os.fsencode(source.name), destination_fd,
                               os.fsencode(destination.name), 1)  # Linux RENAME_NOREPLACE.
            if result != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            os.fsync(source_fd)
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)


def quarantine_exact_cache() -> None:
    global QUARANTINE
    assert_recovery_parents()
    assert_live_scope()
    source = ROOT / CACHE_REL
    assert_live_write_disjoint(source, ROOT / ".git")
    reject_generated_mounts(source)
    before = inspect_preserved_cache(source)
    relative_records = {"." if name == CACHE_REL else Path(name).name: record
                        for name, record in before["records"].items()}
    sync_backup_tree(source, relative_records)
    parent = Path(tempfile.mkdtemp(prefix="mezan-pr2129-bytecode-quarantine-", dir=ROOT / ".git"))
    parent.chmod(0o700)
    require(parent.resolve(strict=True) == parent and stat.S_IMODE(parent.lstat().st_mode) == 0o700
            and parent.stat().st_uid == os.geteuid(), "UNSAFE_QUARANTINE_PARENT")
    QUARANTINE = parent / "backend-__pycache__"
    require(not os.path.lexists(QUARANTINE), "QUARANTINE_DESTINATION_EXISTS")
    require(parent.stat().st_dev == source.stat().st_dev, "QUARANTINE_FILESYSTEM_CHANGED")
    private_json(parent / "manifest-before.json", {
        "source": str(source), "destination": str(QUARANTINE), "original_foreign_sha256": FOREIGN_PIN["sha256"],
        "preserved_cache": before, "operation": "same_filesystem_atomic_rename_noreplace",
        "bytecode_execution_or_validator_waiver": False,
    })
    fsync_directory(parent)
    fsync_directory(parent.parent)
    RECEIPT["cache_quarantine"] = {"state": "RENAME_ARMED", "source": str(source),
        "destination": str(QUARANTINE), "move_may_have_happened": True, "move_confirmed": False,
        "records": CACHE_RECORDS, "metadata_before": before["metadata"],
        "quarantine_parent_identity": live_identity(parent.lstat()),
        "automatic_restore": False, "bytecode_executed": False}
    save_receipt()  # Durable conservative journal precedes the only rename.
    assert_recovery_parents()
    require(inspect_preserved_cache(source)["records"] == before["records"], "CACHE_CHANGED_BEFORE_RENAME")
    rename_directory_noreplace(source, QUARANTINE)
    RECEIPT["cache_quarantine"].update(state="RENAMED_PENDING_VERIFICATION", move_confirmed=True)
    save_receipt()
    require(not os.path.lexists(source), "CACHE_REAPPEARED_AFTER_RENAME")
    after = inspect_preserved_cache(QUARANTINE)
    require(after["records"] == before["records"], "QUARANTINE_PRESERVATION_FAILED")
    sync_backup_tree(QUARANTINE, relative_records)
    fsync_directory(source.parent)
    fsync_directory(parent)
    fsync_directory(parent.parent)
    private_json(parent / "manifest-after.json", {"source_absent": True, "preserved_cache": after,
        "original_foreign_sha256": FOREIGN_PIN["sha256"],
        "timestamp_limit": "Directory rename can change ctime; file bytes, identity, mode, owner and pinned file mtime remain equal."})
    RECEIPT["cache_quarantine"].update(state="PRESERVED", metadata_after=after["metadata"],
                                      source_absent=True, original_records_preserved=True)
    save_receipt()


def preserve_mapped_foreign(before: dict) -> None:
    require(QUARANTINE is not None and not os.path.lexists(ROOT / CACHE_REL), "CACHE_REAPPEARED")
    require(inspect_preserved_cache(QUARANTINE)["records"] == CACHE_RECORDS, "QUARANTINE_CHANGED")
    expected = mapped_foreign_baseline(before)
    preserve_foreign(expected)  # Original complete equality/append checks, with only the exact mapping.
    require(not os.path.lexists(ROOT / CACHE_REL)
            and inspect_preserved_cache(QUARANTINE)["records"] == CACHE_RECORDS, "CACHE_RELOCATION_CHANGED")
    RECEIPT["mapped_foreign_preservation"] = {
        "original_foreign_sha256": FOREIGN_PIN["sha256"], "original_records": len(before["records"]),
        "current_app_records": len(expected["records"]), "mapped_records": len(CACHE_RECORDS),
        "original_ignored_entries": len(before["ignored_entries"]),
        "current_app_ignored_entries": len(expected["ignored_entries"]),
        "nonignored_paths_unchanged": len(before["git_entries"]), "source": CACHE_REL,
        "destination": str(QUARANTINE), "baseline_replaced": False,
        "db_descendants_accessed": False, "all_other_foreign_rules": "unchanged_v4",
    }
    save_receipt()


def load_failed_attempt() -> tuple[dict, dict]:
    for path in (OLD_STAGE, OLD_STAGE / "backups", OLD_STAGE / "rehearsal", OLD_CONTROL):
        require(path.is_dir() and not path.is_symlink() and path.resolve(strict=True) == path,
                "UNSAFE_RETAINED_EVIDENCE_ROOT")
    require(stat.S_IMODE(OLD_STAGE.stat().st_mode) == 0o700
            and stat.S_IMODE(OLD_CONTROL.stat().st_mode) == 0o700, "RETAINED_EVIDENCE_MODE_CHANGED")
    retained = {}
    for relative, pin in FAILED_PINS.items():
        category, name = relative.split("/", 1)
        root = OLD_STAGE if category == "stage" else OLD_CONTROL
        path = root / name
        real_ancestors(path, root)
        retained[relative] = pinned_bytes(path, pin)
    previous = json.loads(retained["stage/receipt.json"])
    require((previous["status"], previous["failure_code"], previous["failure_step"])
            == ("BLOCKED", "validate-intent_FAILED", "validate-intent"), "WRONG_FAILED_ATTEMPT")
    require((previous["source_sha"], previous["intent_sha"], previous["deployment_sha"],
             previous["initial_head"], previous["tree"], previous["release_id"], previous["stage"])
            == (A, B, D, J, TREE, RELEASE_ID, str(OLD_STAGE)), "FAILED_ATTEMPT_RELEASE_CHANGED")
    require(previous["helper_sha256"] == ORIGINAL_HELPER_SHA
            and previous["envelope_sha256"] == ORIGINAL_ENVELOPE_PIN["sha256"]
            and previous["automatic_rollback"] is False
            and previous["live_foreign_scope"] == LIVE_SCOPE
            and previous["initial_public_status"] == {"active": False}, "FAILED_ATTEMPT_POLICY_CHANGED")
    steps = previous["steps"]
    require(len(steps) == 78 and all(row["status"] == "PASS" and row["exit_code"] == 0 for row in steps[:77])
            and steps[73]["step"] == "fast-forward"
            and steps[77]["step"] == "validate-intent" and steps[77]["status"] == "FAILED"
            and steps[77]["exit_code"] == 1, "FAILED_ATTEMPT_STEPS_CHANGED")
    wrapper = json.loads(retained["control/rehearsal-state.json"])
    require(wrapper["status"] == "COMPLETED" and wrapper["exit_code"] == 2
            and wrapper["helper_invocations"] == 1 and wrapper["helper_may_be_running"] is False
            and wrapper["deployment_sha"] == D and wrapper["helper_sha256"] == ORIGINAL_HELPER_SHA
            and wrapper["envelope_sha256"] == ORIGINAL_ENVELOPE_PIN["sha256"], "FAILED_WRAPPER_NOT_FINAL")
    # The completed parent journal is immutable proof. Any reused/live PID requires root review.
    require(not any(Path("/proc", str(wrapper[key])).exists() for key in ("wrapper_pid", "helper_pid")),
            "ORIGINAL_RUN_PID_PRESENT")
    require(b"FINAL_STATUS=BLOCKED:validate-intent_FAILED" in retained["control/rehearsal.stdout"]
            and not retained["control/rehearsal.stderr"], "FAILED_OUTPUT_CHANGED")
    require(b"Backend runtime source contains prepackaged bytecode: __pycache__"
            in retained["stage/78-validate-intent.log"], "FAILED_REASON_CHANGED")
    require(len(previous["adapter_runs"]) == 1
            and previous["adapter_runs"][0]["label"] == "owned-clone"
            and previous["adapter_runs"][0]["status"] == "PASS"
            and previous["owned_rehearsal"]["status"] == "PASS"
            and previous["owned_rehearsal"]["path"] == str(CLONE)
            and previous["owned_rehearsal"]["source_sha"] == A
            and previous["owned_rehearsal"]["deployment_sha"] == D
            and previous["owned_rehearsal"]["release_id"] == RELEASE_ID, "RETAINED_CLONE_PROOF_CHANGED")
    require(b"CANONICAL_PROOF_PASS" in retained["stage/52-owned-clone-full-proof.log"]
            and b"UNCHANGED_INTENT_VALIDATOR_PASS" in retained["stage/48-validate-intent.log"],
            "RETAINED_CLONE_VALIDATORS_NOT_PASS")
    before = json.loads(pinned_bytes(OLD_STAGE / "foreign-before.json", FOREIGN_PIN))
    mapped_foreign_baseline(before)
    require(before["records"][LIVE_LOG]["bytes"] == 19840286
            and before["records"][LIVE_LOG]["sha256"] == "d6b630d79d972fd409c3213a253e5fa178f2b3e91891ec087fd64a5ea805f8c5",
            "ORIGINAL_LIVE_LOG_PREFIX_CHANGED")
    RECEIPT["failed_attempt"] = {"receipt_sha256": FAILED_PINS["stage/receipt.json"]["sha256"],
        "status": "BLOCKED", "exit_code": 2, "stage": str(OLD_STAGE),
        "canonical_adapter_previously_run": False, "pinned_files_verified": sorted(FAILED_PINS),
        "foreign_before_sha256": FOREIGN_PIN["sha256"], "original_helper_rerun": False}
    RECEIPT["backups"] = previous["backups"]
    RECEIPT["owned_rehearsal"] = previous["owned_rehearsal"]
    save_receipt()
    return previous, before


def verify_prior_backups(*, originals: bool) -> None:
    require([entry["source"] for entry in RECEIPT["backups"]] == list(CLEANUP), "BACKUP_SCOPE_CHANGED")
    for entry in RECEIPT["backups"]:
        relative = entry["source"]
        require(entry["present"] is True and entry["state"] == "COPY_VERIFIED", "PRIOR_BACKUP_NOT_VERIFIED")
        destination = OLD_STAGE / "backups" / relative.replace("/", "--")
        require(entry["destination"] == str(destination), "PRIOR_BACKUP_DESTINATION_CHANGED")
        manifest_name = relative.replace("/", "--") + "-manifest.json"
        expected = json.loads(pinned_bytes(OLD_STAGE / manifest_name, FAILED_PINS["stage/" + manifest_name]))
        require(len(expected) == entry["entries"]
                and sum(row.get("bytes", 0) for row in expected.values()) == entry["bytes"], "PRIOR_BACKUP_MANIFEST_CHANGED")
        real_ancestors(destination, OLD_STAGE)
        reject_generated_mounts(destination)
        require(generated_manifest(destination) == expected, "PRIOR_BACKUP_CONTENT_CHANGED")
        if originals:
            source = ROOT / relative
            real_ancestors(source, ROOT)
            reject_generated_mounts(source)
            require(generated_manifest(source) == expected, "ORIGINAL_GENERATED_OUTPUT_CHANGED")
    RECEIPT.setdefault("prior_backup_verifications", []).append({"all_four_backups_match": True,
        "original_generated_outputs_compared": originals, "retained_stage": str(OLD_STAGE)})
    save_receipt()


def verify_retained_clone() -> dict:
    assert_checkout(D, repo=CLONE)
    validate_lineage(CLONE)
    validate_intent(CLONE)
    environment = build_environment(CLONE)
    run("retained-clone-verify-frontend", [sys.executable, "scripts/verify_frontend_build.py",
        "--expected-git-sha", A, "--reviewed-intent-v5"], cwd=CLONE, environment=environment, timeout=180)
    run("retained-clone-verify-packages", [sys.executable, "scripts/emergent_deployment_adapter.py", "verify-packages"],
        cwd=CLONE, environment=environment, timeout=240)
    intent = json.loads((CLONE / "release/release-intent-v5.json").read_text())
    identity = json.loads((CLONE / "backend/release_identity.json").read_text())
    require(identity == intent["runtime_identity"] and identity["release_id"] == RELEASE_ID
            and identity["source_git_sha"] == A, "RETAINED_CLONE_IDENTITY_CHANGED")
    result = storage_measure([CLONE / name for name in CLEANUP], ROOT)
    require(result == RECEIPT["owned_rehearsal"]["generated_storage"], "RETAINED_CLONE_STORAGE_CHANGED")
    RECEIPT["retained_clone_rechecked"] = {"status": "PASS", "build_repeated": False,
        "generated_storage": result, "source_sha": A, "deployment_sha": D}
    save_receipt()
    return result


def protected_recovery(guard: object) -> None:
    global STAGE
    assert_no_lease(guard)
    assert_checkout(D)
    assert_recovery_parents()
    assert_live_scope()
    require(not os.path.lexists(OLD_CONTROL / "bytecode-recovery-claim.json"), "RECOVERY_ALREADY_CLAIMED")
    STAGE = Path(tempfile.mkdtemp(prefix="mezan-pr2129-bytecode-recovery-", dir="/tmp"))
    STAGE.chmod(0o700)
    for name in ("cache", "tmp"):
        (STAGE / name).mkdir(mode=0o700)
    helper_bytes = Path(__file__).read_bytes()
    RECEIPT.update(stage=str(STAGE), helper_sha256=hashlib.sha256(helper_bytes).hexdigest(),
        live_foreign_scope=LIVE_SCOPE,
        preservation_claim="Original foreign-before baseline remains authoritative; exactly three cache records are mapped to a retained same-filesystem quarantine. Other v4 preservation rules are unchanged.",
        temporary_stage_retention="No cleanup or automatic retry; all old and new stages and quarantine remain retained.")
    for path, value in ((STAGE / "operator-helper.py", helper_bytes),
                        (OLD_CONTROL / "bytecode-recovery-claim.json", json.dumps({
                            "state": "CLAIMED", "pid": os.getpid(), "stage": str(STAGE),
                            "deployment_sha": D, "helper_sha256": RECEIPT["helper_sha256"],
                            "automatic_retry": False}, sort_keys=True).encode() + b"\n")):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(path.parent)
    fsync_directory(STAGE.parent)
    save_receipt()
    print("STAGE_PATH=" + str(STAGE), flush=True)
    previous, before = load_failed_attempt()
    info = (ROOT / RUNTIME_PATH).lstat()
    require(stat.S_ISREG(info.st_mode)
            and (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)) == RUNTIME_IDENTITY,
            "RUNTIME_METADATA_IDENTITY_CHANGED")
    fetch_expected()
    validate_lineage(ROOT)
    require(inspect_preserved_cache(ROOT / CACHE_REL)["records"] == CACHE_RECORDS, "CACHE_PRECHECK_FAILED")
    preserve_foreign(before)
    verify_prior_backups(originals=True)
    new_outputs = verify_retained_clone()
    check_app_capacity(new_outputs, include_source_install=False)
    assert_no_lease(guard)
    assert_checkout(D)
    fetch_expected()
    assert_recovery_parents()
    quarantine_exact_cache()
    validate_intent(ROOT)  # Unchanged strict validator; no derived-bytecode allowance.
    preserve_mapped_foreign(before)
    verify_prior_backups(originals=True)
    check_app_capacity(new_outputs, include_source_install=False)
    assert_no_lease(guard)
    assert_checkout(D)
    run_original_adapter(ROOT, "canonical-app")
    assert_checkout(D)
    preserve_mapped_foreign(before)
    verify_prior_backups(originals=False)
    fetch_expected()
    validate_lineage(ROOT)
    assert_checkout(D)
    assert_no_lease(guard)
    assert_recovery_parents()
    assert_live_scope()
    require(not os.path.lexists(ROOT / CACHE_REL)
            and inspect_preserved_cache(QUARANTINE)["records"] == CACHE_RECORDS, "FINAL_CACHE_PRESERVATION_FAILED")
    RECEIPT.update(status="PASS", lease_created=False, published=False, recalculated=False,
        automatic_rollback=False, automatic_cache_restore=False, fast_forward_performed=False,
        final_capacity={"app": free_space(ROOT), "owned_stage": free_space(STAGE)})
    save_receipt()


def main() -> int:
    global STEP
    try:
        require(sys.flags.optimize == 0 and "PYTHONOPTIMIZE" not in ENV, "PYTHON_OPTIMIZATION_NOT_ALLOWED")
        require(len(sys.argv) == 2 and Path(sys.argv[1]) == OLD_CONTROL / "execution-envelope.json",
                "EXACT_ORIGINAL_ENVELOPE_REQUIRED")
        require(Path.cwd() == ROOT and ROOT.resolve() == ROOT, "RUN_FROM_REAL_APP_ROOT")
        for parent in (ROOT, ROOT / ".git", ROOT / "scripts", ROOT / "frontend", ROOT / "backend", OLD_CONTROL, Path("/tmp")):
            require(parent.is_dir() and not parent.is_symlink() and parent.resolve(strict=True) == parent,
                    "UNSAFE_INITIAL_WORKSPACE_PARENT")
        require(stat.S_IMODE(OLD_CONTROL.stat().st_mode) == 0o700, "UNSAFE_CONTROL_MODE")
        require(not any(name in ENV for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")), "AMBIENT_GIT_REDIRECTION")
        for name in tuple(ENV):
            if name.startswith("GIT_TRACE") or name in {"GIT_CURL_VERBOSE", "PYTHONHOME", "PYTHONPATH"}:
                ENV.pop(name)
        envelope_bytes = pinned_bytes(Path(sys.argv[1]), ORIGINAL_ENVELOPE_PIN)
        envelope = json.loads(envelope_bytes)
        require(set(envelope) == {"source_sha", "source_base_sha", "intent_sha", "deployment_sha", "tree", "release_id",
                                 "initial_head", "expected_untracked_count", "allow_runtime_metadata_append", "live_foreign_scope"},
                "ORIGINAL_ENVELOPE_SCHEMA_CHANGED")
        require((envelope["source_sha"], envelope["source_base_sha"], envelope["intent_sha"], envelope["deployment_sha"],
                 envelope["tree"], envelope["release_id"], envelope["initial_head"], envelope["expected_untracked_count"],
                 envelope["allow_runtime_metadata_append"], envelope["live_foreign_scope"])
                == (A, J, B, D, TREE, RELEASE_ID, J, EXPECTED_UNTRACKED, True, LIVE_SCOPE), "ORIGINAL_ENVELOPE_CHANGED")
        RECEIPT["envelope_sha256"] = ORIGINAL_ENVELOPE_PIN["sha256"]
        assert_recovery_parents()
        assert_live_scope()
        require(text_git("git-root", "rev-parse", "--show-toplevel") == str(ROOT), "WRONG_GIT_ROOT")
        require(text_git("git-directory", "rev-parse", "--absolute-git-dir") == str(ROOT / ".git"),
                "UNSUPPORTED_GIT_DIRECTORY")
        assert_checkout(D)
        guard_path = ROOT / "scripts/production_release_guard.py"
        require(guard_path.is_file() and not guard_path.is_symlink(), "UNSAFE_ORIGINAL_GUARD")
        require(guard_path.read_bytes() == git("original-guard-bytes", "show", J + ":scripts/production_release_guard.py"),
                "ORIGINAL_GUARD_CHANGED")
        status = json.loads(run("initial-public-status", [sys.executable, str(guard_path), "status"],
                                capture=True, timeout=45))
        require(status.get("active") is False and not status.get("incomplete_lease_directory"),
                "ACTIVE_OR_INCOMPLETE_RELEASE_LEASE")
        RECEIPT["initial_public_status"] = {"active": False}
        spec = importlib.util.spec_from_file_location("pr2129_recovery_original_release_guard", guard_path)
        require(spec is not None and spec.loader is not None, "CANNOT_LOAD_ORIGINAL_GUARD")
        guard = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = guard
        spec.loader.exec_module(guard)
        require(guard.REPO_ROOT == ROOT and guard.GIT_DIR == ROOT / ".git", "GUARD_ROOT_MISMATCH")
        signal.signal(signal.SIGINT, interrupt)
        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGALRM, interrupt)
        STEP = "original-operation-lock"
        signal.alarm(30)
        with guard._release_operation_lock():
            signal.alarm(0)
            protected_recovery(guard)
        print("FINAL_STATUS=PASS", flush=True)
        return 0
    except BaseException as exc:
        signal.alarm(0)
        code = str(exc) if isinstance(exc, Blocked) else type(exc).__name__
        RECEIPT.update(status="BLOCKED", failure_step=STEP, failure_code=code, automatic_rollback=False,
                       automatic_cache_restore=False, original_helper_rerun=False)
        try:
            if STAGE is not None:
                RECEIPT["failure_capacity"] = {"app": free_space(ROOT), "owned_stage": free_space(STAGE)}
            save_receipt()
        except BaseException:
            pass
        print("FINAL_STATUS=BLOCKED:" + code, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
