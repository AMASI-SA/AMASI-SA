"""Iter-291 regression tests: Salla OAuth `invalid_scope` fix.

Root cause: DEFAULT_SCOPES previously contained `customers.read` while
the Salla Partners Portal App did NOT have the Customers permission
enabled. Salla validates the scope string against the App's enabled
permissions during /oauth2/auth — any unenabled scope fails the whole
request with `error=invalid_scope`.

These tests pin the safe scope list so it cannot silently regress, and
verify that customer data is sourced from the order payload (not from
the standalone Customers API) — so removing `customers.read` is safe.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, "/app/backend")
from salla_integration import service as svc  # noqa: E402


# ── Scope hygiene ─────────────────────────────────────────────────────
class TestScopeHygiene:
    def test_default_scopes_does_not_include_customers(self):
        """`customers.read` must NOT be in the default scope set — it
        is not enabled in our Salla Partners App and causes the whole
        OAuth flow to fail with `invalid_scope`."""
        scopes = svc.DEFAULT_SCOPES.split()
        assert "customers.read" not in scopes, (
            f"customers.read leaked back into DEFAULT_SCOPES: {svc.DEFAULT_SCOPES!r}"
        )
        assert "customers.write" not in scopes
        assert "customers.read_write" not in scopes

    def test_default_scopes_uses_official_read_write_suffix(self):
        """Per Salla's official docs (docs.salla.dev/421118m0 and
        /421413m0 App Events), the write capability is expressed via
        the `.read_write` suffix — NOT a separate `.write` token.

        Using `orders.write` / `webhooks.write` as standalone scopes is
        unofficial and triggers `invalid_scope` at /oauth2/auth.
        """
        scopes = svc.DEFAULT_SCOPES.split()
        # Forbidden: unofficial standalone `.write` tokens.
        assert "orders.write" not in scopes, (
            "orders.write is NOT an official Salla scope — use orders.read_write"
        )
        assert "webhooks.write" not in scopes, (
            "webhooks.write is NOT an official Salla scope — use webhooks.read_write"
        )
        # If we want write capability, the official form must be present.
        # Allow either pure-read (orders.read alone) or read+write
        # (orders.read_write) — but never the unofficial split form.
        has_orders_rw = "orders.read_write" in scopes
        has_orders_r_only = (
            "orders.read" in scopes and "orders.read_write" not in scopes
        )
        assert has_orders_rw or has_orders_r_only, (
            f"DEFAULT_SCOPES must include orders.read_write OR orders.read, "
            f"got: {svc.DEFAULT_SCOPES!r}"
        )
        # Same rule for webhooks.
        has_hooks_rw = "webhooks.read_write" in scopes
        has_hooks_r_only = (
            "webhooks.read" in scopes and "webhooks.read_write" not in scopes
        )
        assert has_hooks_rw or has_hooks_r_only, (
            f"DEFAULT_SCOPES must include webhooks.read_write OR webhooks.read, "
            f"got: {svc.DEFAULT_SCOPES!r}"
        )

    def test_default_scopes_does_not_include_products(self):
        """Products scope is intentionally OFF — we only read/write
        orders. If/when SKU updates land, enable Products in Partners
        Portal FIRST, then add the scope here."""
        scopes = svc.DEFAULT_SCOPES.split()
        for s in ("products.read", "products.write", "products.read_write"):
            assert s not in scopes, f"unexpected scope: {s}"

    def test_default_scopes_does_not_include_shipping_payments_taxes_branches(self):
        """Per integration design, these come from order payload — no
        standalone scope is requested. Adding any of them would fail
        OAuth unless toggled in Partners Portal."""
        scopes = svc.DEFAULT_SCOPES.split()
        forbidden = {
            "payments.read", "payments.write",
            "shipping.read", "shipping.write", "shipments.read",
            "taxes.read", "taxes.write",
            "branches.read",
            "transactions.read",
        }
        leaked = forbidden & set(scopes)
        assert not leaked, f"forbidden scopes present: {leaked}"

    def test_required_scopes_present(self):
        """The minimum set required for the Salla→Qoyod pipeline,
        using Salla's official scope format."""
        scopes = svc.DEFAULT_SCOPES.split()
        # offline_access is REQUIRED to get a refresh_token from Salla.
        assert "offline_access" in scopes
        # Read+write orders (we update order status when Qoyod confirms).
        # Official format: orders.read_write (single token, not split).
        assert "orders.read_write" in scopes or "orders.read" in scopes
        # Webhooks (register + manage subscription on the merchant's store).
        # Official format: webhooks.read_write.
        assert "webhooks.read_write" in scopes or "webhooks.read" in scopes
        # rev42 (user directive): the Salla Partners panel permissions
        # are LOCKED to exactly 5 scopes — settings.read is NOT
        # available; store info is covered by stores.read.
        assert "stores.read" in scopes
        assert "categories.read" in scopes
        assert "settings.read" not in scopes

    def test_scopes_are_space_separated_single_line(self):
        """Salla expects a single, space-separated string — no commas,
        no newlines, no leading/trailing whitespace."""
        s = svc.DEFAULT_SCOPES
        assert "," not in s
        assert "\n" not in s
        assert "\t" not in s
        assert s == s.strip()
        assert "  " not in s  # no double spaces

    def test_scope_override_via_env(self, monkeypatch):
        """Operators can override the scope list via SALLA_OAUTH_SCOPES
        without a code change. This is the recovery path when Salla
        renames scopes or when the App's enabled permissions change.

        The override is used END-TO-END: e.g. if a future version of
        Salla accepts only `orders` (no suffix), an ops engineer can
        roll that out via .env in seconds.
        """
        monkeypatch.setenv(
            "SALLA_OAUTH_SCOPES",
            "offline_access orders.read settings.read",
        )
        # Re-import the module so the env var is picked up.
        import importlib
        import salla_integration.service as svc_reload
        importlib.reload(svc_reload)
        try:
            assert svc_reload.DEFAULT_SCOPES == (
                "offline_access orders.read settings.read"
            )
            assert "orders.read_write" not in svc_reload.DEFAULT_SCOPES.split()
        finally:
            # Restore module state for any tests that run after this one.
            monkeypatch.delenv("SALLA_OAUTH_SCOPES", raising=False)
            importlib.reload(svc_reload)


# ── No-leak: code must not call /customers ────────────────────────────
class TestNoCustomersApiUsage:
    def test_no_customer_api_calls_in_salla_integration_module(self):
        """Confirms the pipeline reads customer data from the order
        payload (per Salla's design) rather than from /customers — so
        dropping `customers.read` is a safe, non-breaking change."""
        import pathlib
        root = pathlib.Path("/app/backend/salla_integration")
        offenders = []
        for py in root.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            # Anything that looks like a Salla customers endpoint call.
            for needle in (
                "/customers?",
                "/customers/",
                "/customers ",
                "admin/v2/customers",
            ):
                if needle in text:
                    offenders.append(f"{py}: {needle}")
        assert not offenders, (
            "Salla integration module references /customers endpoints "
            "but we dropped `customers.read` from DEFAULT_SCOPES. "
            f"Either restore the scope or remove these calls: {offenders}"
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
