"""
囤囤鼠导购助手（带互斥锁 + 皮肤关键词筛选版）
端口: 8002
"""

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from typing import Optional
import httpx
import redis
import os

app = FastAPI(title="囤囤鼠导购助手")

BASE_URL = "https://jyym.jiaoyiyou.com/zh/foreground"
LIST_API_URL = f"{BASE_URL}/goods/listWithPage"
REQUEST_TIMEOUT = 15.0
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Referer": "https://tuntuns.com/",
    "Origin": "https://tuntuns.com",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}

r = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=6379, db=0, decode_responses=True)
LOCK_TTL = 15


def make_lock_key(max_price, min_price, keyword):
    raw = f"guide_lock:{min_price or 'none'}_{max_price or 'none'}_{keyword or 'none'}"
    return raw


@app.get("/search_accounts")
async def search_accounts(
    max_price: Optional[str] = Query(None, description="最高价格（元）"),
    min_price: Optional[str] = Query(None, description="最低价格（元）"),
    keyword: Optional[str] = Query(None, description="皮肤关键词，如：信条、暗星、龙牙"),
):
    lock_key = make_lock_key(max_price, min_price, keyword)

    if r.exists(lock_key):
        return JSONResponse(content={
            "success": True,
            "message": "正在查询中，请稍候...",
            "source": "lock_wait",
        })

    try:
        r.set(lock_key, "1", ex=LOCK_TTL)

        # 解析价格
        if max_price in (None, "null", "", "undefined"):
            max_price_float = None
        else:
            try:
                max_price_float = float(max_price)
            except (ValueError, TypeError):
                max_price_float = None

        if min_price in (None, "null", "", "undefined"):
            min_price_float = None
        else:
            try:
                min_price_float = float(min_price)
            except (ValueError, TypeError):
                min_price_float = None

        # 解析关键词
        keyword_str = None
        if keyword not in (None, "null", "", "undefined"):
            keyword_str = keyword.strip()

        # 构建请求参数
        list_params = {"page": 1, "page_size": 100, "num": 1}

        # 如果有皮肤关键词，用上游 API 原生过滤
        if keyword_str:
            list_params["skin_list1"] = [keyword_str]
            list_params["skin_way1"] = 2

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=DEFAULT_HEADERS) as client:
            try:
                list_resp = await client.post(LIST_API_URL, json=list_params)
                list_data = list_resp.json()
            except Exception as e:
                return {"success": False, "error": f"获取商品列表失败: {str(e)}"}

            if list_data.get("code") != 0:
                return {"success": False, "error": "商品列表接口返回错误"}

            goods_list = list_data.get("data", {}).get("data", [])
            if not goods_list:
                price_hint = ""
                if min_price_float and max_price_float:
                    price_hint = f"{int(min_price_float)}-{int(max_price_float)}元"
                elif max_price_float:
                    price_hint = f"{int(max_price_float)}元以内"
                elif min_price_float:
                    price_hint = f"{int(min_price_float)}元以上"
                keyword_hint = f"含「{keyword_str}」皮肤的" if keyword_str else ""
                return {
                    "success": True,
                    "count": 0,
                    "recommendations": [],
                    "message": f"老板，很抱歉，平台暂时没有{price_hint}{keyword_hint}账号，您可以换个条件再发我帮您看看~",
                }

            recommendations = []
            for item in goods_list:
                price_fen = item.get("price")
                if price_fen is None:
                    continue
                price_yuan = price_fen / 100.0

                if max_price_float is not None and price_yuan > max_price_float:
                    continue
                if min_price_float is not None and price_yuan < min_price_float:
                    continue

                recommendations.append({
                    "title": item.get("title", ""),
                    "price_yuan": price_yuan,
                    "skin": item.get("skin", ""),
                    "goods_no": item.get("goods_no"),
                    "created_at": item.get("created_at"),
                    "url": f"https://tuntuns.com/#/goodsDetail?goodsNo={item.get('goods_no')}"
                })

            recommendations.sort(key=lambda x: x["created_at"], reverse=True)

            if not recommendations:
                price_hint = ""
                if min_price_float and max_price_float:
                    price_hint = f"{int(min_price_float)}-{int(max_price_float)}元"
                elif max_price_float:
                    price_hint = f"{int(max_price_float)}元以内"
                elif min_price_float:
                    price_hint = f"{int(min_price_float)}元以上"
                keyword_hint = f"含「{keyword_str}」皮肤的" if keyword_str else ""
                return {
                    "success": True,
                    "count": 0,
                    "recommendations": [],
                    "message": f"老板，很抱歉，平台暂时没有{price_hint}{keyword_hint}账号，您可以换个条件再发我帮您看看~",
                }

            return {
                "success": True,
                "count": len(recommendations),
                "recommendations": recommendations[:10],
            }

    finally:
        r.delete(lock_key)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
