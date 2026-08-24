"""A real HTTP server used as an external-registry sandbox.

Tests exercise the production HTTP client over a real socket: the client's timeout,
status-code and payload handling are all real, only the counterparty is local.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from taxstamp.jsontypes import JsonObject


@dataclass
class Script:
    """The response programme for the sandbox."""

    status: int = 200
    body: JsonObject = field(default_factory=dict)
    delay_seconds: float = 0.0
    raw_body: str | None = None
    requests: list[JsonObject] = field(default_factory=list)
    anchor_body: JsonObject | None = None


class RegistrySandbox:
    def __init__(self) -> None:
        self.script = Script()
        script = self.script

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                length = int(self.headers.get("content-length", "0"))
                payload = self.rfile.read(length)
                try:
                    decoded = json.loads(payload or b"{}")
                except json.JSONDecodeError:
                    decoded = {}
                if isinstance(decoded, dict):
                    script.requests.append(decoded)
                if script.delay_seconds:
                    threading.Event().wait(script.delay_seconds)
                if self.path.endswith("/anchors"):
                    raw = json.dumps(
                        script.anchor_body
                        if script.anchor_body is not None
                        else {
                            "root": decoded.get("root", "") if isinstance(decoded, dict) else "",
                            "reference": f"ANCHOR-{len(script.requests)}",
                            "anchored_at": "2026-01-01T00:00:00+00:00",
                        }
                    )
                elif script.raw_body is not None:
                    raw = script.raw_body
                else:
                    raw = json.dumps(script.body)
                encoded = raw.encode("utf-8")
                self.send_response(script.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        host, port = self._server.server_address[:2]
        self.base_url = f"http://{host}:{port}"

    def start(self) -> str:
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def compliant(self, reference: str = "REG-OK-1") -> None:
        self.script.status = 200
        self.script.raw_body = None
        self.script.body = {
            "compliant": True,
            "reference": reference,
            "checked_at": "2026-01-01T00:00:00+00:00",
        }

    def non_compliant(self, reason: str = "licence suspended") -> None:
        self.script.status = 200
        self.script.raw_body = None
        self.script.body = {
            "compliant": False,
            "reference": "REG-DENY-1",
            "reason": reason,
            "checked_at": "2026-01-01T00:00:00+00:00",
        }

    def anchor_response(self, body: JsonObject | None) -> None:
        """Override the anchoring response; ``None`` restores the echoing default."""
        self.script.anchor_body = body
