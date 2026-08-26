# Taxstamp Field Mobile

This React Native/Expo foundation targets Android and iOS inspectors and operators. It is deliberately safe-by-default: it permits HTTPS endpoint health and capability inspection, stores only the endpoint URL in `expo-secure-store`, and refuses to embed a bearer token or device HMAC secret.

## Development

Install dependencies in this directory and run `pnpm start`. Configure the endpoint through managed build configuration or the on-device form during non-production validation. Production builds must receive the base URL, Keycloak configuration, device identity policy, and certificate-pinning decision through approved mobile release controls.

## Required before field verification is enabled

The mobile client needs a device-provisioning and signing design approved by security. The current backend expects signed verification requests, so a production implementation must use an approved device identity, gateway-mediated signing or hardware-backed key flow, Keycloak policy, replay protection, audit logging, and a lost-device revocation process. Camera scanning must be added only after this contract and data-protection review are complete.
