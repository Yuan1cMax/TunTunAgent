from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

import cache_proxy


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)

    def ping(self):
        return True

    def scan_iter(self, match=None, count=None):
        prefix = (match or "").replace("*", "")
        return iter([key for key in self.data if key.startswith(prefix)])


class MockAsyncClient:
    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers, json):
        self.calls.append(json)
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request, json={"answer": "押金规则说明", "conversation_id": "conv-1"})


def _client(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache_proxy, "r", fake)
    monkeypatch.setattr(cache_proxy, "DIFY_API_KEY", "test-key")
    monkeypatch.setattr(cache_proxy, "CACHE_ADMIN_TOKEN", "admin-token")
    MockAsyncClient.calls = []
    monkeypatch.setattr(cache_proxy.httpx, "AsyncClient", MockAsyncClient)
    return TestClient(cache_proxy.app), fake


def test_cache_is_scoped_by_user_and_inputs(monkeypatch):
    client, _ = _client(monkeypatch)
    first = client.post("/chat", json={"query": "押金怎么算", "user": "u1", "inputs": {"tenant": "a"}})
    second = client.post("/chat", json={"query": "押金怎么算", "user": "u1", "inputs": {"tenant": "a"}})
    other_user = client.post("/chat", json={"query": "押金怎么算", "user": "u2", "inputs": {"tenant": "a"}})

    assert first.json()["source"] == "dify_live"
    assert second.json()["source"] == "cache"
    assert other_user.json()["source"] == "dify_live"
    assert len(MockAsyncClient.calls) == 2


def test_conversation_requests_bypass_cache_and_keep_context(monkeypatch):
    client, _ = _client(monkeypatch)
    response = client.post(
        "/chat",
        json={"query": "那第二个呢", "user": "u1", "conversation_id": "existing-conv"},
    )
    assert response.json()["source"] == "dify_live_bypass"
    assert MockAsyncClient.calls[-1]["conversation_id"] == "existing-conv"


def test_lock_is_atomic_and_wait_timeout_is_not_reported_as_success(monkeypatch):
    client, fake = _client(monkeypatch)
    monkeypatch.setattr(cache_proxy, "LOCK_WAIT_SECONDS", 0)
    key = cache_proxy.make_lock_key("押金怎么算", "u1", {})
    fake.set(key, "other-request")
    response = client.post("/chat", json={"query": "押金怎么算", "user": "u1"})
    assert response.status_code == 202
    assert response.json()["source"] == "lock_wait_timeout"


def test_cache_admin_requires_bearer_token(monkeypatch):
    client, fake = _client(monkeypatch)
    fake.set("dify_cache:one", '{"answer":"x","cached_at":"now"}')
    assert client.get("/cache/stats").status_code == 401
    stats = client.get("/cache/stats", headers={"Authorization": "Bearer admin-token"})
    assert stats.json()["cached_count"] == 1
    cleared = client.delete("/cache/clear", headers={"Authorization": "Bearer admin-token"})
    assert cleared.json()["message"] == "已清除 1 条缓存"


def test_null_upstream_answer_is_not_reported_as_success(monkeypatch):
    client, _ = _client(monkeypatch)

    class NullAnswerAsyncClient(MockAsyncClient):
        async def post(self, url, headers, json):
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={"answer": None, "conversation_id": "conv-null"})

    monkeypatch.setattr(cache_proxy.httpx, "AsyncClient", NullAnswerAsyncClient)
    response = client.post("/chat", json={"query": "押金怎么算", "user": "u-null"})

    assert response.status_code == 502
    assert response.json()["answer"] == "上游服务未返回有效回答。"
