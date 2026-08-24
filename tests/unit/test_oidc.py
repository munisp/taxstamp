"""Federated token verification, against a provider that really signs and really serves.

Each test states the attack or failure it rules out, because the value of a verifier is
entirely in what it refuses.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest

from taxstamp.errors import DependencyUnavailable, Unauthenticated
from taxstamp.identity.oidc import OidcConfig, OidcVerifier, looks_like_jwt
from tests.support.identity_server import EC_KID, UNPUBLISHED_KID, IdentitySandbox

AUDIENCE = "taxstamp-api"


def _now() -> dt.datetime:
    """Real wall-clock time.

    Token validity is checked against the system clock inside the JWT library, so these
    tests cannot use the frozen clock the rest of the suite uses: a token minted at a
    fixed historical instant would simply be expired.
    """
    return dt.datetime.now(dt.UTC)


@pytest.fixture
def provider() -> Iterator[IdentitySandbox]:
    sandbox = IdentitySandbox()
    sandbox.start()
    try:
        yield sandbox
    finally:
        sandbox.stop()


def _verifier(provider: IdentitySandbox, *, required_acr: str = "mfa") -> OidcVerifier:
    return OidcVerifier(
        OidcConfig(
            issuer=provider.issuer,
            audience=AUDIENCE,
            jwks_url=provider.jwks_url,
            leeway_seconds=30,
            jwks_cache_seconds=600,
            required_methods=frozenset({"mfa", "otp", "hwk"}),
            required_acr=required_acr,
        )
    )


def _token(provider: IdentitySandbox, **kwargs: object) -> str:
    now = _now()
    defaults: dict[str, object] = {
        "subject": "staff-1",
        "issued_at": now - dt.timedelta(minutes=1),
        "expires_at": now + dt.timedelta(minutes=5),
    }
    defaults.update(kwargs)
    return provider.token(**defaults)  # type: ignore[arg-type]


def test_jwt_shape_routes_only_three_segment_tokens() -> None:
    """Opaque platform tokens must not be sent to the provider path at all."""
    assert looks_like_jwt("a.b.c")
    assert not looks_like_jwt("opaque-platform-token")
    assert not looks_like_jwt("a.b")
    assert not looks_like_jwt("a..c")


def test_rsa_signed_token_is_accepted(provider: IdentitySandbox) -> None:
    identity = _verifier(provider).verify(_token(provider, amr=["pwd", "otp"], acr="mfa"))
    assert identity.subject == "staff-1"
    assert identity.issuer == provider.issuer
    assert identity.methods == {"pwd", "otp"}


def test_ec_signed_token_is_accepted(provider: IdentitySandbox) -> None:
    """A provider rotating to elliptic-curve keys must not break authentication."""
    identity = _verifier(provider).verify(_token(provider, kid=EC_KID, algorithm="ES256"))
    assert identity.subject == "staff-1"


def test_wrong_audience_is_refused(provider: IdentitySandbox) -> None:
    """A token minted for another relying party must not be replayable here."""
    with pytest.raises(Unauthenticated):
        _verifier(provider).verify(_token(provider, audience="some-other-service"))


def test_wrong_issuer_is_refused(provider: IdentitySandbox) -> None:
    with pytest.raises(Unauthenticated):
        _verifier(provider).verify(_token(provider, issuer="https://attacker.example"))


def test_expired_token_is_refused(provider: IdentitySandbox) -> None:
    with pytest.raises(Unauthenticated):
        _verifier(provider).verify(
            _token(
                provider,
                issued_at=_now() - dt.timedelta(hours=2),
                expires_at=_now() - dt.timedelta(hours=1),
            )
        )


def test_token_not_yet_valid_is_refused(provider: IdentitySandbox) -> None:
    with pytest.raises(Unauthenticated):
        _verifier(provider).verify(
            _token(
                provider,
                issued_at=_now() + dt.timedelta(hours=1),
                not_before=_now() + dt.timedelta(hours=1),
                expires_at=_now() + dt.timedelta(hours=2),
            )
        )


@pytest.mark.parametrize("claim", ["sub", "exp", "iat", "aud", "iss"])
def test_missing_registered_claim_is_refused(provider: IdentitySandbox, claim: str) -> None:
    """Every claim the decision depends on must be present, not defaulted."""
    with pytest.raises(Unauthenticated):
        _verifier(provider).verify(_token(provider, omit=(claim,)))


def test_unsigned_token_is_refused(provider: IdentitySandbox) -> None:
    """alg=none is the classic JWT bypass; it must never authenticate anyone."""
    with pytest.raises(Unauthenticated):
        _verifier(provider).verify(provider.unsigned_token(subject="staff-1"))


def test_unknown_signing_key_is_refused_as_authentication_failure(provider: IdentitySandbox) -> None:
    """A token signed by a key the provider does not publish is forged, not an outage."""
    with pytest.raises(Unauthenticated):
        _verifier(provider).verify(_token(provider, kid=UNPUBLISHED_KID))


def test_key_set_outage_is_an_outage_not_a_rejection(provider: IdentitySandbox) -> None:
    """A provider outage must fail closed as unavailable.

    Reporting it as "unauthenticated" would be a lie about a legitimate token, and would
    invite an operator to disable federation to "fix" a transient network fault.
    """
    provider.script.status = 503
    with pytest.raises(DependencyUnavailable):
        _verifier(provider).verify(_token(provider))


def test_unreachable_provider_is_an_outage(provider: IdentitySandbox) -> None:
    verifier = OidcVerifier(
        OidcConfig(
            issuer="http://127.0.0.1:1",
            audience=AUDIENCE,
            jwks_url="http://127.0.0.1:1/keys",
            leeway_seconds=30,
            jwks_cache_seconds=600,
            required_methods=frozenset(),
            required_acr="",
        )
    )
    with pytest.raises(DependencyUnavailable):
        verifier.verify(_token(provider))


def test_key_set_is_cached_across_verifications(provider: IdentitySandbox) -> None:
    """Each request must not cost the provider a key-set fetch."""
    verifier = _verifier(provider)
    verifier.verify(_token(provider))
    fetches_after_first = provider.script.fetches
    for _ in range(5):
        verifier.verify(_token(provider))
    assert provider.script.fetches == fetches_after_first


def test_unconfigured_verifier_refuses_rather_than_trusting(provider: IdentitySandbox) -> None:
    """With no issuer configured, a provider token is refused, never accepted blindly."""
    verifier = OidcVerifier(
        OidcConfig(
            issuer="",
            audience="",
            jwks_url="",
            leeway_seconds=30,
            jwks_cache_seconds=600,
            required_methods=frozenset(),
            required_acr="",
        )
    )
    with pytest.raises(Unauthenticated):
        verifier.verify(_token(provider))


def test_multi_factor_recognised_from_either_amr_or_acr(provider: IdentitySandbox) -> None:
    """Providers populate one or the other, so either is sufficient evidence."""
    verifier = _verifier(provider)
    required = verifier.config.required_methods
    by_amr = verifier.verify(_token(provider, amr=["pwd", "hwk"]))
    by_acr = verifier.verify(_token(provider, acr="mfa"))
    single = verifier.verify(_token(provider, amr="pwd", acr="basic"))
    assert by_amr.satisfies_multi_factor(required_methods=required, required_acr="mfa")
    assert by_acr.satisfies_multi_factor(required_methods=required, required_acr="mfa")
    assert not single.satisfies_multi_factor(required_methods=required, required_acr="mfa")


def test_absent_factor_claims_are_not_multi_factor(provider: IdentitySandbox) -> None:
    """Silence about authentication strength is not evidence of strength."""
    identity = _verifier(provider).verify(_token(provider))
    assert not identity.satisfies_multi_factor(
        required_methods=frozenset({"otp"}),
        required_acr="mfa",
    )
