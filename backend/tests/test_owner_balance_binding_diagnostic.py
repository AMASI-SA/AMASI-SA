"""No identity or salary amount may be guessed to make the owner's balance visible."""
import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "owner_balance_diagnostic", Path(__file__).resolve().parents[2] /
    "scripts" / "diagnose_owner_balance_binding.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_explicit_owner_link_required_and_no_name_fallback():
    assert module.classify([], [], [], [], True) == (
        "NO_VERIFIED_OWNER_EMPLOYEE_LINK_DO_NOT_INFER_FROM_NAME")
    assert module.classify([], [], [], [], False) == (
        "NO_VERIFIED_OWNER_EMPLOYEE_LINK_DO_NOT_INFER_FROM_NAME")


def test_unique_existing_owner_salary_link_only_reports_candidate():
    legacy = [{"id": "legacy-explicit"}]
    v2 = [{"id": "v2-explicit", "account_user_id": None}]
    contracts = [{"employee_id": "v2-explicit"}]
    assert module.classify([], legacy, v2, contracts, True) == (
        "EXPLICIT_LEGACY_OWNER_LINK_WITH_V2_CONTRACT_REQUIRES_AUTHENTICATED_UAT")
    assert module.classify([], legacy, v2, [], True) != (
        "EXPLICIT_LEGACY_OWNER_LINK_WITH_V2_CONTRACT_REQUIRES_AUTHENTICATED_UAT")


def test_ambiguous_owner_claims_never_pass():
    assert module.classify([], [{"id": "a"}, {"id": "b"}],
                           [{"id": "v2"}], [{"employee_id": "v2"}], True) == (
        "AMBIGUOUS_IDENTITY_REQUIRES_OWNER_REVIEW")
    assert module.classify([{"id": "a"}, {"id": "b"}], [], [], [], True) == (
        "AMBIGUOUS_IDENTITY_REQUIRES_OWNER_REVIEW")


def test_direct_binding_requires_active_row():
    assert module.classify([{"id": "self", "status": "active"}],
                           [], [], [], True) == (
        "DIRECT_V2_ACCOUNT_LINK_PRESENT_CHECK_SESSION_PERMISSION")
    assert module.classify([{"id": "self", "status": "inactive"}],
                           [], [], [], True) != (
        "DIRECT_V2_ACCOUNT_LINK_PRESENT_CHECK_SESSION_PERMISSION")
