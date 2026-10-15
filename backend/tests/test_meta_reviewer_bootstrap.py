import hashlib

from fastapi import FastAPI

import meta_reviewer_bootstrap as bootstrap


def test_bootstrap_token_uses_digest_only(monkeypatch):
    fixture = "fixture-token-that-is-not-a-production-secret"
    monkeypatch.setattr(
        bootstrap,
        "TOKEN_DIGEST",
        hashlib.sha256(fixture.encode("utf-8")).hexdigest(),
    )

    assert bootstrap._valid_token(fixture) is True
    assert bootstrap._valid_token(fixture + "-wrong") is False


def test_bootstrap_route_installation_is_idempotent():
    app = FastAPI()
    fake_db = object()

    bootstrap.install_meta_reviewer_bootstrap(app, fake_db)
    bootstrap.install_meta_reviewer_bootstrap(app, fake_db)

    matching = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/auth/meta-reviewer-bootstrap"
    ]
    assert len(matching) == 1
    assert matching[0].include_in_schema is False


def test_bootstrap_is_fixed_to_review_account_and_one_year():
    assert bootstrap.REVIEWER_EMAIL == "meta-reviewer@mezansalla.com"
    assert bootstrap.OWNER_EMAIL == "amasi.jewelery@gmail.com"
    assert bootstrap.ACCESS_DAYS >= 365
