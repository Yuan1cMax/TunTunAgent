"""
Redis 缓存代理
放在 Dify API 前面，缓存 RAG 问答结果，减少重复调用 LLM
端口: 8005
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import redis
import hashlib
import json
import time
import os

app = FastAPI(title="Redis 缓存代理")

# ==================== 配置 ====================

DIFY_API_URL = os.getenv("DIFY_API_URL", "http://localhost:81/v1/chat-messages")
DIFY_API_KEY = os.getenv("DIFY_API_KEY")

r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=6379, db=0, decode_responses=True)

CACHE_TTL = 3600
LOCK_TTL = 30

HUMAN_KEYWORDS = ["人工客服", "转人工", "联系客服", "人工服务"]


# ==================== 工具函数 ====================

def make_cache_key(query: str) -> str:
    query_hash = hashlib.md5(query.strip().lower().encode("utf-8")).hexdigest()
    return f"dify_cache:{query_hash}"


def make_lock_key(query: str) -> str:
    query_hash = hashlib.md5(query.strip().lower().encode("utf-8")).hexdigest()
    return f"dify_lock:{query_hash}"


def should_cache(answer: str) -> bool:
    for keyword in HUMAN_KEYWORDS:
        if keyword in answer:
            return False
    return True


# ==================== 核心接口 ====================

@app.post("/chat")
async def chat_proxy(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "请求格式错误"})

    query = body.get("query", "").strip()
    user = body.get("user", "default_user")

    if not query:
        return JSONResponse(status_code=400, content={"error": "query 不能为空"})

    cache_key = make_cache_key(query)
    lock_key = make_lock_key(query)

    # ---- 第一步：查缓存 ----
    cached = r.get(cache_key)
    if cached:
        cached_data = json.loads(cached)
        return JSONResponse(content={
            "answer": cached_data["answer"],
            "source": "cache",
            "cached_at": cached_data["cached_at"],
        })

    # ---- 第二步：检查互斥锁 ----
    if r.exists(lock_key):
        return JSONResponse(content={
            "answer": "您的问题正在处理中，请稍候...",
            "source": "lock_wait",
        })

    # ---- 第三步：加锁 → 调 Dify → 存缓存 → 释放锁 ----
    try:
        r.set(lock_key, "1", ex=LOCK_TTL)

        if not DIFY_API_KEY:
            return JSONResponse(
                status_code=500,
                content={"answer": "服务端未配置 DIFY_API_KEY，请先设置环境变量后再试。"}
            )

        async with httpx.AsyncClient(timeout=60.0) as client:
            dify_response = await client.post(
                DIFY_API_URL,
                headers={
                    "Authorization": f"Bearer {DIFY_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "inputs": {},
                    "query": query,
                    "response_mode": "blocking",
                    "user": user,
                },
            )

        if dify_response.status_code != 200:
            return JSONResponse(
                status_code=502,
                content={"answer": f"系统繁忙，请稍后再试~ (错误码: {dify_response.status_code})"}
            )

        dify_data = dify_response.json()
        answer = dify_data.get("answer", "")

        if answer and should_cache(answer):
            cache_data = {
                "answer": answer,
                "cached_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            r.set(cache_key, json.dumps(cache_data, ensure_ascii=False), ex=CACHE_TTL)

        return JSONResponse(content={
            "answer": answer,
            "source": "dify_live",
            "conversation_id": dify_data.get("conversation_id", ""),
        })
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"answer": "抱歉，系统响应超时了，请稍后再试一下~"}
        )
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"answer": "抱歉，系统暂时无法连接，请稍后再试~"}
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"answer": f"系统出了点小问题，请稍后再试~ ({type(e).__name__})"}
        )
    finally:
        r.delete(lock_key)


# ==================== 管理接口 ====================

@app.get("/cache/stats")
async def cache_stats():
    keys = r.keys("dify_cache:*")
    return {
        "cached_count": len(keys),
        "keys": keys[:20],
    }


@app.delete("/cache/clear")
async def clear_cache():
    keys = r.keys("dify_cache:*")
    if keys:
        r.delete(*keys)
    return {"message": f"已清除 {len(keys)} 条缓存"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
