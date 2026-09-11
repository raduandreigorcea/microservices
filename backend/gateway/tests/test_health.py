async def test_health_is_open_and_green(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_is_green_when_both_services_answer(client, upstream):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "app_service": "ok",
        "user_service": "ok",
    }
    assert [r.url.path for r in upstream.requests] == ["/health", "/health"]


async def test_readiness_goes_red_when_app_service_is_unhealthy(client, upstream):
    upstream.app_health_status = 500
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "status": "degraded",
        "app_service": "unhealthy",
        "user_service": "ok",
    }


async def test_readiness_goes_red_when_user_service_cannot_be_reached(
    client, upstream
):
    upstream.user_unreachable = True
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["user_service"] == "unreachable"


async def test_readiness_needs_no_token(client, upstream):
    await client.get("/health/ready")
    assert upstream.introspections == []
