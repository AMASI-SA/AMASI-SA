from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_COMMIT = "d63a89a95f18e4786ca1e99dd832b23360e8fe45"
SCRIPT_PATH = "scripts/apply_prod_tiktok_native_5m.py"

source = subprocess.check_output(
    ["git", "show", f"{BASE_COMMIT}:{SCRIPT_PATH}"],
    cwd=ROOT,
    text=True,
)
needle = '''    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return source.replace(old, new, 1)
'''
replacement = '''    if label == "TikTok scheduler target query" and count == 2:
        return source.replace(old, new, 1)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return source.replace(old, new, 1)
'''
if source.count(needle) != 1:
    raise SystemExit("Unable to align the controlled TikTok patch helper")
source = source.replace(needle, replacement, 1)
exec(
    compile(source, SCRIPT_PATH, "exec"),
    {
        "__name__": "__main__",
        "__file__": str(ROOT / SCRIPT_PATH),
        "__builtins__": __builtins__,
    },
)
