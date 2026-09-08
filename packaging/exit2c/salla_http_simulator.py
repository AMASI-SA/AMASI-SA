"""Bounded synthetic HTTP provider. Never forwards requests or emits credentials."""
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

API = "http://127.0.0.1:8093/admin/v2"
AUTH = "http://127.0.0.1:8093"
CATEGORIES = ('PRODUCT_DETAIL', 'PRODUCT_SEARCH', 'ORDER_DETAIL', 'ORDER_STATUS_LIST', 'ORDER_STATUS_WRITE', 'OAUTH', 'OTHER')
METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD', 'OTHER')
REASONS = ('UNKNOWN_ROUTE', 'QUERY_SHAPE', 'BODY_SHAPE', 'ID_NOT_IN_FIXTURE', 'AUTH_REJECTED')
COUNTER_KEYS = ('simulated_provider_calls', 'unexpected', 'status_writes', 'denied',
                'shipping_attempted', 'shipping_failed', 'status_write_denied',
                'status_discovery_unavailable', 'auth_rejected')
CLASS_KEYS = frozenset('|'.join((c, m, r)) for c in CATEGORIES for m in METHODS for r in REASONS)


def classify(method, target):
    method = method if method in METHODS else 'OTHER'
    try:
        parsed = urlsplit(target)
    except ValueError:
        return 'OTHER', method
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return 'OTHER', method
    parts = parsed.path.split('/')
    if parsed.path == '/admin/v2/products': category = 'PRODUCT_SEARCH'
    elif len(parts) == 5 and parts[:4] == ['', 'admin', 'v2', 'products']: category = 'PRODUCT_DETAIL'
    elif parsed.path == '/admin/v2/orders/statuses': category = 'ORDER_STATUS_LIST'
    elif len(parts) == 6 and parts[:4] == ['', 'admin', 'v2', 'orders'] and parts[-1] == 'status': category = 'ORDER_STATUS_WRITE'
    elif len(parts) == 5 and parts[:4] == ['', 'admin', 'v2', 'orders']: category = 'ORDER_DETAIL'
    elif parts[:2] == ['', 'oauth2']: category = 'OAUTH'
    else: category = 'OTHER'
    return category, method


ORDER_IDS = {"raw-EXIT2D-1001", "raw-EXIT2D-1002"}
STATUS = {
    71: {"id": 71, "name": "\u062a\u0645 \u0627\u0644\u0645\u0631\u0627\u062c\u0639\u0629", "slug": "reviewed"},
    72: {"id": 72, "name": "\u0642\u064a\u062f \u0627\u0644\u062a\u0646\u0641\u064a\u0630", "slug": "in_progress"},
}


def validate_addresses(env):
    if env.get("SALLA_API_BASE") != API or env.get("SALLA_AUTH_BASE") != AUTH:
        raise ValueError("SIMULATOR_ADDRESS_REJECTED")
    if any(value for key, value in env.items() if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}):
        raise ValueError("SIMULATOR_PROXY_REJECTED")


class Fixture:
    def __init__(self, token, mode):
        if mode not in {"success", "deny", "unavailable"} or len(token) < 32:
            raise ValueError("SIMULATOR_FIXTURE_REJECTED")
        self.token, self.mode = token, mode
        self.statuses = {key: None for key in ORDER_IDS}
        self.counts = dict.fromkeys(COUNTER_KEYS, 0)
        self.unexpected_classes = dict.fromkeys(sorted(CLASS_KEYS), 0)
        self.lock = threading.Lock()

    def respond(self, method, target, authorization, body):
        with self.lock:
            return self._respond(method, target, authorization, body)

    def _respond(self, method, target, authorization, body):
        # Counters have a separate control route and contain no request values.
        if method == "GET" and target == "/__fixture__/counts" and not body:
            return 200, {**self.counts, "unexpected_by_class": {k: v for k, v in self.unexpected_classes.items() if v}}
        self.counts["simulated_provider_calls"] += 1
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + self.token).encode()):
            self.counts["denied"] += 1
            self.counts["auth_rejected"] += 1
            self.reject(method, target, "AUTH_REJECTED")
            return 403, {"error": {"code": "fixture_auth_rejected"}}
        try:
            parsed = urlsplit(target)
        except ValueError:
            return self.reject(method, target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return self.reject(method, target)
        path = parsed.path
        if method == "GET" and path == "/admin/v2/orders/statuses" and not parsed.query and not body:
            if self.mode == "unavailable":
                self.counts["denied"] += 1
                self.counts["status_discovery_unavailable"] += 1
                return 503, {"error": {"code": "fixture_unavailable"}}
            return 200, {"data": list(STATUS.values())}
        for internal_id in ORDER_IDS:
            order_path = "/admin/v2/orders/" + internal_id
            if method == "GET" and path == order_path and not parsed.query and not body:
                status = STATUS.get(self.statuses[internal_id], {"slug": "under_review"})
                return 200, {"data": {"id": internal_id, "reference_id": internal_id[4:], "status": status}}
            if method == "POST" and path == order_path + "/status" and not parsed.query:
                if not isinstance(body, dict) or set(body) != {"status_id"} or type(body["status_id"]) is not int:
                    return self.reject(method, target, "BODY_SHAPE")
                next_status = body["status_id"]
                current = self.statuses[internal_id]
                if next_status not in STATUS or (next_status == 72 and current not in {71, 72}) or (next_status == 71 and current == 72):
                    return self.reject(method, target, "BODY_SHAPE")
                if self.mode in {"deny", "unavailable"}:
                    self.counts["denied"] += 1
                    if self.mode == "deny": self.counts["status_write_denied"] += 1
                    return (403 if self.mode == "deny" else 503), {"error": {"code": "fixture_status_rejected"}}
                self.statuses[internal_id] = next_status
                self.counts["status_writes"] += 1
                return 200, {"data": {"id": internal_id, "status": STATUS[next_status]}}
        if method == "GET" and path == "/admin/v2/orders" and not body:
            query = parse_qs(parsed.query, keep_blank_values=True)
            if query in [{"keyword": [key[4:]], "format": ["light"], "per_page": ["10"]} for key in ORDER_IDS]:
                self.counts["shipping_attempted"] += 1
                self.counts["shipping_failed"] += 1
                return 503, {"error": {"code": "fixture_shipping_lookup_unavailable"}}
        # OAuth is deliberately refused: fixtures have unexpired tokens. No
        # refresh token, shipment, product discovery, redirect, or broad success.
        category, _ = classify(method, target)
        reason = 'UNKNOWN_ROUTE'
        if category in {'ORDER_DETAIL', 'ORDER_STATUS_WRITE'} and path.split('/')[4] not in ORDER_IDS:
            reason = 'ID_NOT_IN_FIXTURE'
        elif category in {'ORDER_DETAIL', 'ORDER_STATUS_WRITE', 'ORDER_STATUS_LIST'} and parsed.query:
            reason = 'QUERY_SHAPE'
        elif category in {'ORDER_DETAIL', 'ORDER_STATUS_LIST'} and body:
            reason = 'BODY_SHAPE'
        elif path == '/admin/v2/orders' and method == 'GET':
            reason = 'QUERY_SHAPE' if not body else 'BODY_SHAPE'
        return self.reject(method, target, reason)

    def reject(self, method, target, reason='UNKNOWN_ROUTE'):
        category, method = classify(method, target)
        if reason not in REASONS: reason = 'UNKNOWN_ROUTE'
        self.unexpected_classes['|'.join((category, method, reason))] += 1
        self.counts["unexpected"] += 1
        return 422, {"error": {"code": "fixture_unexpected_request"}}


def server(fixture, port=8093):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def __getattr__(self, name):
            if name.startswith('do_'):
                return self.handle_request  # Unsupported verbs are counted as OTHER.
            raise AttributeError(name)

        def handle_request(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 2048 or self.headers.get("Transfer-Encoding"):
                    raise ValueError
                body = json.loads(self.rfile.read(length)) if length else None
                code, result = fixture.respond(self.command, self.path, self.headers.get("Authorization", ""), body)
            except (ValueError, UnicodeError):
                with fixture.lock:
                    fixture.counts["simulated_provider_calls"] += 1
                    code, result = fixture.reject(self.command, self.path, "BODY_SHAPE")
            payload = json.dumps(result, ensure_ascii=True).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD": self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = handle_request

    instance = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    instance.daemon_threads = True
    return instance


if __name__ == "__main__":
    validate_addresses(os.environ)
    fixture = Fixture(os.environ["EXIT2D_SIM_TOKEN"], os.environ["EXIT2D_SIM_MODE"])
    with server(fixture) as httpd:
        httpd.serve_forever()
