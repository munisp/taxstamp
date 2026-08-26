package gatewaypolicy

import "testing"

func TestPolicyRejectsInsecureDiscoveryURL(t *testing.T) {
	policy := OIDCPolicy{DiscoveryURL: "http://id.example.ng/realms/taxstamp/.well-known/openid-configuration", ClientID: "taxstamp-pwa", RouteURI: "/v1/*"}
	if err := policy.Validate(); err == nil {
		t.Fatal("expected insecure issuer to be rejected")
	}
}

func TestPolicyBuildsBearerOnlyRoute(t *testing.T) {
	policy := OIDCPolicy{DiscoveryURL: "https://id.example.ng/realms/taxstamp/.well-known/openid-configuration", ClientID: "taxstamp-pwa", RouteURI: "/v1/*", RequiredScopes: []string{"taxstamp.read"}}
	route, err := policy.APISIXRoute()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	plugins := route["plugins"].(map[string]any)
	oidc := plugins["openid-connect"].(map[string]any)
	if oidc["bearer_only"] != true || oidc["ssl_verify"] != true || oidc["unauth_action"] != "deny" {
		t.Fatalf("unsafe APISIX OIDC configuration: %#v", oidc)
	}
}
