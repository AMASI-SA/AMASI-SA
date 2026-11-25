"""Fail closed before imports in this runtime and inherited source children."""
import os
if os.environ.get("MEZAN_PRIVATE_RUNTIME_ACTIVE") == "1":
    try:
        from private_guard import install
        install()
    except BaseException:
        os.write(2, b"private_runtime_guard_blocked\n")
        os._exit(78)
