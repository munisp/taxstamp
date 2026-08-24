"""A real OIDC provider sandbox: real keys, real signatures, real JWKS over a socket.

Nothing about the verification path is stubbed. The sandbox holds actual RSA and EC
private keys, signs actual tokens with them, and publishes the corresponding public keys
at a real HTTP endpoint, so the production verifier performs a genuine signature check
against a genuinely fetched key set. That is the only way a test can distinguish "the
signature was checked" from "the claims were parsed".
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from taxstamp.jsontypes import JsonObject

RSA_KID: Final[str] = "rsa-1"
EC_KID: Final[str] = "ec-1"
#: A key the provider holds but never publishes, for testing an unknown key id.
UNPUBLISHED_KID: Final[str] = "rsa-unpublished"


def _b64(value: int, length: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


@dataclass
class ProviderScript:
    """Controls how the provider's key endpoint behaves."""

    #: Serve an error instead of the key set, to simulate a provider outage.
    status: int = 200
    #: Count of key-set fetches, so cache behaviour can be observed.
    fetches: int = 0
    #: Keys withheld from the published set.
    withheld: set[str] = field(default_factory=lambda: {UNPUBLISHED_KID})


class IdentitySandbox:
    """An OIDC provider that really signs tokens and really serves its key set."""

    def __init__(self) -> None:
        self.script = ProviderScript()
        self._rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._rsa_unpublished = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._ec = ec.generate_private_key(ec.SECP256R1())
        script = self.script
        jwks = self._jwks

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                script.fetches += 1
                if script.status != 200:
                    self.send_response(script.status)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                encoded = json.dumps(jwks()).encode()
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
        self.issuer = f"http://{host}:{port}"
        self.jwks_url = f"{self.issuer}/keys"

    def _jwks(self) -> JsonObject:
        keys: list[JsonObject] = []
        if RSA_KID not in self.script.withheld:
            numbers = self._rsa.public_key().public_numbers()
            keys.append(
                {
                    "kty": "RSA",
                    "kid": RSA_KID,
                    "use": "sig",
                    "alg": "RS256",
                    "n": _b64(numbers.n, 256),
                    "e": _b64(numbers.e, 3),
                }
            )
        if EC_KID not in self.script.withheld:
            ec_numbers = self._ec.public_key().public_numbers()
            keys.append(
                {
                    "kty": "EC",
                    "kid": EC_KID,
                    "use": "sig",
                    "alg": "ES256",
                    "crv": "P-256",
                    "x": _b64(ec_numbers.x, 32),
                    "y": _b64(ec_numbers.y, 32),
                }
            )
        return {"keys": keys}

    def start(self) -> str:
        self._thread.start()
        return self.jwks_url

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def token(
        self,
        *,
        subject: str,
        audience: str = "taxstamp-api",
        issuer: str | None = None,
        issued_at: dt.datetime,
        expires_at: dt.datetime,
        not_before: dt.datetime | None = None,
        amr: list[str] | str | None = None,
        acr: str | None = None,
        kid: str = RSA_KID,
        algorithm: str = "RS256",
        omit: tuple[str, ...] = (),
    ) -> str:
        """Sign a token with the provider's real key material."""
        claims: dict[str, object] = {
            "sub": subject,
            "iss": self.issuer if issuer is None else issuer,
            "aud": audience,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        if not_before is not None:
            claims["nbf"] = int(not_before.timestamp())
        if amr is not None:
            claims["amr"] = amr
        if acr is not None:
            claims["acr"] = acr
        for name in omit:
            claims.pop(name, None)
        return jwt.encode(claims, self._key(kid), algorithm=algorithm, headers={"kid": kid})

    def unsigned_token(self, *, subject: str, audience: str = "taxstamp-api") -> str:
        """A token asserting alg=none, which a verifier must never accept."""
        header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": subject, "iss": self.issuer, "aud": audience}).encode()
        )
        return f"{header.rstrip(b'=').decode()}.{payload.rstrip(b'=').decode()}.".rstrip(".") + "."

    def _key(self, kid: str) -> rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey:
        if kid == EC_KID:
            return self._ec
        if kid == UNPUBLISHED_KID:
            return self._rsa_unpublished
        return self._rsa
