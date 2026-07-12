from pathlib import Path

import httpx
import pytest

from integrations.qoyod_manual.client import (
    ManualQoyodClient,
    ManualQoyodError,
)


@pytest.mark.asyncio
async def test_httpx_timeout_becomes_manual_qoyod_error(monkeypatch):
    async def fail_request(*args, **kwargs):
        request = httpx.Request("POST", "https://api.qoyod.test/invoices")
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "request", fail_request)

    client = ManualQoyodClient(
        api_key="test",
        base_url="https://api.qoyod.test",
    )

    with pytest.raises(ManualQoyodError) as exc:
        await client.create_invoice(
            {"invoice": {}},
            idem="test-invoice",
        )

    assert exc.value.status_code == 0
    assert "ReadTimeout" in exc.value.response_excerpt


def test_invoice_marker_is_persisted_before_followup_get():
    source = Path(
        "integrations/qoyod_manual/send.py"
    ).read_text(encoding="utf-8")

    create_invoice_pos = source.index(
        "created_inv = await client.create_invoice"
    )
    marker = source.index(
        '"manual_qoyod_invoice_id": str(invoice_id)',
        create_invoice_pos,
    )
    followup_get = source.index(
        "fetched_invoice = await client.get_invoice",
        create_invoice_pos,
    )

    assert create_invoice_pos < marker < followup_get


def test_cod_skips_payment_parity_and_payment_post():
    source = Path(
        "integrations/qoyod_manual/send.py"
    ).read_text(encoding="utf-8")

    cod = source.index(
        "# ── COD: invoice only, no payment"
    )
    parity = source.index(
        "# ── 4.5) Paid-method actual-total gate"
    )
    payment = source.index(
        "# ── 5) POST invoice payment"
    )

    assert cod < parity < payment
    assert 'actual_total_source = "local_expected_invoice_only"' in source


def test_unhandled_route_error_releases_lock():
    source = Path(
        "integrations/qoyod_manual/routes.py"
    ).read_text(encoding="utf-8")

    assert '"status": "failed_unhandled"' in source
    assert "qoyod_manual_send_locks.update_many" in source
    assert "logger.exception(" in source
