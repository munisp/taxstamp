// Package gatewaypolicy produces safe declarative APISIX OIDC route fragments.
// It does not call the APISIX Admin API or manage secrets.
package gatewaypolicy

import (
	"errors"
	"net/url"
)

var ErrInvalidPolicy = errors.New("invalid gateway OIDC policy")

// OIDCPolicy is the minimum non-secret policy required for an APISIX route.
// Client secrets belong in an approved secret manager, not this structure.
type OIDCPolicy struct {
	DiscoveryURL   string
	ClientID       string
	RequiredScopes []string
	RouteURI       string
}

func (p OIDCPolicy) Validate() error {
	parsed, err := url.ParseRequestURI(p.DiscoveryURL)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" {
		return ErrInvalidPolicy
	}
	if p.ClientID == "" || p.RouteURI == "" || p.RouteURI[0] != '/' {
		return ErrInvalidPolicy
	}
	return nil
}

// APISIXRoute returns a bearer-only OIDC configuration with TLS verification,
// PKCE support, and explicit required scopes. Deployment injects secret values.
func (p OIDCPolicy) APISIXRoute() (map[string]any, error) {
	if err := p.Validate(); err != nil {
		return nil, err
	}
	return map[string]any{
		"uri": p.RouteURI,
		"plugins": map[string]any{
			"openid-connect": map[string]any{
				"client_id":       p.ClientID,
				"discovery":       p.DiscoveryURL,
				"bearer_only":     true,
				"use_jwks":        true,
				"use_pkce":        true,
				"ssl_verify":      true,
				"unauth_action":   "deny",
				"required_scopes": p.RequiredScopes,
			},
		},
	}, nil
}
