"""Environment proof only. No application imports, workload, provider or HTTP client."""
import errno
import importlib.util
import json
import os
import platform
import socket
import sys
import time
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from pymongo.write_concern import WriteConcern

URI = "mongodb://127.0.0.1:27018/?replicaSet=shipping_envcheck"
DATABASE = "shipping_delivered_synthetic_envcheck"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(path):
    p = Path(path)
    return p.read_text().strip() if p.exists() else None


def network_proof():
    interfaces = sorted(name for _, name in socket.if_nameindex())
    routes = read("/proc/net/route")
    require(interfaces == ["lo"], "Unexpected external network interface")
    require(len(routes.splitlines()) == 1, "Unexpected IPv4 route")
    ipv6_routes = read("/proc/net/ipv6_route")
    if ipv6_routes:
        require(all(row.split()[-1] == "lo" for row in ipv6_routes.splitlines()),
                "Unexpected IPv6 route")
    status = dict(line.split(":", 1) for line in read("/proc/self/status").splitlines() if ":" in line)
    require(int(status["CapEff"].strip(), 16) == 0, "Network capabilities available")
    require(status["NoNewPrivs"].strip() == "1", "no-new-privileges absent")
    probes = []
    # Documentation addresses only. No DNS lookup or Production connection attempt.
    for family, address in [(socket.AF_INET, ("192.0.2.1", 443)),
                            (socket.AF_INET6, ("2001:db8::1", 443))]:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            code = sock.connect_ex(address)
        require(code in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EADDRNOTAVAIL},
                "Network probe was not rejected by the kernel")
        probes.append({"destination": address[0], "errno": code,
                       "reason": errno.errorcode[code]})
    return {"interfaces": interfaces, "ipv4_routes": routes,
            "ipv6_routes": ipv6_routes, "effective_capabilities": status["CapEff"].strip(),
            "no_new_privileges": True, "external_probes": probes,
            "production_addresses_contacted": [], "dns_lookups_performed": 0}


def mongo_proof():
    # Direct connection is used ONLY to initialize this newly created local mongod.
    direct = MongoClient("mongodb://127.0.0.1:27018/?directConnection=true",
                         serverSelectionTimeoutMS=1000, connectTimeoutMS=1000)
    deadline = time.monotonic() + 60
    while True:
        try:
            direct.admin.command("ping")
            break
        except ConnectionFailure:
            require(time.monotonic() < deadline, "Fresh mongod unavailable")
            time.sleep(1)
    version = direct.admin.command("buildInfo")["version"]
    require(version == "8.0.12", "MongoDB version mismatch")
    direct.admin.command("replSetInitiate", {
        "_id": "shipping_envcheck", "members": [{"_id": 0, "host": "127.0.0.1:27018"}]})
    while True:
        hello = direct.admin.command("hello")
        if hello.get("isWritablePrimary"):
            break
        require(time.monotonic() < deadline, "Replica set did not elect PRIMARY")
        time.sleep(1)
    status = direct.admin.command("replSetGetStatus")
    require(status["myState"] == 1 and len(status["members"]) == 1, "Invalid replica state")
    require(hello["setName"] == "shipping_envcheck", "Replica name mismatch")
    require(hello["hosts"] == ["127.0.0.1:27018"], "External replica member")
    initial_databases = sorted(direct.list_database_names())
    require(set(initial_databases) <= {"admin", "config", "local"}, "Nonempty user database")
    options = direct.admin.command("getCmdLineOpts")["parsed"]
    require(options["net"]["bindIp"] == "127.0.0.1", "Mongo bind address mismatch")
    require(options["storage"]["dbPath"] == "/data/db", "Mongo dbPath mismatch")
    direct.close()
    with MongoClient(URI, serverSelectionTimeoutMS=5000) as client:
        db = client[DATABASE]
        collection = db.create_collection("environment_proof")
        # One synthetic transaction proves real session/commit/rollback support.
        # It does not import PR-B, run a race, or measure performance.
        with client.start_session() as session:
            session.start_transaction(write_concern=WriteConcern("majority"))
            collection.insert_one({"_id": "synthetic-only", "purpose": "environment-verification"}, session=session)
            session.commit_transaction()
            session.start_transaction(write_concern=WriteConcern("majority"))
            collection.update_one({"_id": "synthetic-only"}, {"$set": {"must_rollback": True}}, session=session)
            session.abort_transaction()
        document = collection.find_one({"_id": "synthetic-only"})
        require(document == {"_id": "synthetic-only", "purpose": "environment-verification"},
                "Transaction/rollback proof failed")
        databases = sorted(client.list_database_names())
        require(set(databases) <= {"admin", "config", "local", DATABASE}, "Unexpected database")
    return {"version": version, "replica_set": hello["setName"], "is_writable_primary": True,
            "state": status["myState"], "state_string": status["members"][0]["stateStr"],
            "member": status["members"][0]["name"], "uri": URI, "database": DATABASE,
            "initial_databases": initial_databases, "final_databases": databases,
            "bind_ip": options["net"]["bindIp"], "dbpath": options["storage"]["dbPath"],
            "real_transaction_commit_and_abort": True, "fixture_document_count": 1,
            "synthetic_only": True, "mongomock": False}


def main():
    require(platform.system() == "Linux", "Linux required")
    require(sys.version_info[:2] == (3, 11), "Python 3.11 required")
    require(importlib.util.find_spec("mongomock") is None, "mongomock is forbidden")
    allowed_env = {"PATH", "HOSTNAME", "HOME", "LANG", "GPG_KEY", "PYTHON_VERSION",
                   "PYTHON_SHA256", "PYTHONDONTWRITEBYTECODE", "PYTHON_DOTENV_DISABLED"}
    require(set(os.environ) <= allowed_env, "Unexpected environment variables; values suppressed")
    cpu = {"logical": os.cpu_count(), "affinity": len(os.sched_getaffinity(0)),
           "cgroup_cpu_max": read("/sys/fs/cgroup/cpu.max")}
    require(cpu["affinity"] >= 4, "At least four CPU required for this environment gate")
    if cpu["cgroup_cpu_max"]:
        quota, period = cpu["cgroup_cpu_max"].split()
        require(quota == "max" or int(quota) / int(period) >= 4, "CPU quota below four")
    proof = {"status": "CHECKING", "os": platform.platform(),
             "os_release": read("/etc/os-release"), "python": sys.version,
             "cpu": cpu, "memory": {"meminfo": read("/proc/meminfo"),
                                      "cgroup_limit_bytes": read("/sys/fs/cgroup/memory.max")},
             "environment_keys": sorted(os.environ), "network": network_proof()}
    proof["mongo"] = mongo_proof()
    import pymongo
    proof["pymongo_version"] = pymongo.version
    proof.update(status="PASS", benchmark_executed=False, application_imported=False,
                 provider_calls=0, production_writes=0)
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Avoid dumping driver exceptions/configuration that could contain values.
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__,
                          "reason": str(exc) if isinstance(exc, RuntimeError) else "Environment probe failed",
                          "benchmark_executed": False}))
        sys.exit(1)
