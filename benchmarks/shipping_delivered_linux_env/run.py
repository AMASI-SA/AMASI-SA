"""GitHub-hosted env-only gate for PR-B. This file has no benchmark entrypoint."""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path

BASE = "617858e5d327d7c1cd898e73ef6aa12e726e567f"
DIRECTORY = "benchmarks/shipping_delivered_linux_env/"
WORKFLOW = ".github/workflows/shipping-delivered-linux-env.yml"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MONGO_IMAGE = "mongo:8.0.12"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def verify_changed_files(changed):
    require(bool(changed), "No independent harness changes")
    require(all(p.startswith(DIRECTORY) or p == WORKFLOW for p in changed),
            "Changes outside independent test-only harness")


def validate_container(info, network, image_env):
    host = info["HostConfig"]
    require(host["NetworkMode"] == network, "Network namespace mismatch")
    require(not host["Privileged"], "Privileged container forbidden")
    require(host["ReadonlyRootfs"], "Writable root filesystem forbidden")
    require(host["CapDrop"] == ["ALL"] and not host["CapAdd"], "Capabilities not dropped")
    require("no-new-privileges" in host["SecurityOpt"], "Privilege escalation allowed")
    require(not host["PortBindings"] and not host["PublishAllPorts"], "Published ports forbidden")
    require(host["PidMode"] != "host" and host["IpcMode"] != "host", "Host namespace forbidden")
    require(not host["Binds"] and not host["Devices"], "Host mounts/devices forbidden")
    require(all(m["Type"] == "tmpfs" for m in info["Mounts"]), "Persistent/mounted data forbidden")
    require(sorted(info["Config"]["Env"]) == sorted(image_env), "Inherited host environment forbidden")
    require(info["Config"]["User"] in {"999:999", "65534:65534"}, "Root runtime forbidden")
    require(not host["ExtraHosts"], "Custom host mapping forbidden")
    require(host["NanoCpus"] >= 4_000_000_000, "CPU quota below four")
    networks = info["NetworkSettings"]["Networks"]
    require(set(networks) <= ({"none"} if network == "none" else set()), "Additional network attached")
    return {"id": info["Id"], "image_id": info["Image"], "user": info["Config"]["User"],
            "network_mode": network, "networks": networks,
            "ports": host["PortBindings"], "mounts": info["Mounts"], "tmpfs": host["Tmpfs"],
            "cap_drop": host["CapDrop"], "cap_add": host["CapAdd"],
            "security_options": host["SecurityOpt"], "read_only_rootfs": host["ReadonlyRootfs"],
            "privileged": host["Privileged"], "pid_mode": host["PidMode"], "ipc_mode": host["IpcMode"],
            "cpu_quota_nanocpus": host["NanoCpus"], "memory_limit_bytes": host["Memory"],
            "environment_keys": sorted(v.split("=", 1)[0] for v in info["Config"]["Env"]),
            "host_environment_inherited": False, "bind_mounts": [], "published_ports": []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-only", action="store_true", required=True)
    parser.parse_args()
    require(platform.system() == "Linux", "Run only on GitHub-hosted Linux")
    require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Dedicated GitHub-hosted runner required")
    require(os.environ.get("GITHUB_REPOSITORY") == "AMASI-SA/AMASI-SA", "Repository mismatch")
    require(os.environ.get("GITHUB_REF") == "refs/heads/codex/shipping-delivered-linux-env", "Branch mismatch")
    head = output("git", "-C", str(ROOT), "rev-parse", "HEAD")
    require(head == os.environ["GITHUB_SHA"], "Workflow/source identity mismatch")
    require(not output("git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"), "Dirty source")
    subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", BASE, head], check=True)
    changed = output("git", "-C", str(ROOT), "diff", "--name-only", BASE, head).splitlines()
    verify_changed_files(changed)
    evidence = ROOT / "environment-results"
    evidence.mkdir(exist_ok=False)
    metadata = {"status": "CHECKING", "source_pr": 1300, "source_sha": BASE, "harness_sha": head,
                "changed_files": changed, "benchmark_executed": False, "production_writes": 0,
                "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
                "host_os": platform.platform(), "host_os_release": Path("/etc/os-release").read_text(),
                "runner_image": os.environ.get("ImageVersion"),
                "host_cpu_logical": os.cpu_count(), "host_cpu_affinity": len(os.sched_getaffinity(0)),
                "host_meminfo": Path("/proc/meminfo").read_text(),
                "probe_sha256": hashlib.sha256((HERE / "probe.py").read_bytes()).hexdigest(),
                "online_preparation_scope": ["GitHub source/actions", "Docker official images", "PyPI dependencies"],
                "runtime_scope": "no external network; no application source, host environment, secrets or data mounted"}
    write_json(evidence / "identity.json", metadata)
    suffix = uuid.uuid4().hex[:12]
    mongo, probe, image = "shipping-env-mongo-" + suffix, "shipping-env-probe-" + suffix, "shipping-env-probe:" + suffix
    owned = []
    result = {"status": "FAIL", "benchmark_executed": False}
    try:
        require(len(os.sched_getaffinity(0)) >= 4, "GitHub runner provides fewer than four effective CPUs")
        # Online bootstrap contains only public packages and this tiny probe context.
        # Nothing from Backend, .git, secrets, checkout env or production is passed.
        subprocess.run(["docker", "build", "--pull", "--tag", image, str(HERE)], check=True)
        subprocess.run(["docker", "pull", MONGO_IMAGE], check=True)
        image_info = {tag: json.loads(output("docker", "image", "inspect", tag))[0]
                      for tag in (MONGO_IMAGE, image)}
        metadata["images"] = {tag: {"id": item["Id"], "digests": item["RepoDigests"]}
                              for tag, item in image_info.items()}
        write_json(evidence / "identity.json", metadata)
        common = ["--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
                  "--cpus", "4", "--pids-limit", "256", "--label", "shipping-env-owner=" + suffix,
                  "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m,mode=1777"]
        mongo_id = output("docker", "create", "--name", mongo, "--network", "none", *common,
                          "--user", "999:999", "--memory", "4g",
                          "--tmpfs", "/data/db:rw,noexec,nosuid,size=2g,uid=999,gid=999,mode=0700",
                          "--tmpfs", "/data/configdb:rw,noexec,nosuid,size=16m,uid=999,gid=999,mode=0700",
                          "--entrypoint", "mongod", image_info[MONGO_IMAGE]["Id"],
                          "--port", "27018", "--replSet", "shipping_envcheck", "--bind_ip", "127.0.0.1",
                          "--dbpath", "/data/db", "--wiredTigerCacheSizeGB", "0.5")
        owned.append(mongo)
        def inspect(name):
            return json.loads(output("docker", "inspect", name))[0]
        mongo_proof = validate_container(inspect(mongo), "none", image_info[MONGO_IMAGE]["Config"]["Env"])
        write_json(evidence / "mongo-container.json", mongo_proof)
        # A separate private network namespace already exists and is validated before mongod starts.
        subprocess.run(["docker", "start", mongo], check=True)
        probe_id = output("docker", "create", "--name", probe, "--network", "container:" + mongo_id,
                          *common, "--user", "65534:65534", "--memory", "1g", image_info[image]["Id"])
        owned.append(probe)
        probe_proof = validate_container(inspect(probe), "container:" + mongo_id, image_info[image]["Config"]["Env"])
        write_json(evidence / "probe-container.json", probe_proof)
        subprocess.run(["docker", "start", probe], check=True)
        exit_code = int(output("docker", "wait", probe))
        with (evidence / "probe.stderr.log").open("w", encoding="utf-8") as errors:
            capture = subprocess.run(["docker", "logs", probe], text=True, stdout=subprocess.PIPE, stderr=errors, check=True)
        (evidence / "probe.json").write_text(capture.stdout, encoding="utf-8")
        result = json.loads(capture.stdout)
        require(exit_code == 0 and result["status"] == "PASS", "Isolated environment probe failed")
        # Recheck that no network, mount, privilege, image or credential configuration changed.
        validate_container(inspect(mongo), "none", image_info[MONGO_IMAGE]["Config"]["Env"])
        validate_container(inspect(probe), "container:" + mongo_id, image_info[image]["Config"]["Env"])
        metadata.update(status="PASS", benchmark_executed=False, production_writes=0)
        write_json(evidence / "identity.json", metadata)
        print(json.dumps({"marker": "SHIPPING_DELIVERED_RACE_LINUX_ENV_READY", "source_sha": BASE,
                          "harness_sha": head, "python": result["python"], "cpu": result["cpu"],
                          "mongo": result["mongo"], "benchmark_executed": False}, indent=2))
    except Exception as exc:
        metadata.update(status="FAIL", error_type=type(exc).__name__,
                        reason=str(exc) if isinstance(exc, RuntimeError) else "Environment setup failed; see logs")
        write_json(evidence / "identity.json", metadata)
        raise
    finally:
        for name in reversed(owned):
            with (evidence / (name + ".log")).open("w", encoding="utf-8") as log:
                subprocess.run(["docker", "logs", name], stdout=log, stderr=subprocess.STDOUT, check=False)
            subprocess.run(["docker", "rm", "--force", "--volumes", name], check=True)
        write_json(evidence / "cleanup.json", {"owned_containers_removed": owned,
                   "ephemeral_data_destroyed": True, "benchmark_executed": False})


if __name__ == "__main__":
    main()
