"""Focused safety/relay checks; no production data, credentials or Mongo access."""
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from private_common import MONGO_URL, SOURCE_SHA, TOOL_ROOT, RuntimeBlocked, digest, explicit_environment, load_config, load_export, owner_is_valid, replace_owner
from private_http import ReadOnlyGate, authenticated_owner, browser_login_payload, cookie_has_nonbrowser_envelope, path_allowed, relay_auth

ORIGIN = "https://salla-analytics.preview.emergentagent.com"


class ConfigAndGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        root = Path(self.temp.name)
        backend = root / "source" / "backend"
        backend.mkdir(parents=True)
        (backend / "demand_read_runner.py").write_text("import private_guard\nprivate_guard.require_installed()\nprint('inherited_guard_active')\n")
        (root / "export").mkdir()
        self.path = root / "private.json"
        self.value = {"source_root": str(root / "source"), "export_directory": str(root / "export"),
                      "manifest_sha256": "a" * 64, "database_name": "mezan_pr2095_20261125",
                      "expected_owner_id": "owner-example-1", "preview_origin": ORIGIN}
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        self.path.write_text(json.dumps(self.value))

    def test_old_databases_and_other_origins_rejected(self):
        for name in ["production", "mz2_uat_1128_20260921", "mezan_pr2095_probe_20261125", "admin", "test"]:
            self.value["database_name"] = name; self.save()
            with self.assertRaises(RuntimeBlocked): load_config(self.path)
        self.value["database_name"] = "mezan_pr2095_20261125"
        for origin in ["https://mezansalla.com", "http://salla-analytics.preview.emergentagent.com", ORIGIN + "/path"]:
            self.value["preview_origin"] = origin; self.save()
            with self.assertRaises(RuntimeBlocked): load_config(self.path)

    def test_explicit_environment_and_dotenv(self):
        config = load_config(self.path)
        env = explicit_environment(config, "worker")
        self.assertEqual(env["MONGO_URL"], MONGO_URL)
        self.assertNotIn("HOME", env)
        self.assertNotIn("HTTP_PROXY", env)
        self.assertEqual(env["MEZAN_BACKGROUND_WORKER_PROCESS"], "1")
        (Path(self.value["source_root"]) / "backend" / ".env").write_text("UNUSED=1")
        with self.assertRaises(RuntimeBlocked): load_config(self.path)

    def test_no_manifest_no_start(self):
        with self.assertRaises(RuntimeBlocked): load_export(load_config(self.path))

    def test_snapshot_contract_and_disjoint_user_scopes(self):
        root = Path(self.temp.name)
        policy = json.loads((TOOL_ROOT / 'coverage-policy.json').read_bytes())
        contract_raw = (TOOL_ROOT / 'source_contract_v2.json').read_bytes()
        contract = json.loads(contract_raw)
        (root / 'export' / 'source_contract_v2.json').write_bytes(contract_raw)
        entries = []
        for rule in contract['scopes']:
            count = 1 if rule['scope'] == 'owner' else 2 if rule['scope'] == 'team' else 0
            entries.append({**replace_owner(rule, self.value['expected_owner_id']), 'count':count, 'source_count':count,
                'files':[{}] * count, 'row_limit':max(1,count), 'cursor_exhausted':True})
        manifest = {"schema_version": 2, "kind": "mezan_native_bson_snapshot", "source_commit": SOURCE_SHA,
            "owner_id": self.value["expected_owner_id"], "state": {"sha256":"b"*64, "revision":1},
            'source_contract':{'version':2, 'file':'source_contract_v2.json', 'sha256':digest(contract_raw)},
            'tool_implementation':policy['tool_implementation'],
            "source_recheck": {"raw_sha256_unchanged":True, "revision_unchanged":True, "outside_capture_snapshot":True},
            "consistency": {"source_reads":"one_read_only_snapshot_session", "snapshot_fallback_permitted":False},
            "scope_limits": {key:True for key in ["raw_canonical_state_and_referenced_audit_complete", "owner_context_included",
                "resource_directory_included", "expected_forecast_source_graph_included", "provider_order_label_sources_included",
                "full_navigation_source_closure_verified", "courier_unready_order_discovery_included"]},
            "supplemental": entries}
        def capture():
            data=json.dumps(manifest).encode()
            (root/'export'/'manifest.json').write_bytes(data)
            (root/'export'/'COMPLETE.json').write_text(json.dumps({'schema_version':2,'manifest_sha256':digest(data)}))
            self.value['manifest_sha256']=digest(data); self.save()
        capture()
        self.assertEqual(load_export(load_config(self.path))[1]['users'], 3)
        manifest['scope_limits']['expected_forecast_source_graph_included']=False
        capture()
        with self.assertRaisesRegex(RuntimeBlocked,'semantic_coverage_incomplete'):
            load_export(load_config(self.path))

    def test_owner_is_never_promoted_or_remapped(self):
        self.assertTrue(owner_is_valid({"id": "same", "role": "owner"}, "same"))
        for row in [{"id": "other", "role": "owner"}, {"id": "same", "role": "admin", "is_owner": True},
                    {"id": "same", "role": "owner", "disabled": True}]:
            self.assertFalse(owner_is_valid(row, "same"))

    def test_real_child_inherits_guard_and_blocks_external_old_db_and_full_server(self):
        config = load_config(self.path)
        source = r'''
import socket, subprocess, sys
from pathlib import Path
import private_guard
private_guard.require_installed()
def blocked(fn):
    try: fn()
    except (PermissionError, ImportError): return
    raise AssertionError('guard did not reject')
for address in [('203.0.113.1',443),('127.0.0.1',27018),('127.0.0.1',27017),('127.0.0.1',8001)]:
    with socket.socket() as sock: blocked(lambda: sock.connect(address))
blocked(lambda: socket.getaddrinfo('example.com',443))
blocked(lambda: __import__('server'))
blocked(lambda: open('/tmp/.env'))
blocked(lambda: subprocess.run(['/bin/true']))
from private_common import load_config
import os
config=load_config(os.environ['MEZAN_PRIVATE_RUNTIME_CONFIG'])
backend=Path(config['source_root'])/'backend'
result=subprocess.run([sys.executable,'-B',str(backend/'demand_read_runner.py'),'a'*64,'b'*32,'c'*32,'1.000000000'],cwd=str(backend),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,check=True)
assert result.stdout.strip()=='inherited_guard_active'
print('guard_checks_passed')
'''
        result = subprocess.run([sys.executable, "-c", source], env=explicit_environment(config, "worker"),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "guard_checks_passed")

    def test_actual_guarded_api_listener_start_stop(self):
        config = load_config(self.path)
        source = r'''
import asyncio, socket
import uvicorn
import private_guard
private_guard.require_installed()
async def app(scope, receive, send):
    await send({'type':'http.response.start','status':200,'headers':[]})
    await send({'type':'http.response.body','body':b'probe'})
async def main():
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8002,lifespan='off',access_log=False,log_level='critical'))
    task=asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done(): await task
                await asyncio.sleep(0.01)
        with socket.socket() as sock:
            try: sock.connect(('127.0.0.1',8002))
            except PermissionError: pass
            else: raise AssertionError('bind resolution weakened connect restriction')
    finally:
        server.should_exit=True
        await task
asyncio.run(main())
print('guarded_listener_started_stopped')
'''
        result = subprocess.run([sys.executable, "-c", source], env=explicit_environment(config, "api"),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "guarded_listener_started_stopped")


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_and_malformed_login_mode_rejected(self):
        for value in [True, "true", 1, [], {}]:
            self.assertFalse(browser_login_payload(json.dumps({"mobile_client": value}).encode()))
        for value in [None, False]:
            self.assertTrue(browser_login_payload(json.dumps({"mobile_client": value}).encode()))
        self.assertFalse(browser_login_payload(b"[]"))
        self.assertFalse(browser_login_payload(b'{"client_type":"amasi_mobile"}'))
        # These are deliberately unsigned envelope fragments, never valid sessions.
        envelope = lambda value: "unsigned." + base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=") + ".invalid"
        self.assertFalse(cookie_has_nonbrowser_envelope(envelope({"type":"access"}), "access"))
        self.assertTrue(cookie_has_nonbrowser_envelope(envelope({"type":"access", "client":"amasi_mobile"}), "access"))
        self.assertTrue(cookie_has_nonbrowser_envelope(envelope({"type":"refresh"}), "access"))
        self.assertTrue(cookie_has_nonbrowser_envelope("invalid", "access"))

    async def test_exact_read_and_demand_release_only(self):
        self.assertTrue(path_allowed("GET", "/api/operational-balances/workspace/report"))
        for suffix in ('movements', 'workspace/options', 'workspace/context', 'workspace/order-issues', 'workspace/parties',
                'workspace/selection/bank/example', 'workspace/accounts/bank', 'workspace/resource/bank/example',
                'workspace/forecast/bank/example', 'workspace/ledger/bank/example', 'workspace/statements/bank/example',
                'workspace/history/bank/example', 'workspace/supplier-invoices/example/invoice', 'access/users', 'access/users/example'):
            self.assertTrue(path_allowed('GET', '/api/operational-balances/' + suffix), suffix)
        self.assertTrue(path_allowed("DELETE", "/api/operational-balances/workspace/read-demand/" + "a" * 64))
        for method, path in [("POST", "/api/operational-balances/refresh"), ("GET", "/api/operational-balances/inventory"),
                             ("PUT", "/api/operational-balances/access/users/x"), ("DELETE", "/api/operational-balances/workspace/pending-commands/x"),
                             ('GET', '/api/operational-balances/workspace/specialist/opening-stock'),
                             ('GET', '/api/operational-balances/workspace/resource/bank/extra/path'),
                             ('GET', '/api/operational-balances/workspace/refunds')]:
            self.assertFalse(path_allowed(method, path))
        calls = []
        async def endpoint(request):
            calls.append(request.url.path)
            return PlainTextResponse("allowed")
        app = ReadOnlyGate(Starlette(routes=[Route("/{path:path}", endpoint, methods=["GET", "POST", "DELETE"])]), origin=ORIGIN)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            self.assertEqual((await client.get("/api/operational-balances/workspace/report", headers={"authorization":"Bearer example"})).status_code, 403)
            self.assertEqual((await client.post("/api/auth/login", headers={"origin":"https://other.example", "sec-fetch-site":"cross-site"})).status_code, 403)
            self.assertEqual((await client.post("/api/auth/login", headers={"origin":ORIGIN, "sec-fetch-site":"same-origin"})).status_code, 200)
            self.assertEqual((await client.post("/api/operational-balances/refresh", headers={"origin":ORIGIN, "sec-fetch-site":"same-origin"})).status_code, 403)
        self.assertEqual(calls, ["/api/auth/login"])

    async def test_relay_preserves_browser_headers_and_individual_cookies_without_retention(self):
        seen = []
        async def upstream(request):
            seen.append(request)
            return httpx.Response(200, headers=[("Set-Cookie", "access_token=test-one; HttpOnly; Secure; Path=/; SameSite=None"),
                                               ("Set-Cookie", "refresh_token=test-two; HttpOnly; Secure; Path=/; SameSite=None")],
                                  json={"id":"owner-example-1", "role":"owner"})
        transport = httpx.MockTransport(upstream)
        headers = [(b"cookie", b"access_token=existing-example"), (b"origin", ORIGIN.encode()),
                   (b"sec-fetch-site", b"same-origin"), (b"x-forwarded-host", b"untrusted.example")]
        response = await relay_auth("GET", "/api/auth/me", headers, transport=transport)
        self.assertTrue(authenticated_owner(response, "owner-example-1"))
        self.assertFalse(authenticated_owner(response, "different-owner"))
        self.assertEqual(len([1 for k,v in response.raw_headers if k == b"set-cookie"]), 2)
        self.assertEqual(seen[0].headers["cookie"], "access_token=existing-example")
        self.assertEqual(seen[0].headers["origin"], ORIGIN)
        self.assertEqual(seen[0].headers["sec-fetch-site"], "same-origin")
        self.assertNotIn("x-forwarded-host", seen[0].headers)
        await relay_auth("GET", "/api/auth/me", [], transport=transport)
        self.assertNotIn("cookie", seen[1].headers)

    async def test_redirect_is_not_followed_and_native_authorization_rejected(self):
        calls = []
        async def upstream(request):
            calls.append(request.url)
            return httpx.Response(302, headers={"location":"https://other.example"})
        response = await relay_auth("GET", "/api/auth/me", [], transport=httpx.MockTransport(upstream))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(ValueError):
            await relay_auth("GET", "/api/auth/me", [(b"authorization", b"Bearer example")], transport=httpx.MockTransport(upstream))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
