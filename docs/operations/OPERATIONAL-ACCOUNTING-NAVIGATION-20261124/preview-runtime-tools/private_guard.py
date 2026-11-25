"""Process and socket guard inherited by the exact source demand child."""
from __future__ import annotations
import os
from pathlib import Path
import re
import sys
from private_common import MONGO_URL, RuntimeBlocked, load_config

INSTALLED = False


def install():
    global INSTALLED
    if INSTALLED:
        return
    config = load_config(os.environ.get("MEZAN_PRIVATE_RUNTIME_CONFIG", ""))
    mode = os.environ.get("MEZAN_PRIVATE_RUNTIME_MODE")
    if (os.environ.get("MEZAN_PRIVATE_RUNTIME_ACTIVE") != "1" or mode not in {"api", "worker"}
            or os.environ.get("MONGO_URL") != MONGO_URL or os.environ.get("DB_NAME") != config["database_name"]
            or os.environ.get("PYTHON_DOTENV_DISABLED") != "1"
            or os.environ.get("MEZAN_BACKGROUND_WORKER_PROCESS") != ("1" if mode == "worker" else "0")):
        raise RuntimeBlocked("private_process_binding_invalid")
    backend = Path(config["source_root"]) / "backend"
    ports = {27019, 8001} if mode == "api" else {27019}
    resolution_ports = ports | ({8002} if mode == "api" else set())

    def endpoint(address, allowed_ports):
        return (isinstance(address, tuple) and len(address) >= 2 and address[0] in {"127.0.0.1", "::1"}
                and address[1] in allowed_ports)

    def audit(event, args):
        if event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
            if not endpoint(args[-1], ports):
                raise PermissionError("private_loopback_endpoint_only")
        elif event == "socket.getaddrinfo":
            if args[0] not in {"127.0.0.1", "::1", b"127.0.0.1", b"::1"} or int(args[1]) not in resolution_ports:
                raise PermissionError("private_literal_address_only")
        elif event == "socket.bind":
            if mode != "api" or not endpoint(args[-1], {8002}):
                raise PermissionError("private_listener_only")
        elif event == "subprocess.Popen":
            executable, argv, cwd, child_env = args
            valid = (mode == "worker" and isinstance(argv, (list, tuple)) and len(argv) == 7
                     and str(executable) == sys.executable and argv[:3] == [sys.executable, "-B", str(backend / "demand_read_runner.py")]
                     and re.fullmatch(r"[a-f0-9]{64}", str(argv[3])) and all(re.fullmatch(r"[a-f0-9]{32}", str(v)) for v in argv[4:6])
                     and re.fullmatch(r"[0-9]{1,15}\.[0-9]{9}", str(argv[6])) and str(cwd) == str(backend)
                     and child_env is None)
            if not valid:
                raise PermissionError("private_demand_child_only")
        elif event in {"os.system", "os.exec", "os.posix_spawn", "os.posix_spawnp"}:
            raise PermissionError("private_process_launch_forbidden")
        elif event == "import" and args[0] in {"server", "background_worker_runner", "operational_balance_automation_runner"}:
            raise ImportError("private_full_server_or_scheduler_forbidden")
        elif event == "open" and isinstance(args[0], (str, bytes)) and Path(os.fsdecode(args[0])).name == ".env":
            raise PermissionError("private_dotenv_forbidden")
    sys.addaudithook(audit)
    INSTALLED = True


def require_installed():
    if not INSTALLED:
        raise RuntimeBlocked("private_process_guard_not_installed")
