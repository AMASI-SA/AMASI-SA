"""Closed native shipping-contract integration; independent of the P02 phase gate.

This source-controlled gate is intentionally not configurable by an API payload,
owner role, database flag or environment variable. No existing shipping route
imports this module. Opening it requires another reviewed source change after
production evidence, native-ledger/report integration and UI acceptance exist.
"""
from fastapi import HTTPException


def native_contract_readiness() -> dict:
    return {
        "enabled": False,
        "state": "LOCKED",
        "code": "shipping_contract_native_path_locked",
        "production_evidence_service": "NOT_INTEGRATED",
        "ledger_report_integration": "NOT_CONNECTED",
        "current_shipping_routes": "UNCHANGED",
        "first_external_cod_sale": "cod_base_receivable_required",
    }


def require_native_contract_runtime() -> None:
    # Deliberately no bypass. Test adapters cannot open a production route.
    raise HTTPException(423, detail=native_contract_readiness())
