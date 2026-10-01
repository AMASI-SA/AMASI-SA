"""Public legacy opening mutations must enter the governed onboarding flow."""
from fastapi import HTTPException


def reject_alternate_opening():
    raise HTTPException(409, detail={
        "code": "opening_onboarding_required",
        "onboarding_path": "/api/accounting-module/onboarding",
        "live_actions_enabled": False,
    })


def guard_opening_entry(entry):
    if entry and entry.get("entry_type") == "opening_balance":
        reject_alternate_opening()
