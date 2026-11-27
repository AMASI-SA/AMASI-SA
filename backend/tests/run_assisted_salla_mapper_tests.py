"""Isolated Salla canonical-order mapper test runner (NO production imports).

The real order_engine/__init__.py registers FastAPI routes and all operations
engines; the focused assisted Salla synthetic workflow intentionally installs
only pytest, pydantic and cryptography. Import the PURE source mapper and its
real Pydantic models through a minimal namespace package WITHOUT executing the
unrelated API/router/DB/bootstrap application. This executes the actual mapper
test file unmodified in its own interpreter, so the main pytest suite cannot
inherit the temporary namespace.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


def main() -> int:
    source_dir = Path(__file__).resolve().parents[1] / "order_engine"
    if not (source_dir / "mapper.py").is_file() or not (
        source_dir / "models.py"
    ).is_file():
        raise RuntimeError("expected_source_order_mapper_missing")
    # An isolated *test interpreter* only. It is never used by the Mezan
    # runtime and cannot change how order_engine imports in production.
    fake = types.ModuleType("order_engine")
    fake.__package__ = "order_engine"
    fake.__path__ = [str(source_dir)]
    sys.modules["order_engine"] = fake
    importlib.import_module("order_engine.mapper")

    import pytest

    test_path = Path(__file__).with_name("test_order_engine_mapper.py")
    return pytest.main(["--noconftest", "-q", str(test_path)])


if __name__ == "__main__":
    raise SystemExit(main())
