# open-appsec at the tax-stamp edge

open-appsec is a WAF that attaches to the reverse proxy, not to the application. It is
configured here rather than in Python on purpose: a request that a WAF should reject must
be rejected *before* it reaches business logic, and business logic that second-guesses a
WAF ends up with two disagreeing filters and no clear owner.

## Where it sits

```
client ──TLS──> APISIX (deploy/edge/apisix.yaml) ──> taxstamp API (uvicorn)
                  │
                  └── open-appsec attachment: inspects, then allows or drops
```

open-appsec ships an NGINX attachment; APISIX is built on OpenResty/NGINX, so the
attachment applies to the same worker processes that serve the routes above. The agent
runs as a sidecar and the attachment is loaded into the proxy.

## What it must protect

| Surface | Why it matters |
| --- | --- |
| `POST /v1/public/verify` | The only unauthenticated route. Reachable by anyone with a phone camera, so it is the natural target for injection and volumetric abuse. |
| Any route carrying `Authorization` | Credential stuffing and token brute force. The API already refuses unknown tokens in constant time, but the edge should shed that traffic. |
| `POST /v1/payments/*` | Signed bodies. A malformed body is already rejected as unauthenticated, so anything the WAF drops here is pure load reduction. |
| Regulator export routes | Large signed responses; the WAF limits who can provoke that work. |

## Mode

Run in **learn/detect** mode first and review the findings, then switch to **prevent**.
Turning prevention on before a learning window has covered a full reporting cycle will
block legitimate regulator exports, which are bursty and only happen monthly.

Recommended starting policy (`local_policy.yaml` for the agent):

```yaml
policies:
  default:
    triggers: [appsec-default-log-trigger]
    mode: detect-learn
    practices: [webapp-default-practice]
    custom-response: appsec-default-web-user-response
practices:
  - name: webapp-default-practice
    web-attacks:
      override-mode: detect-learn
      minimum-confidence: high
    anti-bot:
      override-mode: inactive # The device fleet is not a browser; bot heuristics
      # would fight the handhelds.
```

## What this file is not

This is a documented integration point and a starting policy. It is **not** evidence that
a WAF is deployed, tuned or effective. Production evidence requires:

- the agent deployed alongside the real edge, with the attachment loaded (verifiable in
  the proxy's loaded-module list);
- a learning window's findings reviewed, with false positives recorded;
- prevention mode enabled, with the resulting rejection rate measured;
- an external test (penetration test or red team) confirming the rejections.

Until those exist, `/v1/capabilities` reports the WAF as requiring configuration.
