"""Observe existing synthetic HTTP tests without changing requests/results/assertions."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import sys

output = Path(os.environ['MZ2_UAT_OBSERVER_OUTPUT'])
assert output.is_absolute() and not output.exists()
def append(value):
    with output.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + '\n')
def network_audit(event, args):
    address = None
    if event == 'socket.getaddrinfo':
        address = args[0]
    elif event == 'socket.connect':
        address = args[1][0] if isinstance(args[1], tuple) else args[1]
    elif event == 'socket.sendto':
        address = args[2][0] if isinstance(args[2], tuple) else args[2]
    if address is None:
        return
    try:
        local = ipaddress.ip_address(address).is_loopback
    except ValueError:
        local = str(address) == 'localhost'
    if not local:
        append({'denied_network': str(address), 'event': event})
        raise PermissionError('Acceptance observer rejects non-loopback network')
sys.addaudithook(network_audit)
import httpx
original_send = httpx.AsyncClient.send
async def observe(self, request, *args, **kwargs):
    response = await original_send(self, request, *args, **kwargs)
    raw = await response.aread()
    try:
        body = json.loads(raw)
    except ValueError:
        body = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    append({'method': request.method, 'path': request.url.path, 'status': response.status_code, 'response': body})
    return response
httpx.AsyncClient.send = observe
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
