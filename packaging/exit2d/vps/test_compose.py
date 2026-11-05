"""Validate actual resolved Compose contract on Linux, not YAML string guesses."""
import json
import sys

d = json.load(open(sys.argv[1]))
s = d['services']
assert set(s) == {'web', 'reports', 'sync', 'accounting', 'mongo', 'migration'}
persistent = [s[n] for n in ('web', 'reports', 'sync', 'accounting', 'mongo')]
assert sum(float(x['cpus']) for x in persistent) == 3.5
assert sum(int(x['mem_limit']) for x in persistent) == 5888*1024*1024
assert all(int(x['mem_limit']) == int(x['memswap_limit']) for x in persistent)
assert all(x['cgroup_parent'] for x in persistent)
assert d['networks']['private']['internal'] is True
assert len(s['web']['ports']) == 1 and s['web']['ports'][0]['host_ip'] == '127.0.0.1'
assert all(not s[n].get('ports') for n in ('reports', 'sync', 'accounting', 'mongo'))
for name in ('web', 'reports', 'sync', 'accounting'):
    assert s[name]['depends_on']['migration']['condition'] == 'service_completed_successfully'
    assert 'JWT_SECRET' not in s[name]['environment'] and 'MONGO_URL' not in s[name]['environment']
assert s['migration']['depends_on']['mongo']['condition'] == 'service_healthy'
print('PASS resolved Compose roles, resource sum, swap, private network, secret mounts and migration barrier')
