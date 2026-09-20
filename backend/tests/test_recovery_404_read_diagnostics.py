"""Read failures remain blocked and expose no customer/key/provider content."""
import json
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from test_recovery_404_isolated import r, FakePorts, SCOPE
from integrations.qoyod_manual.recovery_diagnostics import reading
from integrations.qoyod_manual.recovery_adapter import ProductionPorts
from integrations.qoyod_manual.client import ManualQoyodClient, ManualQoyodError
from mongomock_motor import AsyncMongoMockClient

SECRET = "customer@example.invalid API-KEY secret-token response-body"


class ReadDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_deepest_stage_and_timeout_cause_survive_without_messages(self):
        p = FakePorts()
        p.claims.add("100")
        async def failed_read(ref):
            with reading("observe"):
                with reading("provider_page", page=7):
                    try:
                        raise httpx.ReadTimeout(SECRET)
                    except httpx.ReadTimeout as cause:
                        raise ManualQoyodError(status_code=0, endpoint=SECRET,
                            response_excerpt=SECRET) from cause
        p.observe = failed_read
        result = await r.audit_one(SCOPE, "100", p)
        self.assertEqual((result.state, result.reason), ("review", "outcome_unknown"))
        d = result.read_diagnostic
        self.assertEqual((d["stage"], d["page"], d["http_status"], d["cause_type"]),
                         ("provider_page", 7, 0, "ReadTimeout"))
        self.assertNotIn(SECRET, json.dumps(d))
        self.assertEqual((p.sends, p.repairs, p.claims), (0, 0, {"100"}))

    async def test_real_adapter_identifies_invoice_detail_http_failure(self):
        db = AsyncMongoMockClient().db
        port = ProductionPorts(db, {"orders_owner": "synthetic-store"})
        failure = ManualQoyodError(status_code=503, endpoint=SECRET, response_excerpt=SECRET)
        with (patch('integrations.qoyod.credentials.get_api_key', AsyncMock(return_value='synthetic')),
             patch.dict('os.environ', {'QOYOD_API_BASE':'https://qoyod.invalid'}),
             patch.object(ManualQoyodClient, '_request', AsyncMock(return_value={'invoices':[{'id':7,'reference':'100'}]})),
             patch.object(ManualQoyodClient, 'get_invoice', AsyncMock(side_effect=failure))):
            with self.assertRaises(ManualQoyodError) as raised:
                await port.observe('100')
        self.assertIs(raised.exception, failure)
        d = failure._recovery_read_diagnostic
        self.assertEqual((d['stage'],d['http_status'],d['page']),('provider_invoice',503,1))
        self.assertNotIn(SECRET,json.dumps(d))

    def test_programming_error_has_code_location_without_exception_text(self):
        try:
            with reading('facts'):
                raise TypeError(SECRET)
        except TypeError as exc:
            self.assertEqual(exc._recovery_read_diagnostic['error_type'],'TypeError')
            self.assertNotIn(SECRET,json.dumps(exc._recovery_read_diagnostic))

    def test_success_does_not_emit_diagnostic_or_change_return(self):
        with reading('preflight'):
            total = '161.12'
        self.assertEqual(total,'161.12')
