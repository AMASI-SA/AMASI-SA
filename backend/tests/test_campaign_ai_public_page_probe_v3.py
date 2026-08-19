import asyncio

import campaign_ai_public_page_probe_v3 as probe


def test_cross_host_destination_is_rejected_before_network(monkeypatch):
    def public_dns(_host):
        raise AssertionError("DNS/network guard should not be reached for host mismatch")

    monkeypatch.setattr(probe, "_public_dns", public_dns)
    result = asyncio.run(probe.probe_product_page(
        "https://evil.example/product",
        canonical_url="https://amasi-sa.com/product/1",
    ))
    assert result["status"] == "PRODUCT_URL_WRONG_DESTINATION"
    assert result["reason"] == "destination_not_on_trusted_public_product_host"


def test_private_ip_cannot_pass_allowed_guard(monkeypatch):
    monkeypatch.setattr(probe, "_public_dns", lambda _host: False)
    allowed = asyncio.run(probe._allowed(
        "https://amasi-sa.com/p/1",
        "amasi-sa.com",
    ))
    assert allowed is False


def test_host_parser_rejects_embedded_credentials():
    assert probe._host("https://user:pass@amasi-sa.com/p/1") is None


def test_visible_text_strips_script_and_tags():
    source = "<html><script>secret()</script><style>.x{}</style><body><h1>منتج أماسي</h1><p>وصف واضح</p></body></html>"
    visible = probe._visible_text(source)
    assert "منتج أماسي" in visible
    assert "وصف واضح" in visible
    assert "secret" not in visible


def test_visible_text_strips_spaced_end_tags():
    source = (
        "<html><body>"
        "<script>secret()</script >"
        "<style>.hidden{display:none}</style   >"
        "<noscript>fallback-only</noscript\t>"
        "<p>Visible product text</p>"
        "</body></html>"
    )
    visible = probe._visible_text(source)
    assert "Visible product text" in visible
    assert "secret" not in visible
    assert "hidden" not in visible
    assert "fallback-only" not in visible


def test_visible_text_strips_malformed_closing_tag_payloads():
    source = (
        "<html><body>"
        "<script>script-secret</script\t\n bar>"
        "<style>style-secret</style data-bad>"
        "<noscript>noscript-secret</noscript/x>"
        "<p>Safe visible content</p>"
        "</body></html>"
    )
    visible = probe._visible_text(source)
    assert "Safe visible content" in visible
    assert "script-secret" not in visible
    assert "style-secret" not in visible
    assert "noscript-secret" not in visible


def test_meta_parser_reads_og_metadata():
    source = '<meta property="og:title" content="منتج مميز"><meta name="description" content="وصف المنتج">'
    assert probe._meta_content(source, prop="og:title") == "منتج مميز"
    assert probe._meta_content(source, name="description") == "وصف المنتج"
