from salla_orders_v3.diagnostics import build_parity_report, scope_diagnostic


def test_scope_diagnostic_uses_stored_scope_and_never_returns_tokens():
    result = scope_diagnostic({
        "status": "connected",
        "scope": "offline_access orders.read products.read",
        "access_token_encrypted": b"secret",
        "refresh_token_encrypted": b"secret",
    })

    assert result["required_scope_present"] is True
    assert result["token_fields_returned"] is False
    assert "access_token_encrypted" not in result
    assert "refresh_token_encrypted" not in result


def test_scope_diagnostic_accepts_current_salla_read_write_scope():
    result = scope_diagnostic({
        "status": "connected",
        "scope": "settings.read orders.read_write offline_access",
    })

    assert result["required_scope_present"] is True
    assert result["effective_order_read_scopes"] == ["orders.read_write"]


def test_cutover_remains_closed_until_all_parity_and_regressions_pass():
    order = {"products": []}
    dry = {"eligible": True, "payload": {}, "idempotency_key": "same"}
    rows = [{"order_number": "1", "campaign_id": "c", "revenue": 10}]

    blocked = build_parity_report(
        legacy_order=order,
        v3_order=order,
        legacy_qoyod_dry_run=dry,
        v3_qoyod_dry_run=dry,
        legacy_attribution_rows=rows,
        v3_attribution_rows=rows,
        regression_results={"order_review": True, "fulfillment": False},
    )
    allowed = build_parity_report(
        legacy_order=order,
        v3_order=order,
        legacy_qoyod_dry_run=dry,
        v3_qoyod_dry_run=dry,
        legacy_attribution_rows=rows,
        v3_attribution_rows=rows,
        regression_results={
            "order_review": True,
            "fulfillment": True,
            "qoyod": True,
            "snapchat_attribution": True,
            "dashboard_order_totals": True,
        },
    )

    assert blocked["cutover_allowed"] is False
    assert allowed["cutover_allowed"] is True
    assert allowed["provider_write_reached"] is False
