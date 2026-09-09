from app.api.health import health_check


def test_health_check():
    assert health_check() == {"status": "ok", "message": "FirmaDoc API is running"}
