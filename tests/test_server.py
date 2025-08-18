from fastapi.testclient import TestClient
from fpl_recommender.server import app  # <- changed

def test_health():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("ok") is True
