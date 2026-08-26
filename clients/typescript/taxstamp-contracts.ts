/**
 * Shared Taxstamp API contract for PWA, React Native, and gateway adapters.
 * Device signing stays outside this package: mobile applications must never
 * embed the platform device HMAC secret.
 */

export type CapabilityState =
  | "implemented"
  | "requires_configuration"
  | "configured_not_verified"
  | "not_implemented";

export type Capability = {
  name: string;
  state: CapabilityState;
  detail: string;
};

export type Readiness = {
  status: "ready" | "not_ready";
  database?: boolean;
  redis?: boolean;
  revision?: string;
};

export type VerificationRequest = {
  serial: string;
  secure_code: string;
  device_id: string;
  nonce: string;
};

export type VerificationResult = {
  authentic: boolean;
  outcome: string;
  reason: string;
  serial: string;
  product_category?: string;
  expires_at?: string;
};

export type SignedVerificationTransport = {
  verify(request: VerificationRequest): Promise<VerificationResult>;
};

export function requireHttpsBaseUrl(value: string): string {
  const normalized = value.trim().replace(/\/$/, "");
  if (!normalized.startsWith("https://") && !normalized.startsWith("http://localhost")) {
    throw new Error("A remote Taxstamp API endpoint must use HTTPS.");
  }
  return normalized;
}

export async function fetchReadiness(baseUrl: string): Promise<Readiness> {
  const response = await fetch(`${requireHttpsBaseUrl(baseUrl)}/readyz`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`Readiness failed with HTTP ${response.status}.`);
  return (await response.json()) as Readiness;
}

export async function fetchCapabilities(baseUrl: string, accessToken?: string): Promise<Capability[]> {
  const response = await fetch(`${requireHttpsBaseUrl(baseUrl)}/v1/capabilities`, {
    headers: accessToken ? { Accept: "application/json", Authorization: `Bearer ${accessToken}` } : { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`Capability inspection failed with HTTP ${response.status}.`);
  const document = (await response.json()) as { capabilities: Capability[] };
  return document.capabilities;
}
