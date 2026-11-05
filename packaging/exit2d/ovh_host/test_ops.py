"""Hosting boundaries, alert correctness and fail-closed operation approvals."""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).parent))
from config import load, validate
import backup
from generate import caddy, firewall, units
from live_performance import handshake, summarize
from monitor import evaluate

CONFIG = json.loads((Path(__file__).parent/'host.example.json').read_text())

class Boundaries(unittest.TestCase):
    def test_valid_config_and_preserved_source(self):
        c = validate(CONFIG)
        self.assertEqual(c['source_sha'], '901dc25e4b673188f4ac2ee8ca5b581b903c336b')
        self.assertIn('127.0.0.1:10000', caddy(c))
        self.assertNotIn('tls internal', caddy(c))
        self.assertIn('tls internal', caddy(c, ci=True))

    def test_reject_production_and_secrets(self):
        variants = [('origin', 'https://mezansalla.com'), ('origin', 'https://x.mezansalla.com'),
                    ('origin', 'https://user:password@staging.example.test'), ('origin', 'http://staging.example.test'),
                    ('origin', 'https://127.0.0.1'), ('admin_cidrs', ['0.0.0.0/0']),
                    ('checkout', '/opt/../app'), ('public_interface', 'eth0" accept'),
                    ('password', 'public-test-value')]
        for key, value in variants:
            with self.subTest(key=key, value=value):
                c = copy.deepcopy(CONFIG); c[key] = value
                with self.assertRaises(ValueError): validate(c)

    def test_firewall_owns_one_table_and_covers_docker_forwarding(self):
        rules = firewall(validate(CONFIG))
        self.assertNotIn('flush ruleset', rules)
        self.assertIn('hook forward priority -10', rules)
        self.assertIn('iifname "ens3" ct state new drop', rules)
        self.assertIn('ip saddr 192.0.2.10/32 tcp dport 22 accept', rules)

    def test_units_are_dormant_files_and_bound_backup(self):
        u = units(validate(CONFIG))
        self.assertIn('--execute --approval /etc/mezan-vps/operations-approval.json', u['mezan-backup.service'])
        self.assertIn('admin off', caddy(validate(CONFIG)))
        self.assertIn('MemoryMax=192M', u['mezan-tls.service'])

    def test_missing_or_stale_backup_and_oom_alert(self):
        good = {'running': True, 'oom': False, 'health': 'healthy', 'memory_fraction': .3, 'restarts': 0}
        self.assertEqual(evaluate({'web': good}, .2, 3600), [])
        bad = dict(good, oom=True, memory_fraction=.9, restarts=1)
        self.assertEqual(len(evaluate({'web': bad}, .8, None)), 5)

    def test_approval_example_never_authorizes(self):
        with patch('backup.os.geteuid', return_value=0, create=True), patch('backup.private_file', return_value=Path(__file__).parent/'operations-approval.example.json'):
            with self.assertRaises(ValueError): backup.authorize(validate(CONFIG), 'ignored')

    def test_expired_wrong_source_and_scope_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'approval.json'
            base = {'approved': True, 'scope': 'synthetic-vps-backup', 'project': CONFIG['project'],
                    'source_sha': CONFIG['source_sha'], 'expires_at': (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
            for field, value in [('source_sha', '0'*40), ('scope', 'production'), ('expires_at', '2020-01-01T00:00:00+00:00')]:
                p.write_text(json.dumps(dict(base, **{field: value})))
                with patch('backup.os.geteuid', return_value=0, create=True), patch('backup.private_file', return_value=p):
                    with self.assertRaises(ValueError): backup.authorize(validate(CONFIG), str(p))

    def test_https_handshake_rejects_before_any_cookie(self):
        with patch('live_performance.fetch', return_value=({'kind': 'production', 'source_sha': CONFIG['source_sha']}, 1)):
            with self.assertRaises(ValueError): handshake(validate(CONFIG))

    def test_actual_p95_nearest_rank(self):
        self.assertEqual(summarize(list(range(1, 101)))['p95_ms'], 95)

if __name__ == '__main__': unittest.main()
