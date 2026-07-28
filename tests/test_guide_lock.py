from __future__ import annotations

from fastapi.testclient import TestClient

import guide
from tests.test_cache_proxy import FakeRedis


def test_duplicate_guide_request_returns_202_without_upstream_call(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(guide, "r", fake)
    key = guide.make_lock_key("500", None, "信条")
    fake.set(key, "in-flight")
    response = TestClient(guide.app).get("/search_accounts?max_price=500&keyword=信条")
    assert response.status_code == 202
    assert response.json()["source"] == "lock_wait"
