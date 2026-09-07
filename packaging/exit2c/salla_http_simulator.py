"""Bounded synthetic HTTP provider. Never forwards requests or emits credentials."""
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

API = "http://127.0.0.1:8093/admin/v2"
AUTH = "http://127.0.0.1:8093"
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
        self.counts = dict(simulated_provider_calls=0, unexpected=0, status_writes=0,
                           denied=0, shipping_attempted=0, shipping_failed=0)
        self.lock = threading.Lock()

    def respond(self, method, target, authorization, body):
        with self.lock:
            return self._respond(method, target, authorization, body)

    def _respond(self, method, target, authorization, body):
        # Counters have a separate control route and contain no request values.
        if method == "GET" and target == "/__fixture__/counts" and not body:
            return 200, dict(self.counts)
        self.counts["simulated_provider_calls"] += 1
        if not hmac.compare_digest(authorization, "Bearer " + self.token):
            self.counts["denied"] += 1
            return 403, {"error": {"code": "fixture_auth_rejected"}}
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return self.reject()
        path = parsed.path
        if method == "GET" and path == "/admin/v2/orders/statuses" and not parsed.query and not body:
            if self.mode == "unavailable":
                self.counts["denied"] += 1
                return 503, {"error": {"code": "fixture_unavailable"}}
            return 200, {"data": list(STATUS.values())}
        for internal_id in ORDER_IDS:
            order_path = "/admin/v2/orders/" + internal_id
            if method == "GET" and path == order_path and not parsed.query and not body:
                status = STATUS.get(self.statuses[internal_id], {"slug": "under_review"})
                return 200, {"data": {"id": internal_id, "reference_id": internal_id[4:], "status": status}}
            if method == "POST" and path == order_path + "/status" and not parsed.query:
                if not isinstance(body, dict) or set(body) != {"status_id"} or type(body["status_id"]) is not int:
                    return self.reject()
                next_status = body["status_id"]
                current = self.statuses[internal_id]
                if next_status not in STATUS or (next_status == 72 and current not in {71, 72}) or (next_status == 71 and current == 72):
                    return self.reject()
                if self.mode in {"deny", "unavailable"}:
                    self.counts["denied"] += 1
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
        return self.reject()

    def reject(self):
        self.counts["unexpected"] += 1
        return 422, {"error": {"code": "fixture_unexpected_request"}}


def server(fixture, port=8093):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

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
                    code, result = fixture.reject()
            payload = json.dumps(result, ensure_ascii=True).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = handle_request

    instance = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    instance.daemon_threads = True
    return instance


if __name__ == "__main__":
    validate_addresses(os.environ)
    fixture = Fixture(os.environ["EXIT2D_SIM_TOKEN"], os.environ["EXIT2D_SIM_MODE"])
    with server(fixture) as httpd:
        httpd.serve_forever()
