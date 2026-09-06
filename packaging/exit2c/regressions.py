"""Run inherited focused contracts separately from real HTTP acceptance."""
import os
import sys
sys.path.insert(0, "/opt/mezan/backend")
from independent_runtime import validate_before_import
validate_before_import("web")
# Public deterministic fixture from the inherited Qoyod test workflow.
# Set only inside this network-isolated regression process, after boundary validation.
os.environ["QOYOD_TOKEN_ENC_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["QOYOD_API_BASE"] = "https://qoyod.invalid"
# A historical test conditionally installs fake jwt/bcrypt when absent; load
# genuine distributions first and assert they remain genuine after collection.
sys.path.insert(0, "/opt/rehearsal")
import jwt
import bcrypt
assert jwt.__file__ and bcrypt.__file__
import pytest
names = [
    "test_independent_security_install.py", "test_login_security_v1.py",
    "test_progressive_login_security_v1.py", "test_mfa_security_v1.py",
    "test_email_otp_security_v1.py", "test_passkey_security_v1.py",
    "test_mobile_session_security.py", "test_browser_security.py",
    "test_runtime_stability_mongo_auth.py", "test_auth_session_revocation_v1.py",
    "test_mobile_app_manager_role.py", "test_mobile_app_permission_owner_resolution.py",
    "test_qoyod_manual_send_plan_b.py", "test_qoyod_plan_b_auto_send.py",
    "test_server_diagnostics_route_bindings.py", "test_startup_guard.py",
    "test_scheduler_readiness_gate.py", "test_qoyod_unsent_memory_bounds.py",
    "test_qoyod_unsent_range_counts.py", "test_qoyod_unsent_report_eligibility.py",
    "test_qoyod_reconciliation_v2.py", "test_preparation_pdf_media_text_gap.py",
    "test_preparation_pdf_card_file_number.py",
    "test_preparation_file_registry.py", "test_preparation_piece_operations.py",
    "test_qoyod_state_machine.py", "test_preparation_piece_execution_guard.py",
]
status = pytest.main(["--noconftest", "-p", "no:cacheprovider", "-q", "--tb=short", "/opt/rehearsal/test_boundary.py", *[
    "/opt/mezan/backend/tests/" + name for name in names
]])
assert sys.modules["jwt"] is jwt and sys.modules["bcrypt"] is bcrypt
raise SystemExit(status)
