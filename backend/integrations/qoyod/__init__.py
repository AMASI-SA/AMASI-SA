"""Qoyod Output Connector — Invoice push MVP.

Goal (June 2026 deadline):
    Receive Salla order → Make.com webhook → Mezan → Qoyod invoice + receipt.

Layered pipeline (per ADR-001 #6):
    Webhook  →  Inbox  →  Normalization  →  Business Rules
    →  Customer  →  Products  →  Invoice  →  Receipt  →  Completed.

Top-level modules:
    crypto.py       — Fernet for API keys (per ADR-001 #14 Secrets).
    models.py       — Pydantic models + index helpers for 5 collections.
    credentials.py  — encrypted credential store (shared style with Salla).
    api_client.py   — thin httpx wrapper around Qoyod REST API.
"""
