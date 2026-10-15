"""Offline regression for credential leakage in emitted runtime logs."""
from __future__ import annotations

import ast
from io import StringIO
import logging
from pathlib import Path

import httpx
import pytest

from sensitive_logging import (
    SensitiveDataFormatter,
    configure_sensitive_logging,
    redact_sensitive_log_text,
)


@pytest.fixture
def log_output():
    """Keep the test runner's own handlers/formatters and levels unchanged."""
    root = logging.getLogger()
    prior_handlers, prior_level = root.handlers[:], root.level
    loggers = [root, *[
        logger for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]]
    prior_formatters = {handler: handler.formatter for logger in loggers for handler in logger.handlers}
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    try:
        yield output
    finally:
        root.handlers, root.level = prior_handlers, prior_level
        for old_handler, formatter in prior_formatters.items():
            old_handler.setFormatter(formatter)
        handler.close()


def test_real_httpx_info_event_redacts_credentials_without_changing_request(log_output):
    configure_sensitive_logging()
    sent = []
    params = {
        "access_token": "fixture-access-token",
        "appsecret_proof": "fixture-secret-proof",
        "limit": "10",
    }

    def respond(request):
        sent.append(request)
        return httpx.Response(200, json={"data": [{"id": "fixture-result"}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        response = client.get("https://graph.facebook.com/v25.0/fixture/insights", params=params)
        # Provider pagination supplies a complete URL, bypassing params dicts.
        client.get("https://graph.facebook.com/v25.0/fixture/insights?after=page2&access_token=fixture-page-token")

    assert response.json() == {"data": [{"id": "fixture-result"}]}
    assert dict(sent[0].url.params) == params
    assert sent[1].url.params["access_token"] == "fixture-page-token"
    rendered = log_output.getvalue()
    for secret in (*list(params.values())[:2], "fixture-page-token"):
        assert secret not in rendered
    assert rendered.count("HTTP Request: GET") == 2
    assert '"HTTP/1.1 200 OK"' in rendered
    assert "graph.facebook.com/v25.0/fixture/insights" in rendered
    assert "limit=10" in rendered and "after=page2" in rendered


@pytest.mark.parametrize("key", [
    "access_token", "appsecret_proof", "client_secret", "app_secret",
    "fb_exchange_token", "input_token", "refresh_token", "id_token",
    "api_key", "api_secret", "authorization", "password", "code",
    "ACCESS_TOKEN", "%61ccess%5Ftoken", "appsecret%5fproof", "access-token",
])
def test_query_aliases_duplicates_and_encoded_keys(key):
    text = f"GET https://graph.facebook.com/oauth/access_token?{key}=fixture%2Bsecret%26value&limit=5&{key}=second-secret"
    rendered = redact_sensitive_log_text(text)
    assert "fixture" not in rendered and "second-secret" not in rendered
    assert "/oauth/access_token?" in rendered and "limit=5" in rendered
    assert rendered.count("[REDACTED]") == 2
    assert redact_sensitive_log_text(rendered) == rendered


@pytest.mark.parametrize("text", [
    '{"access_token": "fixture secret with spaces"}',
    "{'appsecret_proof': 'fixture secret with spaces'}",
    r'{"client_secret": "fixture\"embedded quote"}',
    "input_token=fixture-secret, operation=debug",
    "headers=[(b'authorization', b'Bearer fixture-secret')]",
    "Authorization: Bearer fixture-secret",
])
def test_reflected_fields_and_bearer_headers(text):
    rendered = redact_sensitive_log_text(text)
    assert "fixture" not in rendered
    assert "[REDACTED]" in rendered
    assert redact_sensitive_log_text(rendered) == rendered


@pytest.mark.parametrize("key", ["access_token", "code", "%61ccess_token"])
@pytest.mark.parametrize("value", ["fixture)tail", "fixture;tail", "fixture}tail"])
def test_httpx_pagination_url_redacts_entire_credential_value(log_output, key, value):
    configure_sensitive_logging()
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        response = client.get(f"https://graph.facebook.com/me?{key}={value}&limit=5")
    assert response.url.params["access_token" if key == "%61ccess_token" else key] == value
    rendered = log_output.getvalue()
    assert "fixture" not in rendered and "tail" not in rendered
    assert "HTTP/1.1 200 OK" in rendered and "limit=5" in rendered


@pytest.mark.parametrize("value", ['"fixture quoted secret"', "'fixture quoted secret'", r'"fixture\"secret"'])
def test_quoted_query_values_are_not_partially_replaced(value):
    rendered = redact_sensitive_log_text(f"GET /callback?access_token={value}&limit=5")
    assert "fixture" not in rendered and "secret" not in rendered
    assert "limit=5" in rendered


def test_exception_chain_cached_traceback_and_stack_are_sanitized(log_output):
    configure_sensitive_logging()
    logger = logging.getLogger("fixture.meta")
    try:
        try:
            raise httpx.RequestError("GET https://graph.facebook.com/me?access_token=fixture-cause")
        except httpx.RequestError as cause:
            raise RuntimeError("provider failed appsecret_proof=fixture-error") from cause
    except RuntimeError:
        logger.exception("Meta request failed operation=insights status=502")
    rendered = log_output.getvalue()
    assert "fixture-cause" not in rendered and "fixture-error" not in rendered
    assert "RuntimeError" in rendered and "RequestError" in rendered
    assert "Traceback" in rendered and "operation=insights status=502" in rendered

    event = logging.LogRecord("fixture", logging.ERROR, __file__, 1, "cached", (), None)
    event.exc_text = "cached failure https://graph.facebook.com/me?input_token=fixture-cache"
    event.stack_info = "Stack https://graph.facebook.com/me?client_secret=fixture-stack"
    output = SensitiveDataFormatter().format(event)
    assert "fixture-cache" not in output and "fixture-stack" not in output
    assert "cached failure" in output and "Stack" in output
    assert "fixture-cache" in event.exc_text and "fixture-stack" in event.stack_info


def test_existing_nonpropagating_uvicorn_formatter_and_access_filter(log_output):
    from uvicorn.logging import AccessFormatter

    # Exercise the real filter without importing the unrelated provider graph.
    # Its only dependency is logging; no credentials or app initialization.
    source = Path(__file__).parents[1] / "integrations_control_center" / "tiktok_creator_api.py"
    classes = [node for node in ast.parse(source.read_text()).body
               if isinstance(node, ast.ClassDef) and node.name == "_CallbackAccessLogFilter"]
    assert len(classes) == 1
    namespace = {"logging": logging}
    exec(compile(ast.Module(body=classes, type_ignores=[]), str(source), "exec"), namespace)
    callback_filter = namespace["_CallbackAccessLogFilter"]()

    logger = logging.getLogger("fixture.uvicorn.access")
    old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
    output = StringIO()
    handler = logging.StreamHandler(output)
    original = AccessFormatter('%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    handler.setFormatter(original)
    handler.addFilter(callback_filter)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        configure_sensitive_logging()
        installed = handler.formatter
        configure_sensitive_logging()
        assert handler.formatter is installed
        assert installed.formatter is original
        logger.info('%s - "%s %s HTTP/%s" %d', "fixture-client", "GET", "/api/integrations-v2/tiktok/callback?code=fixture-code&state=fixture-state", "1.1", 303)
        logger.info('%s - "%s %s HTTP/%s" %d', "fixture-client", "GET", "/api/fixture?access_token=fixture-query&page=1", "1.1", 200)
        rendered = output.getvalue()
        for secret in ("fixture-code", "fixture-state", "fixture-query"):
            assert secret not in rendered
        assert "303" in rendered and "200" in rendered
        assert "/api/fixture?access_token=[REDACTED]&page=1" in rendered
        assert handler.level == logging.NOTSET and logger.propagate is False
    finally:
        logger.handlers, logger.level, logger.propagate = old_handlers, old_level, old_propagate
        handler.close()


def test_default_formatter_multiple_handlers_and_ordinary_messages(log_output):
    root = logging.getLogger()
    other_output = StringIO()
    other_handler = logging.StreamHandler(other_output)
    root.addHandler(other_handler)
    try:
        configure_sensitive_logging()
        logger = logging.getLogger("fixture.ordinary")
        ordinary = "GET https://graph.facebook.com/me?fields=id,name&limit=10 status=200 code=190"
        logger.info(ordinary)
        logger.info("url=%s", "https://graph.facebook.com/me?access_token=fixture-multiple")
        for rendered in (log_output.getvalue(), other_output.getvalue()):
            assert "fixture-multiple" not in rendered
            assert ordinary in rendered
        record = logging.LogRecord("fixture", logging.INFO, __file__, 1, "%s %d", ("مرحبا", 200), None)
        assert SensitiveDataFormatter().format(record) == "مرحبا 200"
        assert record.args == ("مرحبا", 200) and record.msg == "%s %d"
    finally:
        root.removeHandler(other_handler)
        other_handler.close()


def test_server_installs_boundary_before_provider_imports_without_importing_server():
    # Never import server (loads runtime .env/DB). Execute only its bootstrap
    # statements with an injected function, proving their order from the AST.
    tree = ast.parse((Path(__file__).parents[1] / "server.py").read_text())
    assert isinstance(tree.body[1], ast.ImportFrom)
    assert tree.body[1].module == "sensitive_logging"
    assert isinstance(tree.body[2], ast.Expr)
    assert isinstance(tree.body[2].value, ast.Call)
    invoked = []
    namespace = {"configure_sensitive_logging": lambda: invoked.append(True)}
    exec(compile(ast.Module(body=[tree.body[2]], type_ignores=[]), "server-bootstrap", "exec"), namespace)
    assert invoked == [True]
