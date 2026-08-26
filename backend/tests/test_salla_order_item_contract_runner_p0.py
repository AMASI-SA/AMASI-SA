from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "research" / "salla_order_item_contract_runner.py"
SPEC = importlib.util.spec_from_file_location("salla_p0_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def configured_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    manifest = tmp_path / "seed.json"
    manifest.write_text(json.dumps({
        "classification": "SANDBOX_SEED_MANIFEST",
        "orders": [{"state": state} for state in (
            "pending", "under_review", "in_progress", "paid",
            "partially_paid", "completed", "cancelled",
        )],
        "products": [{"kind": "simple"}],
    }), encoding="utf-8")
    env = {
        "SALLA_SANDBOX_BASE_URL": "https://sandbox.invalid.test/admin/v2",
        "SALLA_SANDBOX_ACCESS_TOKEN": "sandbox-secret-token",
        "SALLA_SANDBOX_STORE_ID": "sandbox-store",
        "SALLA_SANDBOX_SEED_MANIFEST": str(manifest),
        "SALLA_SANDBOX_EVIDENCE_DIR": str(tmp_path / "evidence"),
        "SALLA_SANDBOX_RUN_WRITES": "false",
        "SALLA_ACCESS_TOKEN": "must-never-be-read",
    }
    env.update(overrides)
    return env


class SallaP0RunnerTests(unittest.TestCase):
 def test_refuses_to_run_without_sandbox_configuration(self):
    with self.assertRaisesRegex(runner.ContractRunnerError, runner.NOT_CONFIGURED):
        runner.SandboxConfig.from_env({})


 def test_never_falls_back_to_production_settings(self):
    with self.assertRaisesRegex(runner.ContractRunnerError, runner.NOT_CONFIGURED):
        runner.SandboxConfig.from_env({
            "SALLA_ACCESS_TOKEN": "production-token",
            "SALLA_API_BASE_URL": "https://api.salla.dev/admin/v2",
        })


 def test_rejects_known_production_host(self):
    with tempfile.TemporaryDirectory() as directory:
        env = configured_env(Path(directory), SALLA_SANDBOX_BASE_URL="https://api.salla.dev/admin/v2")
        with self.assertRaisesRegex(runner.ContractRunnerError, "SALLA_PRODUCTION_ENDPOINT_FORBIDDEN"):
            runner.SandboxConfig.from_env(env)


 def test_sanitizes_tokens_and_personal_data(self):
    value = runner.sanitize({
        "Authorization": "Bearer secret-token",
        "access_token": "secret",
        "customer": {"name": "Person", "mobile": "0500000000"},
        "safe": "token=also-secret&case=1",
    })
    encoded = json.dumps(value)
    assert "secret" not in encoded
    assert "0500000000" not in encoded
    assert value["Authorization"] == "[REDACTED]"


 def test_mock_retry_reuses_client_request_id(self):
    transport = runner.MockTransport()
    _, retry = runner.retry_once(transport, "POST", "/orders/items", {"quantity": 1}, "same-id")
    assert [call[2] for call in transport.calls] == ["same-id", "same-id"]
    assert retry["same_client_request_id"] is True


 def test_replacement_flow_forbids_delete_first(self):
    with self.assertRaisesRegex(runner.ContractRunnerError, "DELETE_FIRST_FORBIDDEN"):
        runner.ensure_replacement_order([
            {"action": "delete_old"},
            {"action": "create_replacement"},
        ])
    runner.ensure_replacement_order([
        {"action": "create_replacement"},
        {"action": "fetch_confirm_replacement"},
        {"action": "delete_old"},
    ])


 def test_mock_fixture_cannot_be_pass_evidence(self):
    evidence = runner.build_evidence(
        case={"id": "mock"},
        classification=runner.FIXTURE_CLASSIFICATION,
        before={}, request={}, response={"status": 200}, after={},
        webhooks=[], retry=None, verdict="PASS",
    )
    assert evidence["verdict"] == "NOT_EXECUTED"

 def test_mock_transport_cannot_generate_real_pass_evidence(self):
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        config = runner.SandboxConfig.from_env(configured_env(
            tmp_path, SALLA_SANDBOX_RUN_WRITES="true",
        ))
        evidence_path = runner.run_case(
            config,
            {
                "id": "mock-only", "method": "POST", "path": "/orders/items",
                "before_fetch_path": "/orders/items?order_id=1",
                "after_fetch_path": "/orders/items?order_id=1",
            },
            runner.MockTransport(), runner.EvidenceWriter(tmp_path / "evidence"),
            lambda _: [],
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["classification"], runner.FIXTURE_CLASSIFICATION)
        self.assertEqual(evidence["verdict"], "NOT_EXECUTED")


 def test_writes_are_opt_in_and_readiness_is_read_only(self):
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        config = runner.SandboxConfig.from_env(configured_env(tmp_path))
        self.assertFalse(config.writes_enabled)
        self.assertEqual(runner.readiness(config)["status"], "READY")
        transport = runner.MockTransport()
        with self.assertRaisesRegex(runner.ContractRunnerError, runner.WRITES_DISABLED):
            runner.run_case(
                config,
                {"id": "blocked", "method": "POST", "path": "/orders/items"},
                transport,
                runner.EvidenceWriter(tmp_path / "evidence"),
                lambda _: [],
            )
        self.assertEqual(transport.calls, [])


 def test_lost_response_does_not_repeat_commercial_write(self):
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        config = runner.SandboxConfig.from_env(configured_env(
            tmp_path, SALLA_SANDBOX_RUN_WRITES="true",
        ))
        transport = runner.MockTransport()
        runner.run_case(
            config,
            {
                "id": "lost-response",
                "method": "POST",
                "path": "/orders/items",
                "body": {"quantity": 1},
                "before_fetch_path": "/orders/items?order_id=1",
                "after_fetch_path": "/orders/items?order_id=1",
                "simulate_lost_response": True,
            },
            transport,
            runner.EvidenceWriter(tmp_path / "evidence"),
            lambda _: [],
        )
        writes = [call for call in transport.calls if call[0] == "POST"]
        self.assertEqual(len(writes), 1)


if __name__ == "__main__":
    unittest.main()
