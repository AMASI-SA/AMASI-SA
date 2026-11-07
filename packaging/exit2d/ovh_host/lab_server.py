"""Fixture upstream/listeners only; does not represent application authentication."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({'fixture': True, 'forwarded_proto': self.headers.get('X-Forwarded-Proto')}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self, *args): pass
for port in (22, 80, 443, 10000, 27017):
    threading.Thread(target=ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever, daemon=True).start()
while True: time.sleep(60)
