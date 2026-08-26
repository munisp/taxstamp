"""Low-impact loopback-only security assessment for the disposable Taxstamp stack."""

from __future__ import annotations

import json
import socket
import sys
from dataclasses import asdict, dataclass
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class Finding:
    identifier: str
    severity: str
    target: str
    evidence: str
    remediation: str


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: dict[str, str]


def request(url: str, method: str = "GET") -> Response:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("local security scanner permits only loopback HTTP targets")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    connection = HTTPConnection("127.0.0.1", parsed.port or 80, timeout=4)
    try:
        connection.request(method, path, headers={"User-Agent": "Taxstamp-Local-Security-Assessment/1.0"})
        response = connection.getresponse()
        return Response(
            status=response.status,
            headers={key.lower(): item for key, item in response.getheaders()},
        )
    finally:
        connection.close()


def tcp_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    findings: list[Finding] = []
    observed_ports = {
        8080: "Taxstamp API",
        19080: "APISIX gateway",
        18080: "Keycloak application",
        19000: "Keycloak management",
        13476: "Permify API",
        13478: "Permify HTTP service",
        19090: "Prometheus",
        13000: "Grafana",
        19092: "Kafka",
        55432: "PostgreSQL",
        56379: "Redis",
    }
    open_ports = {port: name for port, name in observed_ports.items() if tcp_open(port)}
    for port, name in open_ports.items():
        if port not in {19080, 18080, 13000}:
            findings.append(
                Finding(
                    identifier="HOST_EXPOSED_INTERNAL_SERVICE",
                    severity="high",
                    target=f"127.0.0.1:{port} ({name})",
                    evidence=(
                        "Service is reachable on the host loopback scan; the disposable Compose stack "
                        "publishes this port."
                    ),
                    remediation=(
                        "In persistent deployment, publish only the gateway and approved monitoring "
                        "endpoints behind private networking; keep state, broker, authorisation and "
                        "management ports on the internal Docker network."
                    ),
                )
            )

    checks = {
        "api": "http://127.0.0.1:8080",
        "gateway": "http://127.0.0.1:19080",
        "keycloak": "http://127.0.0.1:18080",
        "prometheus": "http://127.0.0.1:19090",
        "grafana": "http://127.0.0.1:13000",
    }
    api_docs = request(f"{checks['api']}/docs")
    if api_docs.status == 200:
        findings.append(
            Finding(
                identifier="API_DOCUMENTATION_EXPOSED",
                severity="medium",
                target=f"{checks['api']}/docs",
                evidence=(
                    "Interactive FastAPI documentation returned HTTP 200 on the directly published API "
                    "port."
                ),
                remediation=(
                    "Disable or access-restrict interactive documentation in persistent non-production "
                    "and production; publish only approved contract documentation through controlled "
                    "identity-aware access."
                ),
            )
        )

    direct_capability = request(f"{checks['api']}/v1/capabilities")
    gateway_capability = request(f"{checks['gateway']}/v1/capabilities")
    if direct_capability.status != 401 or gateway_capability.status != 401:
        findings.append(
            Finding(
                identifier="PROTECTED_ROUTE_AUTHENTICATION_BOUNDARY",
                severity="high",
                target="/v1/capabilities",
                evidence=(
                    f"Direct API returned {direct_capability.status}; gateway returned "
                    f"{gateway_capability.status}."
                ),
                remediation=(
                    "Investigate route authentication immediately; both direct and gateway paths must "
                    "deny requests without a valid bearer token."
                ),
            )
        )

    for _label, url in checks.items():
        response = request(url)
        missing = [
            name for name in ("x-content-type-options", "referrer-policy") if name not in response.headers
        ]
        if missing:
            findings.append(
                Finding(
                    identifier="MISSING_BROWSER_HARDENING_HEADERS",
                    severity="low",
                    target=url,
                    evidence=f"HTTP {response.status}; missing headers: {', '.join(missing)}.",
                    remediation=(
                        "Set browser hardening headers at APISIX for routed web traffic. Do not treat "
                        "local HTTP absence of HSTS as a production configuration verdict; enforce HSTS "
                        "only after TLS is active."
                    ),
                )
            )
        trace_response = request(url, method="TRACE")
        if trace_response.status < 400:
            findings.append(
                Finding(
                    identifier="TRACE_METHOD_ACCEPTED",
                    severity="medium",
                    target=url,
                    evidence=f"TRACE returned HTTP {trace_response.status}.",
                    remediation="Deny TRACE at APISIX and all application-facing HTTP listeners.",
                )
            )

    report = {
        "scope": "loopback-only, non-destructive checks against disposable Taxstamp compose stack",
        "open_ports": open_ports,
        "authentication_boundary": {
            "direct_api_status": direct_capability.status,
            "gateway_status": gateway_capability.status,
        },
        "findings": [asdict(item) for item in findings],
    }
    output = Path("docs/local_security_scan_results.json")
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
