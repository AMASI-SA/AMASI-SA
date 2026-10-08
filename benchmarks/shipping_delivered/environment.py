"""Fail-closed benchmark environment proof; localhost synthetic Mongo only."""
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from pymongo import MongoClient


def main():
    uri = os.environ["MZ2_TEST_MONGO_URI"]
    assert uri == "mongodb://127.0.0.1:27018/?replicaSet=shippingbenchmark", uri
    assert platform.system() == "Linux", platform.system()
    assert sys.version_info[:2] == (3, 11), sys.version
    with MongoClient(uri, serverSelectionTimeoutMS=5000) as client:
        hello = client.admin.command("hello")
        build = client.admin.command("buildInfo")
        assert build["version"] == "8.0.12", build["version"]
        assert hello["setName"] == "shippingbenchmark" and hello["isWritablePrimary"]
        proof = {
            "python": sys.version, "platform": platform.platform(),
            "mongo_version": build["version"], "replica_set": hello["setName"],
            "primary": hello["isWritablePrimary"], "mongomock": False,
            "cpu_count": os.cpu_count(), "cpuinfo": Path("/proc/cpuinfo").read_text(),
            "meminfo": Path("/proc/meminfo").read_text(),
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "base": "617858e5d327d7c1cd898e73ef6aa12e726e567f",
            "production_writes": 0,
        }
    changed = subprocess.check_output(["git", "diff", "--name-only", proof["base"], "HEAD"], text=True).splitlines()
    assert all(p.startswith("benchmarks/shipping_delivered/") or p == ".github/workflows/shipping-delivered-benchmark.yml" for p in changed), changed
    proof["changed_files"] = changed
    Path("benchmark-results").mkdir(exist_ok=True)
    Path("benchmark-results/environment.json").write_text(json.dumps(proof, indent=2))
    print(json.dumps({k: proof[k] for k in ("head", "python", "mongo_version", "replica_set", "primary", "cpu_count")}))


if __name__ == "__main__":
    main()
