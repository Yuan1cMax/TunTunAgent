"""Dify FAQ cache proxy with atomic request coalescing.

The proxy intentionally caches only stateless FAQ requests. Requests carrying a
conversation id bypass the cache so a previous turn cannot leak into another
conversation.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any

import httpx
import redis
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


app = FastAPI(title="TunTunAgent FAQ cache proxy")
logger = logging.getLogger("tuntun.cache_proxy")

DIFY_API_URL = os.getenv("DIFY_API_URL", "http://localhost:81/v1/chat-messages")
DIFY_API_KEY = os.getenv("DIFY_API_KEY")
CACHE_ADMIN_TOKEN = os.getenv("CACHE_ADMIN_TOKEN", "").strip()
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))
LOCK_TTL = int(os.getenv("LOCK_TTL", "90"))
LOCK_WAIT_SECONDS = float(os.getenv("LOCK_WAIT_SECONDS", "5"))
LOCK_POLL_SECONDS = float(os.getenv("LOCK_POLL_SECONDS", "0.2"))
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "60"))

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    db=int(os.getenv("REDIS_DB", "0")),
    decode_responses=True,
)

HUMAN_KEYWORDS = ("人工客服", "转人工", "联系客服", "人工服务")
security = HTTPBearer(auto_error=False)


def _normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def _canonical_inputs(inputs: Any) -> dict[str, Any]:
    return inputs if isinstance(inputs, dict) else {}


def _cache_fingerprint(query: str, user: str, inputs: Any) -> str:
    payload = {
        "query": _normalize_query(query),
        "user": str(user or "default_user").strip(),
        "inputs": _canonical_inputs(inputs),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_cache_key(query: str, user: str = "default_user", inputs: Any = None) -> str:
    digest = hashlib.sha256(_cache_fingerprint(query, user, inputs).encode("utf-8")).hexdigest()
    return f"dify_cache:{digest}"


def make_lock_key(query: str, user: str = "default_user", inputs: Any = None) -> str:
    digest = hashlib.sha256(_cache_fingerprint(query, user, inputs).encode("utf-8")).hexdigest()
    return f"dify_lock:{digest}"


def should_cache(answer: str) -> bool:
    return bool(answer) and not any(keyword in answer for keyword in HUMAN_KEYWORDS)


def _load_cache(cache_key: str) -> dict[str, Any] | None:
    raw = r.get(cache_key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        r.delete(cache_key)
        logger.warning("discarded malformed cache value for key=%s", cache_key)
        return None
    return data if isinstance(data, dict) and data.get("answer") else None


def _cache_response(data: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        content={
            "answer": data["answer"],
            "source": "cache",
            "cached_at": data["cached_at"],
        }
    )


def _release_lock(lock_key: str, lock_token: str) -> None:
    """Release only the lock acquired by this request."""
    if r.get(lock_key) == lock_token:
        r.delete(lock_key)


async def _wait_for_cache(cache_key: str) -> dict[str, Any] | None:
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(LOCK_POLL_SECONDS)
        cached = _load_cache(cache_key)
        if cached:
            return cached
    return None


def _require_cache_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    if not CACHE_ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CACHE_ADMIN_TOKEN is not configured; cache administration is disabled.",
        )
    if credentials is None or not hmac.compare_digest(credentials.credentials, CACHE_ADMIN_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="valid Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.post("/chat")
async def chat_proxy(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "请求格式错误"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "请求体必须是 JSON 对象"})

    query = str(body.get("query", "")).strip()
    user = str(body.get("user", "default_user")).strip() or "default_user"
    inputs = _canonical_inputs(body.get("inputs"))
    conversation_id = str(body.get("conversation_id", "")).strip()
    if not query:
        return JSONResponse(status_code=400, content={"error": "query 不能为空"})
    if not DIFY_API_KEY:
        return JSONResponse(status_code=503, content={"answer": "服务端未配置 DIFY_API_KEY。"})

    cacheable = not conversation_id
    cache_key = make_cache_key(query, user, inputs)
    lock_key = make_lock_key(query, user, inputs)
    lock_token = secrets.token_urlsafe(18)
    lock_acquired = False

    if cacheable:
        cached = _load_cache(cache_key)
        if cached:
            return _cache_response(cached)

        lock_acquired = bool(r.set(lock_key, lock_token, nx=True, ex=LOCK_TTL))
        if not lock_acquired:
            cached = await _wait_for_cache(cache_key)
            if cached:
                return _cache_response(cached)
            return JSONResponse(
                status_code=202,
                content={
                    "answer": "相同问题正在处理中，请稍后重试。",
                    "source": "lock_wait_timeout",
                    "retry_after_seconds": max(1, round(LOCK_POLL_SECONDS)),
                },
            )

    try:
        payload: dict[str, Any] = {
            "inputs": inputs,
            "query": query,
            "response_mode": "blocking",
            "user": user,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if "files" in body:
            payload["files"] = body["files"]

        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
            response = await client.post(
                DIFY_API_URL,
                headers={
                    "Authorization": f"Bearer {DIFY_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.status_code != 200:
            logger.warning("dify upstream failed status=%s", response.status_code)
            return JSONResponse(
                status_code=502,
                content={"answer": "上游问答服务暂时不可用，请稍后重试。"},
            )

        data = response.json()
        answer = str(data.get("answer", "")).strip()
        if not answer:
            return JSONResponse(status_code=502, content={"answer": "上游服务未返回有效回答。"})

        if cacheable and should_cache(answer):
            r.set(
                cache_key,
                json.dumps({"answer": answer, "cached_at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False),
                ex=CACHE_TTL,
            )

        return JSONResponse(
            content={
                "answer": answer,
                "source": "dify_live" if cacheable else "dify_live_bypass",
                "conversation_id": data.get("conversation_id", ""),
            }
        )
    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={"answer": "上游服务响应超时，请稍后重试。"})
    except httpx.ConnectError:
        return JSONResponse(status_code=503, content={"answer": "暂时无法连接上游问答服务。"})
    except (redis.RedisError, ValueError, TypeError):
        logger.exception("cache proxy request failed")
        return JSONResponse(status_code=500, content={"answer": "服务处理失败，请稍后重试。"})
    finally:
        if lock_acquired:
            _release_lock(lock_key, lock_token)


@app.get("/health")
async def health_check():
    try:
        r.ping()
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="redis unavailable") from exc
    return {"status": "ok", "redis": "ok", "dify_configured": bool(DIFY_API_KEY)}


@app.get("/cache/stats", dependencies=[Depends(_require_cache_admin)])
async def cache_stats():
    keys = list(r.scan_iter(match="dify_cache:*", count=100))
    return {"cached_count": len(keys), "keys": keys[:20]}


@app.delete("/cache/clear", dependencies=[Depends(_require_cache_admin)])
async def clear_cache():
    keys = list(r.scan_iter(match="dify_cache:*", count=100))
    if keys:
        r.delete(*keys)
    return {"message": f"已清除 {len(keys)} 条缓存"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8005)
