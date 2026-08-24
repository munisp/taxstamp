"""HTTP client for external systems of record.

There is no simulated success path. When a provider is not configured the caller
receives ``CapabilityNotConfigured``; when it is configured but fails, the error is
propagated. Responses are validated before use.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import structlog

from taxstamp.errors import CapabilityNotConfigured, DependencyUnavailable
from taxstamp.jsontypes import JsonObject

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    timeout_seconds: float

    @property
    def configured(self) -> bool:
        return bool(self.base_url)


class ProviderClient:
    def __init__(self, config: ProviderConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client

    @property
    def configured(self) -> bool:
        return self._config.configured

    def with_client(self, client: httpx.Client) -> ProviderClient:
        """Return an equivalent client bound to an injected transport."""
        return ProviderClient(self._config, client=client)

    def require_configured(self) -> None:
        if not self._config.configured:
            raise CapabilityNotConfigured(
                f"{self._config.name} is not configured; the request is refused instead of "
                "returning an unverified result",
                detail={"provider": self._config.name},
            )

    def post_json(self, path: str, body: JsonObject) -> JsonObject:
        self.require_configured()
        headers = {"content-type": "application/json", "accept": "application/json"}
        if self._config.api_key:
            headers["authorization"] = f"Bearer {self._config.api_key}"
        url = self._config.base_url.rstrip("/") + path
        client = self._client or httpx.Client(timeout=self._config.timeout_seconds)
        try:
            response = client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("provider_transport_error", provider=self._config.name, error=str(exc))
            raise DependencyUnavailable(f"{self._config.name} is unreachable") from exc
        finally:
            if self._client is None:
                client.close()

        if response.status_code >= 500:
            raise DependencyUnavailable(
                f"{self._config.name} returned {response.status_code}",
                detail={"provider": self._config.name, "status": str(response.status_code)},
            )
        if response.status_code >= 400:
            raise DependencyUnavailable(
                f"{self._config.name} rejected the request with {response.status_code}",
                detail={"provider": self._config.name, "status": str(response.status_code)},
            )
        try:
            decoded = json.loads(response.content)
        except json.JSONDecodeError as exc:
            raise DependencyUnavailable(f"{self._config.name} returned malformed JSON") from exc
        if not isinstance(decoded, dict):
            raise DependencyUnavailable(f"{self._config.name} returned a non-object response")
        return decoded
