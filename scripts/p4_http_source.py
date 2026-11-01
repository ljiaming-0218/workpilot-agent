"""Loopback-only HTTP test source: a checkout calls a controllable dependency.

The 503 and HTTPError are produced by real HTTP requests. This is an intentionally
faulted local test, not production data or a fabricated tool fixture.
"""

import argparse
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Lock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4


def make_server(port: int = 8765) -> ThreadingHTTPServer:
    observations = deque(maxlen=100)
    lock = Lock()
    fault = {"enabled": True}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # No request body, credentials or arbitrary URL query logging.

        def respond(self, status: int, data: dict):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/observations":
                with lock:
                    items = list(observations)
                return self.respond(200, {"source": "local-http-test", "items": items})
            if self.path == "/dependency":
                return self.respond(503 if fault["enabled"] else 200,
                                    {"available": not fault["enabled"]})
            if self.path != "/checkout":
                return self.respond(404, {"error": "NOT_FOUND"})
            request_id = "p4-http-" + uuid4().hex
            origin = f"http://127.0.0.1:{self.server.server_port}"
            try:
                with build_opener(ProxyHandler({})).open(origin + "/dependency", timeout=3) as response:
                    response.read()
            except HTTPError as exc:
                exc.close()
                item = {"request_id": request_id, "created_at": datetime.now(timezone.utc).isoformat(),
                        "level": "ERROR", "error_type": "DEPENDENCY_HTTP_ERROR",
                        "message": f"[LOCAL_HTTP_TEST] GET /checkout -> 503; GET {origin}/dependency -> {exc.code}; observed HTTPError",
                        "stack_trace": None}
                with lock:
                    observations.append(item)
                return self.respond(503, {"error": "DEPENDENCY_HTTP_ERROR", "request_id": request_id})
            return self.respond(200, {"ok": True, "request_id": request_id})

        def do_POST(self):
            if self.path not in {"/control/recover", "/control/fail"}:
                return self.respond(404, {"error": "NOT_FOUND"})
            fault["enabled"] = self.path == "/control/fail"
            self.respond(200, {"fault_enabled": fault["enabled"]})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f"Local HTTP test source: http://127.0.0.1:{server.server_port}/checkout", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
