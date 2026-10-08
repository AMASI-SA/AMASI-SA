"""Finite child wrapper collecting Linux CPU/I/O; does not launch any server."""
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil


def snapshot(process):
    try:
        cpu = process.cpu_times()
        io = process.io_counters()
        return {"pid": process.pid, "cpu_seconds": cpu.user + cpu.system,
                "read_bytes": io.read_bytes, "write_bytes": io.write_bytes,
                "rss": process.memory_info().rss}
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def main():
    destination, *command = sys.argv[1:]
    if command and command[0] == "--":
        command.pop(0)
    assert command
    mongo = next((p for p in psutil.process_iter() if p.name() == "mongod"), None)
    # Host runner may deny access to container process counters; report missing.
    child = subprocess.Popen(command)
    process = psutil.Process(child.pid)
    with Path(destination).open("w") as stream:
        while child.poll() is None:
            stream.write(json.dumps({"monotonic": time.monotonic(), "client": snapshot(process),
                                     "mongo": snapshot(mongo) if mongo else None}) + "\n")
            stream.flush()
            time.sleep(0.5)
    raise SystemExit(child.returncode)


if __name__ == "__main__":
    main()
