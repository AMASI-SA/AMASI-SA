"""Run the Shipping suite belonging to the checked-out PR source, fail closed."""
import os
from pathlib import Path
import subprocess
import sys


def main(tests):
    bootstrap = "backend/tests/test_shipping_snapshot_bootstrap.py"
    if not Path(bootstrap).is_file():
        if os.environ.get("BOOTSTRAP_REQUIRED") != "false":
            raise SystemExit("Shipping snapshot scope changed or unknown but its required test is missing")
        print("Snapshot bootstrap test not applicable: absent at tested HEAD and its owned scope is unchanged", flush=True)
        tests = [name for name in tests if name != bootstrap]
    elif bootstrap not in tests:
        tests = [bootstrap, *tests]
    if not tests:
        raise SystemExit("No Shipping regression tests selected")
    missing = [name for name in tests if not Path(name).is_file()]
    if missing:
        raise SystemExit(f"Required Shipping regression files missing: {missing}")
    subprocess.run([sys.executable, "-m", "pytest", "--noconftest", "--asyncio-mode=auto", "-q", "-ra", *tests], check=True)


if __name__ == "__main__":
    main(sys.argv[1:])
