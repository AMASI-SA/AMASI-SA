"""Offline security tests for Mezan-only assisted Salla OAuth diagnostics."""
import pytest

from salla_order_creation.capabilities import (
    assisted_salla_scope_report, scope_claims,
)


def test_connected_mezan_token_scope_claims_report_only_and_no_bank_iban():
    doc={
        "status":"connected",
        "scope":"offline_access settings.read orders.read_write shipping.read_write webhooks.read_write",
        "access_token":"SENSITIVE-SYNTHETIC-ACCESS",
        "refresh_token":"SENSITIVE-SYNTHETIC-REFRESH",
        "bank_iban":"SA0380000000608010167519",
    }
    report=assisted_salla_scope_report(doc,creation_enabled=True)
    assert report["connected"] is True
    assert report["orders_create_scope"] is True
    assert report["shipping_read_scope"] is True
    assert report["catalog_read_scope"] is False
    assert report["payment_bank_read_scope"] is False
    assert report["missing_required_scope_groups"]==[
        "catalog_read_scope","payment_bank_read_scope",
    ]
    assert report["creation_feature_enabled"] is True
    assert report["stored_scope_claims_only"] is True
    assert report["live_provider_permission_verified"] is False
    assert report["active_bank_beneficiary_iban_verified"] is False
    assert report["proof_writers_integrated"] is False
    for secret in ("SENSITIVE-SYNTHETIC-ACCESS","SENSITIVE-SYNTHETIC-REFRESH",
                   "SA0380000000608010167519"):
        assert secret not in str(report)


@pytest.mark.parametrize("bad",[
    None,False,3,{},["payments.read",None],
    ["orders.read_write","x#unsafe"],
    "payments.read\n;DROP TABLE",
    " ".join(["products.read"]*41),
])
def test_untrusted_or_malformed_scope_fails_closed(bad):
    assert scope_claims(bad)==frozenset()


def test_connected_scope_list_is_supported_but_not_live_authority():
    doc={
        "status":"connected",
        "scope":[
            "offline_access","orders.read_write","shipping.read_write",
            "products.read","payments.read",
        ],
    }
    value=assisted_salla_scope_report(doc)
    assert value["missing_required_scope_groups"] == []
    assert value["live_provider_permission_verified"] is False
    assert value["creation_feature_enabled"] is False
    assert value["bank_review_read_feature_enabled"] is False


def test_inactive_or_disconnected_merchant_cannot_claim_scope_authority():
    doc={"status":"disconnected","scope":" ".join([
        "orders.read_write","products.read","shipping.read_write","payments.read",
    ])}
    value=assisted_salla_scope_report(doc)
    assert value["connected"] is False
    assert len(value["missing_required_scope_groups"])==4
    assert value["live_provider_permission_verified"] is False


def test_fake_scope_is_never_granted_from_separate_salla_plugin():
    doc={"status":"connected","scope":"orders.read_write"}
    value=assisted_salla_scope_report(doc)
    assert value["orders_create_scope"] is True
    assert value["payment_bank_read_scope"] is False
    assert value["catalog_read_scope"] is False
    assert value["shipping_read_scope"] is False
