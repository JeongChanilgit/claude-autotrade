from fastapi.testclient import TestClient

from kairos import __version__
from kairos.dashboard.api.main import app


def test_health_endpoint() -> None:
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
