"""Iter-293.3 — Production Writes Kill Switch.

When `settings.production_writes_locked == True`, the live webhook
pipeline must:

  • Run every stage (normalize → preflight → product/customer resolve
    → invoice payload build).
  • NOT call `api.qoyod.com/invoices` or `/invoice_payments`.
  • Persist the fully-built payload in
    `inbox.qoyod_payloads.invoice_locked_payload`.
  • Mark the row's pipeline_stage as `LOCKED_AWAITING_APPROVAL`.

These tests focus on the SETTINGS contract (PUT/GET roundtrip) +
unit-level confirmation that the SettingsPatch model exposes the
new field. Pipeline integration is covered by the existing
pipeline test suite via the production_writes_locked branch.
"""
from __future__ import annotations

import os
import sys

import pytest
import requests

sys.path.insert(0, "/app/backend")


def _read_backend_url() -> str:
    explicit = os.environ.get("REACT_APP_BACKEND_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().strip('"').rstrip("/")
    except FileNotFoundError:
        pass
    return ""


BASE_URL = _read_backend_url()
API = f"{BASE_URL}/api" if BASE_URL else ""


def _admin_token() -> str:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": "admin@hesab.app", "password": "admin123"},
        timeout=10,
    )
    if r.status_code != 200:
        pytest.skip(f"admin login failed: {r.status_code}")
    return r.json().get("access_token") or r.json().get("token")


class TestKillSwitchSettings:
    @pytest.fixture(autouse=True)
    def _gate(self):
        if not API:
            pytest.skip("REACT_APP_BACKEND_URL not configured")

    def test_production_writes_locked_settable_via_put(self):
        """PUT /settings must accept and persist the kill switch."""
        h = {"Authorization": f"Bearer {_admin_token()}",
             "Content-Type": "application/json"}
        # Snapshot current value for cleanup.
        cur = requests.get(f"{API}/integrations/qoyod/settings",
                           headers=h, timeout=10).json()
        original = cur.get("production_writes_locked")
        try:
            # Set True.
            r = requests.put(
                f"{API}/integrations/qoyod/settings",
                headers=h,
                json={"production_writes_locked": True},
                timeout=10,
            )
            assert r.status_code == 200, r.text
            after = requests.get(f"{API}/integrations/qoyod/settings",
                                 headers=h, timeout=10).json()
            assert after.get("production_writes_locked") is True
            # Set False.
            r = requests.put(
                f"{API}/integrations/qoyod/settings",
                headers=h,
                json={"production_writes_locked": False},
                timeout=10,
            )
            assert r.status_code == 200, r.text
            after = requests.get(f"{API}/integrations/qoyod/settings",
                                 headers=h, timeout=10).json()
            assert after.get("production_writes_locked") is False
        finally:
            # Restore.
            if original is not None:
                requests.put(
                    f"{API}/integrations/qoyod/settings",
                    headers=h,
                    json={"production_writes_locked": bool(original)},
                    timeout=10,
                )


class TestPipelineKillSwitchBehaviour:
    """Direct unit-level assertions against the pipeline branch.

    We DO NOT spin up a full pipeline integration here — that's
    covered by the larger qoyod pipeline tests. We just confirm the
    GUARD CONDITION the pipeline reads is correct, by importing the
    pipeline module and checking it reads `production_writes_locked`
    from settings before posting.
    """

    def test_pipeline_module_references_production_writes_locked(self):
        import pathlib
        src = pathlib.Path(
            "/app/backend/integrations/qoyod/pipeline.py"
        ).read_text(encoding="utf-8")
        # The kill-switch literal must appear and be tied to the
        # create_invoice call site (Iter-293.3 sentinel comment).
        assert "production_writes_locked" in src, (
            "Kill switch flag missing from pipeline.py")
        assert "LOCKED_AWAITING_APPROVAL" in src, (
            "LOCKED_AWAITING_APPROVAL outcome missing — operators "
            "won't be able to distinguish locked rows from failures")
        # The check must run BEFORE `api_client.create_invoice` —
        # search for the substring proximity.
        ks_idx = src.index("production_writes_locked")
        post_idx = src.index("api_client.create_invoice")
        assert ks_idx < post_idx, (
            "Kill switch check must precede the create_invoice POST")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
