from urllib.parse import parse_qs, urlparse


async def start_login(client):
    """Returns the state Google would hand back on the redirect."""
    response = await client.get("/auth/google/authorize")
    assert response.status_code == 200
    return parse_qs(urlparse(response.json()["authorization_url"]).query)["state"][0]


async def test_starting_a_login_hands_back_a_google_url(client):
    response = await client.get("/auth/google/authorize")
    assert response.status_code == 200
    assert response.json()["authorization_url"].startswith(
        "https://accounts.google.com/"
    )


async def test_every_login_attempt_gets_its_own_state(client):
    assert await start_login(client) != await start_login(client)


async def test_the_browser_entry_point_redirects_to_google(client):
    response = await client.get("/auth/google/login", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].startswith("https://accounts.google.com/")


async def test_completing_the_callback_returns_a_token_pair(client):
    state = await start_login(client)
    response = await client.get(
        "/auth/google/callback", params={"code": "good-code", "state": state}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] > 0


async def test_the_first_login_creates_the_account(client, fake_google):
    state = await start_login(client)
    tokens = (
        await client.get(
            "/auth/google/callback", params={"code": "good-code", "state": state}
        )
    ).json()
    profile = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert profile.status_code == 200
    assert profile.json()["email"] == fake_google.identity.email


async def test_logging_in_twice_does_not_create_a_second_account(client):
    first_state = await start_login(client)
    first = (
        await client.get(
            "/auth/google/callback", params={"code": "good-code", "state": first_state}
        )
    ).json()
    second_state = await start_login(client)
    second = (
        await client.get(
            "/auth/google/callback", params={"code": "good-code", "state": second_state}
        )
    ).json()

    headers = {"Authorization": f"Bearer {first['access_token']}"}
    first_id = (await client.get("/users/me", headers=headers)).json()["id"]
    headers = {"Authorization": f"Bearer {second['access_token']}"}
    second_id = (await client.get("/users/me", headers=headers)).json()["id"]
    assert first_id == second_id


async def test_an_unknown_state_is_refused(client):
    response = await client.get(
        "/auth/google/callback", params={"code": "good-code", "state": "never-issued"}
    )
    assert response.status_code == 400


async def test_a_state_cannot_be_replayed(client):
    state = await start_login(client)
    first = await client.get(
        "/auth/google/callback", params={"code": "good-code", "state": state}
    )
    assert first.status_code == 200
    replay = await client.get(
        "/auth/google/callback", params={"code": "good-code", "state": state}
    )
    assert replay.status_code == 400


async def test_a_code_google_rejects_is_unauthorised(client):
    state = await start_login(client)
    response = await client.get(
        "/auth/google/callback", params={"code": "bad-code", "state": state}
    )
    assert response.status_code == 401


async def test_a_user_who_cancels_at_google_gets_a_clear_error(client):
    state = await start_login(client)
    response = await client.get(
        "/auth/google/callback", params={"error": "access_denied", "state": state}
    )
    assert response.status_code == 401
    assert "access_denied" in response.json()["detail"]


async def test_an_unverified_google_email_is_refused(client, fake_google):
    fake_google.mark_email_unverified()
    state = await start_login(client)
    response = await client.get(
        "/auth/google/callback", params={"code": "good-code", "state": state}
    )
    assert response.status_code == 403


async def test_the_pkce_verifier_we_kept_is_the_one_we_send(client, fake_google):
    state = await start_login(client)
    await client.get(
        "/auth/google/callback", params={"code": "good-code", "state": state}
    )
    sent_verifier = fake_google.exchanges[-1]["code_verifier"]
    assert sent_verifier and len(sent_verifier) >= 43


async def test_a_valid_token_introspects_as_active(client, registered):
    response = await client.post(
        "/auth/introspect", json={"token": registered["access_token"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["active"] is True
    assert "users:read" in body["scopes"]


async def test_nonsense_introspects_as_inactive_rather_than_erroring(client):
    response = await client.post("/auth/introspect", json={"token": "not-a-jwt"})
    assert response.status_code == 200
    assert response.json()["active"] is False


async def test_refreshing_returns_a_new_pair(client, registered):
    response = await client.post(
        "/auth/refresh", json={"refresh_token": registered["refresh_token"]}
    )
    assert response.status_code == 200
    assert response.json()["refresh_token"] != registered["refresh_token"]


async def test_a_refresh_token_cannot_be_used_twice(client, registered):
    first = await client.post(
        "/auth/refresh", json={"refresh_token": registered["refresh_token"]}
    )
    assert first.status_code == 200
    replayed = await client.post(
        "/auth/refresh", json={"refresh_token": registered["refresh_token"]}
    )
    assert replayed.status_code == 401


async def test_logging_out_kills_the_refresh_token(client, registered, auth_header):
    response = await client.post(
        "/auth/logout",
        json={"refresh_token": registered["refresh_token"]},
        headers=auth_header,
    )
    assert response.status_code == 204
    reused = await client.post(
        "/auth/refresh", json={"refresh_token": registered["refresh_token"]}
    )
    assert reused.status_code == 401


async def test_logging_out_also_stops_the_access_token_working(
    client, registered, auth_header
):
    await client.post(
        "/auth/logout",
        json={"refresh_token": registered["refresh_token"]},
        headers=auth_header,
    )
    introspection = await client.post(
        "/auth/introspect", json={"token": registered["access_token"]}
    )
    assert introspection.json()["active"] is False


async def test_there_is_no_password_login_any_more(client):
    assert (
        await client.post(
            "/auth/token", data={"username": "a@example.com", "password": "x"}
        )
    ).status_code == 404
    assert (
        await client.post("/auth/register", json={"email": "a@example.com"})
    ).status_code == 404


async def test_login_fails_loudly_when_google_is_not_configured(
    client, settings, monkeypatch
):
    """Better a clear 503 from us than 'Missing required parameter' from Google."""
    monkeypatch.setattr(settings, "google_client_id", "")
    response = await client.get("/auth/google/authorize")
    assert response.status_code == 503
    assert "GOOGLE_CLIENT_ID" in response.json()["detail"]


async def test_my_profile_comes_back_for_a_valid_token(
    client, auth_header, fake_google
):
    response = await client.get("/users/me", headers=auth_header)
    assert response.status_code == 200
    assert response.json()["email"] == fake_google.identity.email


async def test_my_profile_is_refused_without_a_token(client):
    assert (await client.get("/users/me")).status_code == 401


async def test_my_profile_is_refused_with_a_bogus_token(client):
    response = await client.get(
        "/users/me", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 401


async def test_logging_in_shows_up_as_a_session(client, auth_header):
    response = await client.get("/users/me/sessions", headers=auth_header)
    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_a_session_can_be_revoked_by_its_owner(client, auth_header):
    sessions = (await client.get("/users/me/sessions", headers=auth_header)).json()
    response = await client.delete(
        f"/users/me/sessions/{sessions[0]['id']}", headers=auth_header
    )
    assert response.status_code == 204
    remaining = (await client.get("/users/me/sessions", headers=auth_header)).json()
    assert remaining == []


async def test_revoking_an_unknown_session_is_a_404(client, auth_header):
    response = await client.delete(
        "/users/me/sessions/00000000-0000-0000-0000-000000000000", headers=auth_header
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "no such session"


async def test_a_token_for_an_unknown_user_is_refused(client):
    """A well-signed token is not enough. The account has to still exist."""
    from app.service import create_access_token
    from tests.conftest import TEST_JWT_SECRET

    token, _ = create_access_token(
        "00000000-0000-0000-0000-000000000000",
        ["users:read"],
        secret=TEST_JWT_SECRET,
    )
    response = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


async def test_a_token_without_the_required_scope_is_forbidden(client, registered):
    """Same user, same signature, but the scope the route demands is missing."""
    from app.service import create_access_token
    from tests.conftest import TEST_JWT_SECRET

    introspected = await client.post(
        "/auth/introspect", json={"token": registered["access_token"]}
    )
    subject = introspected.json()["sub"]
    token, _ = create_access_token(subject, ["scrape:read"], secret=TEST_JWT_SECRET)
    response = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403


async def test_health_reports_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_checks_postgres_and_redis(client):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert body["redis"] == "ok"


async def test_swagger_offers_a_paste_the_token_option(client):
    schemes = (await client.get("/openapi.json")).json()["components"][
        "securitySchemes"
    ]
    assert schemes["HTTPBearer"]["scheme"] == "bearer"


async def test_swagger_does_not_ask_for_google_credentials(client):
    schemes = (await client.get("/openapi.json")).json()["components"][
        "securitySchemes"
    ]
    assert list(schemes) == ["HTTPBearer"]


async def test_a_protected_route_uses_the_bearer_scheme(client):
    schema = (await client.get("/openapi.json")).json()
    security = schema["paths"]["/users/me"]["get"]["security"]
    assert {name for entry in security for name in entry} == {"HTTPBearer"}
