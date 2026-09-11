async def test_health_is_green(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_is_green_when_both_services_answer(client, upstream):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "user_service": "ok",
        "scraper_service": "ok",
    }
    assert {r.url.host for r in upstream.requests} == {
        "user-service",
        "scraper-service",
    }


async def test_readiness_goes_red_when_the_scraper_is_unhealthy(client, upstream):
    upstream.health_status["scraper-service"] = 500
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "status": "degraded",
        "user_service": "ok",
        "scraper_service": "unhealthy",
    }


async def test_readiness_goes_red_when_a_service_cannot_be_reached(client, upstream):
    upstream.unreachable.add("user-service")
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["user_service"] == "unreachable"
