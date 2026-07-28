from integrations_control_center.models import ProviderCard
from openai_integration_status_support import _recount, openai_integration_card


def test_connected_openai_card_is_schema_valid_and_secret_safe(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-appear")
    monkeypatch.delenv("MEZAN_AI_IMAGE_ENABLED", raising=False)
    monkeypatch.delenv("MEZAN_OPENAI_IMAGE_MODEL", raising=False)

    card = openai_integration_card()
    validated = ProviderCard.model_validate(card).model_dump()
    assert validated["connection_status"] == "connected"
    assert validated["connection_provenance"] == "api_connection"
    assert validated["capabilities"]["analysis.generate"]["state"] == "available"
    assert validated["capabilities"]["images.execute"]["state"] == "planned"
    assert "must-not-appear" not in str(validated)


def test_disconnected_openai_card_is_not_counted_as_api_connection(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    card = openai_integration_card()
    validated = ProviderCard.model_validate(card).model_dump()
    assert validated["connection_status"] == "not_configured"
    assert validated["connection_provenance"] == "disconnected"


def test_summary_is_recounted_after_openai_card_is_added(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    providers = [
        {
            "connection_status": "not_configured",
            "connection_provenance": "disconnected",
            "health": {"status": "not_available"},
            "permissions": {"missing": []},
        },
        openai_integration_card(),
    ]
    summary = _recount({}, providers)
    assert summary["total"] == 2
    assert summary["connected"] == 1
    assert summary["api_connections"] == 1
    assert summary["disconnected"] == 1
