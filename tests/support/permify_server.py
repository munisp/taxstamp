"""A real HTTP policy-engine sandbox speaking Permify's check API.

The point of testing over a socket rather than against a fake client is that the real
failure modes - an error status, a truncated body, an unrecognised verdict, a connection
refused - are all reachable, and each of them must produce "unknown" rather than an
accidental allow.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from taxstamp.jsontypes import JsonObject


@dataclass
class EngineScript:
    status: int = 200
    #: Verdicts keyed by "entity_type:entity_id:permission:subject_id"; the default
    #: applies to any question not named.
    verdicts: dict[str, str] = field(default_factory=dict)
    default_verdict: str = "RESULT_DENIED"
    raw_body: str | None = None
    questions: list[JsonObject] = field(default_factory=list)
    authorizations: list[str | None] = field(default_factory=list)


class PermifySandbox:
    def __init__(self) -> None:
        self.script = EngineScript()
        script = self.script

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length)
                script.authorizations.append(self.headers.get("authorization"))
                try:
                    decoded = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    decoded = {}
                if isinstance(decoded, dict):
                    script.questions.append(decoded)
                if script.status != 200:
                    self.send_response(script.status)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                raw = (
                    script.raw_body
                    if script.raw_body is not None
                    else json.dumps({"can": script.verdicts.get(_key(decoded), script.default_verdict)})
                )
                encoded = raw.encode()
                self.send_response(200)
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

    def allow(self, *, entity_type: str, entity_id: str, permission: str, subject_id: str) -> None:
        self.script.verdicts[f"{entity_type}:{entity_id}:{permission}:{subject_id}"] = "RESULT_ALLOWED"


def _key(question: object) -> str:
    if not isinstance(question, dict):
        return ""
    entity = question.get("entity")
    subject = question.get("subject")
    permission = question.get("permission")
    if not isinstance(entity, dict) or not isinstance(subject, dict) or not isinstance(permission, str):
        return ""
    return f"{entity.get('type')}:{entity.get('id')}:{permission}:{subject.get('id')}"
