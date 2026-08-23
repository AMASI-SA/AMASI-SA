"""Lightweight pytest bootstrap for isolated Snapchat V2 unit tests.

The production packages expose broad router-composition APIs from their
``__init__`` modules. Foundation tests need only specific submodules, so this
plugin registers namespace packages that preserve normal submodule imports
without importing every production router and optional dependency.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

BACKEND_ROOT = Path(__file__).resolve().parent


def _register_namespace(name: str) -> None:
    if name in sys.modules:
        return
    module = ModuleType(name)
    module.__package__ = name
    module.__path__ = [str(BACKEND_ROOT / name)]  # type: ignore[attr-defined]
    sys.modules[name] = module


_register_namespace("integrations_control_center")
_register_namespace("ads_manager")
