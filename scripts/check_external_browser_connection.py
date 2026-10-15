"""Run only after BROWSERLESS_API_TOKEN is installed through secret settings.

No command-line token argument, token output, account inspection, storefront
navigation, or automatic retry. Exit zero proves connectivity only.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from external_browser_connection import probe_browser_connection


if __name__ == '__main__':
    result = asyncio.run(probe_browser_connection(os.environ.get('BROWSERLESS_API_TOKEN', '')))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result['status'] == 'connection_verified' else 1)
