"""No secrets or database writes in public/private HTTP health probes."""
import sys
import urllib.request

port = 10000 if sys.argv[1] == 'web' else 8000
with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ready', timeout=3) as response:
    assert response.status == 200
