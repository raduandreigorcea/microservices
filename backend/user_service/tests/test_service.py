import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from app.service import (
    GoogleOAuthClient,
    InvalidToken,
    create_access_token,
    decode_access_token,
    hash_refresh_token,
    new_pkce_pair,
    new_refresh_token,
)

SECRET = "test-secret-not-used-anywhere-else"


def test_an_access_token_carries_its_subject_and_scopes_back():
    token, _ = create_access_token("user-1", ["scrape:read"], secret=SECRET)
    claims = decode_access_token(token, secret=SECRET)
    assert claims.subject == "user-1"
    assert claims.scopes == ("scrape:read",)


def test_every_access_token_gets_its_own_id():
    first, _ = create_access_token("user-1", [], secret=SECRET)
    second, _ = create_access_token("user-1", [], secret=SECRET)
    assert decode_access_token(first, secret=SECRET).token_id != decode_access_token(
        second, secret=SECRET
    ).token_id


def test_an_expired_token_is_rejected():
    token, _ = create_access_token("user-1", [], secret=SECRET, ttl_seconds=-1)
    with pytest.raises(InvalidToken):
        decode_access_token(token, secret=SECRET)


def test_a_token_signed_with_another_secret_is_rejected():
    token, _ = create_access_token(
        "user-1", [], secret="a-different-secret-also-at-least-32-bytes"
    )
    with pytest.raises(InvalidToken):
        decode_access_token(token, secret=SECRET)


def test_garbage_is_rejected_rather_than_crashing():
    with pytest.raises(InvalidToken):
        decode_access_token("not-a-jwt", secret=SECRET)


def test_refresh_tokens_are_unguessable_and_unique():
    assert new_refresh_token() != new_refresh_token()
    assert len(new_refresh_token()) >= 32


def test_refresh_tokens_are_stored_as_a_hash():
    token = new_refresh_token()
    stored = hash_refresh_token(token)
    assert token not in stored
    assert stored == hash_refresh_token(token)


def test_each_pkce_pair_is_different():
    assert new_pkce_pair().verifier != new_pkce_pair().verifier


def test_the_pkce_challenge_is_the_s256_digest_of_the_verifier():
    pair = new_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pair.verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert pair.challenge == expected


def test_the_pkce_verifier_is_long_enough_for_rfc_7636():
    # RFC 7636 requires 43 to 128 characters.
    assert 43 <= len(new_pkce_pair().verifier) <= 128


CLIENT = GoogleOAuthClient(
    client_id="client-id-123",
    client_secret="client-secret",
    redirect_uri="http://localhost:8002/auth/google/callback",
)


def query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_the_authorization_url_goes_to_google():
    assert CLIENT.authorization_url(state="s", code_challenge="c").startswith(
        "https://accounts.google.com/o/oauth2/v2/auth?"
    )


def test_the_authorization_url_asks_for_an_authorization_code():
    assert query(CLIENT.authorization_url(state="s", code_challenge="c"))[
        "response_type"
    ] == ["code"]


def test_the_authorization_url_carries_our_client_id_and_redirect():
    parameters = query(CLIENT.authorization_url(state="s", code_challenge="c"))
    assert parameters["client_id"] == ["client-id-123"]
    assert parameters["redirect_uri"] == [
        "http://localhost:8002/auth/google/callback"
    ]


def test_the_authorization_url_uses_pkce_with_s256():
    parameters = query(CLIENT.authorization_url(state="s", code_challenge="chal"))
    assert parameters["code_challenge"] == ["chal"]
    assert parameters["code_challenge_method"] == ["S256"]


def test_the_authorization_url_carries_the_state_back():
    assert query(CLIENT.authorization_url(state="my-state", code_challenge="c"))[
        "state"
    ] == ["my-state"]


def test_we_ask_google_for_identity_not_for_data():
    scope = query(CLIENT.authorization_url(state="s", code_challenge="c"))["scope"][0]
    assert "openid" in scope
    assert "email" in scope
    assert "https://www.googleapis.com/auth/drive" not in scope


def test_the_client_secret_never_appears_in_the_redirect():
    url = CLIENT.authorization_url(state="s", code_challenge="c")
    assert "client-secret" not in url
