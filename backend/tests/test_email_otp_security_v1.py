import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from email_otp_policy import requires_email_otp
from email_otp_security import (
    _challenge_token,
    _decode_challenge_token,
    _now,
    _send_email_sync,
    generate_otp,
    mask_email,
    otp_digest,
    smtp_settings,
    validate_email_otp_runtime,
)


class _AssignmentCollection:
    def __init__(self, rows=None):
        self.rows = {str(row.get("user_id")): dict(row) for row in (rows or [])}

    async def find_one(self, query, projection=None):
        row = self.rows.get(str(query.get("user_id") or ""))
        return dict(row) if row else None


class _FakeDb:
    def __init__(self, assignments=None):
        self.assignments = _AssignmentCollection(assignments)

    def __getitem__(self, name):
        assert name == "mezan_role_assignments_v2"
        return self.assignments


def _requires(db, user):
    return asyncio.run(requires_email_otp(db, user))


def test_owner_never_uses_email_otp(monkeypatch):
    monkeypatch.setenv("EMAIL_OTP_ENABLED", "1")
    assert _requires(_FakeDb(), {"id": "owner-1", "role": "owner", "email_otp_required": True}) is False


def test_every_non_owner_employee_requires_email_otp(monkeypatch):
    monkeypatch.setenv("EMAIL_OTP_ENABLED", "1")
    employees = [
        {"id": "admin-1", "role": "admin"},
        {"id": "acct-1", "role": "accountant"},
        {"id": "warehouse-1", "role": "viewer"},
        {"id": "support-1", "role": "customer_service"},
        {"id": "employee-1", "role": "employee"},
    ]
    for employee in employees:
        assert _requires(_FakeDb(), employee) is True


def test_false_document_flag_cannot_disable_employee_otp(monkeypatch):
    monkeypatch.setenv("EMAIL_OTP_ENABLED", "1")
    assert _requires(
        _FakeDb(),
        {"id": "employee-2", "role": "viewer", "email_otp_required": False},
    ) is True


def test_meta_reviewer_is_the_only_non_owner_exception(monkeypatch):
    monkeypatch.setenv("EMAIL_OTP_ENABLED", "1")
    reviewer = {
        "id": "meta-reviewer-1",
        "role": "meta_reviewer",
        "email": "meta-reviewer@mezansalla.com",
    }
    assert _requires(_FakeDb(), reviewer) is False

    # The exception is role-bound; a similarly named normal employee must
    # still complete email OTP.
    assert _requires(
        _FakeDb(),
        {
            "id": "employee-3",
            "role": "viewer",
            "email": "meta-reviewer@mezansalla.com",
        },
    ) is True


def test_deployment_flag_cannot_disable_employee_otp(monkeypatch):
    monkeypatch.setenv("EMAIL_OTP_ENABLED", "0")
    assert _requires(_FakeDb(), {"id": "admin-1", "role": "admin"}) is True


def test_otp_is_six_digits_and_digest_never_stores_plaintext(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-only-email-otp-secret-that-is-long-enough")
    code = generate_otp()
    assert len(code) == 6
    assert code.isdigit()
    digest = otp_digest("challenge-a", code)
    assert digest != code
    assert len(digest) == 64
    assert digest != otp_digest("challenge-b", code)


def test_challenge_token_is_signed_typed_and_short_lived(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-only-email-otp-secret-that-is-long-enough")
    token = _challenge_token(
        user_id="user-1",
        jti="jti-1",
        expires_at=_now() + timedelta(minutes=5),
    )
    payload = _decode_challenge_token(token)
    assert payload["sub"] == "user-1"
    assert payload["jti"] == "jti-1"
    assert payload["type"] == "email_otp_challenge"


def test_email_mask_does_not_reveal_full_local_part():
    masked = mask_email("employee@example.com")
    assert masked.startswith("e")
    assert masked.endswith("@example.com")
    assert "employee@example.com" != masked
    assert "mployee" not in masked


def test_runtime_is_always_fail_closed(monkeypatch):
    for key in (
        "EMAIL_OTP_SMTP_HOST",
        "EMAIL_OTP_SMTP_USERNAME",
        "EMAIL_OTP_SMTP_PASSWORD",
        "EMAIL_OTP_FROM_EMAIL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JWT_SECRET", "test-only-email-otp-secret-that-is-long-enough")
    monkeypatch.setenv("EMAIL_OTP_ENABLED", "0")
    with pytest.raises(RuntimeError):
        validate_email_otp_runtime()

    monkeypatch.setenv("EMAIL_OTP_ENABLED", "1")
    with pytest.raises(RuntimeError):
        validate_email_otp_runtime()

    monkeypatch.setenv("EMAIL_OTP_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("EMAIL_OTP_SMTP_USERNAME", "mezan@example.test")
    monkeypatch.setenv("EMAIL_OTP_SMTP_PASSWORD", "not-a-real-secret")
    settings = smtp_settings()
    assert settings.host == "smtp.example.test"
    assert settings.from_email == "mezan@example.test"
    validate_email_otp_runtime()


def test_auth_middleware_order_and_frontend_contract():
    auth_source = Path("backend/auth.py").read_text(encoding="utf-8")
    assert auth_source.index("await install_mfa_security(app, db,") < auth_source.index(
        "await install_email_otp_security(app, db)"
    )

    login_source = Path("frontend/src/pages/Login.jsx").read_text(encoding="utf-8")
    assert 'result?.mfa_channel === "email"' in login_source
    assert 'api.post("/auth/email-otp/verify"' in login_source
    assert 'api.post("/auth/email-otp/resend"' in login_source
    assert "حساب المالك في ميزان يتطلب تطبيق مصادقة" in login_source


def test_email_message_uses_amasi_brand_and_recipient_name(monkeypatch):
    sent = []

    class FakeSmtp:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            pass

        def send_message(self, message):
            sent.append(message)

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setenv("EMAIL_OTP_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EMAIL_OTP_FROM_EMAIL", "no-reply@example.com")
    monkeypatch.setenv("EMAIL_OTP_FROM_NAME", "AMASI")
    monkeypatch.setenv("EMAIL_OTP_SMTP_STARTTLS", "0")
    monkeypatch.setenv("EMAIL_OTP_SMTP_SSL", "0")
    monkeypatch.delenv("EMAIL_OTP_SMTP_USERNAME", raising=False)
    monkeypatch.delenv("EMAIL_OTP_SMTP_PASSWORD", raising=False)
    monkeypatch.setattr("email_otp_security.smtplib.SMTP", FakeSmtp)

    _send_email_sync("employee@example.com", "123456", "عرفات")

    assert len(sent) == 1
    message = sent[0]
    assert message.get_content_type() == "multipart/alternative"
    assert message["Subject"] == "رمز التحقق لتسجيل الدخول إلى نظام أماسي"
    assert message["From"] == "AMASI <no-reply@example.com>"
    plain_body = message.get_body(preferencelist=("plain",)).get_content()
    html_body = message.get_body(preferencelist=("html",)).get_content()
    assert "مرحبًا عرفات،" in plain_body
    assert "نظام أماسي" in plain_body
    assert "MEZAN" not in plain_body
    assert '<html lang="ar" dir="rtl">' in html_body
    assert 'align="right"' in html_body
    assert "direction:rtl" in html_body
    assert "text-align:right" in html_body
    assert '<strong dir="ltr"' in html_body
    assert "مرحبًا عرفات،" in html_body
    assert "123456" in html_body
    assert "MEZAN" not in html_body
