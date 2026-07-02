"""
Lightweight benchmark tool for public demo endpoints.

Supported modes:
- GET: benchmark a search-style endpoint with query parameters
- POST: benchmark a custom JSON chat endpoint
- DIFY: benchmark a Dify-compatible /v1/chat-messages endpoint

No real API keys or fixed production URLs are embedded in this file.
Pass them via CLI arguments or environment variables when running locally.
"""

import argparse
import asyncio
import json
import os
import statistics
import time
from collections import Counter

import httpx


DEFAULT_QUERY = "500以内有信条号吗"
DEFAULT_KEYWORD = "信条"
DEFAULT_USER_PREFIX = "benchmark-user"


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * ratio) - 1))
    return ordered[index]


async def run_one(
    client: httpx.AsyncClient,
    url: str,
    method: str,
    query: str,
    user: str,
    attempt: int,
    max_price: str,
    min_price: str,
    keyword: str,
    api_key: str,
) -> dict:
    started = time.perf_counter()
    try:
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        if method == "GET":
            params = {}
            if max_price:
                params["max_price"] = max_price
            if min_price:
                params["min_price"] = min_price
            if keyword:
                params["keyword"] = keyword
            response = await client.get(url, params=params, headers=headers)
        elif method == "DIFY":
            response = await client.post(
                url,
                headers={
                    **headers,
                    "Content-Type": "application/json",
                },
                json={
                    "inputs": {},
                    "query": query,
                    "response_mode": "blocking",
                    "user": f"{user}-{attempt}",
                },
            )
        else:
            response = await client.post(
                url,
                headers=headers,
                json={
                    "query": query,
                    "user": f"{user}-{attempt}",
                },
            )
        elapsed = time.perf_counter() - started
        source = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                source = str(payload.get("source", ""))
        except Exception:
            source = ""
        return {
            "ok": response.status_code == 200,
            "status_code": response.status_code,
            "elapsed": elapsed,
            "source": source,
            "body_preview": response.text[:200].replace("\n", " "),
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {
            "ok": False,
            "status_code": "EXC",
            "elapsed": elapsed,
            "source": "",
            "body_preview": f"{type(exc).__name__}: {exc!r}",
        }


async def run_benchmark(
    url: str,
    method: str,
    query: str,
    user_prefix: str,
    total: int,
    concurrency: int,
    timeout: float,
    max_price: str,
    min_price: str,
    keyword: str,
    api_key: str,
) -> list[dict]:
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        semaphore = asyncio.Semaphore(concurrency)

        async def wrapped(index: int) -> dict:
            async with semaphore:
                return await run_one(
                    client,
                    url,
                    method,
                    query,
                    user_prefix,
                    index,
                    max_price,
                    min_price,
                    keyword,
                    api_key,
                )

        tasks = [asyncio.create_task(wrapped(i)) for i in range(total)]
        return await asyncio.gather(*tasks)


def print_summary(results: list[dict], total: int, concurrency: int) -> None:
    elapsed_values = [item["elapsed"] for item in results]
    ok_values = [item for item in results if item["ok"]]
    status_counter = Counter(str(item["status_code"]) for item in results)
    source_counter = Counter(item["source"] for item in results if item["source"])
    success_rate = (len(ok_values) / len(results) * 100) if results else 0.0

    print("=== Benchmark Summary ===")
    print(f"Total requests: {total}")
    print(f"Concurrency: {concurrency}")
    print(f"Success count: {len(ok_values)}")
    print(f"Success rate: {success_rate:.2f}%")
    print(f"Average latency: {statistics.mean(elapsed_values):.3f}s")
    print(f"Median latency: {statistics.median(elapsed_values):.3f}s")
    print(f"P95 latency: {percentile(elapsed_values, 0.95):.3f}s")
    print(f"P99 latency: {percentile(elapsed_values, 0.99):.3f}s")
    print(f"Min latency: {min(elapsed_values):.3f}s")
    print(f"Max latency: {max(elapsed_values):.3f}s")
    print("Status distribution:")
    for status_code, count in sorted(status_counter.items()):
        print(f"  {status_code}: {count}")
    if source_counter:
        print("Source distribution:")
        for source, count in sorted(source_counter.items()):
            print(f"  {source}: {count}")

    failed = [item for item in results if not item["ok"]]
    if failed:
        print("Sample failures:")
        for sample in failed[:5]:
            print(f"  status={sample['status_code']} elapsed={sample['elapsed']:.3f}s body={sample['body_preview']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Concurrent benchmark for TunTunAgent endpoints.")
    parser.add_argument(
        "--url",
        default=os.getenv("BENCHMARK_URL", ""),
        help="Target URL, e.g. https://your-domain.example/v1/chat-messages",
    )
    parser.add_argument(
        "--method",
        default=os.getenv("BENCHMARK_METHOD", "POST").upper(),
        choices=["GET", "POST", "DIFY"],
        help="Request mode: GET for search API, POST for custom chat API, DIFY for /v1/chat-messages",
    )
    parser.add_argument("--query", default=os.getenv("BENCHMARK_QUERY", DEFAULT_QUERY), help="Query to send")
    parser.add_argument("--keyword", default=os.getenv("BENCHMARK_KEYWORD", DEFAULT_KEYWORD), help="Keyword for GET mode")
    parser.add_argument("--max-price", default=os.getenv("BENCHMARK_MAX_PRICE", "700"), help="Max price for GET mode")
    parser.add_argument("--min-price", default=os.getenv("BENCHMARK_MIN_PRICE", ""), help="Min price for GET mode")
    parser.add_argument("--api-key", default=os.getenv("BENCHMARK_API_KEY", ""), help="API key for Dify or protected endpoints")
    parser.add_argument("--user-prefix", default=os.getenv("BENCHMARK_USER_PREFIX", DEFAULT_USER_PREFIX), help="Prefix for generated user IDs")
    parser.add_argument("--total", type=int, default=int(os.getenv("BENCHMARK_TOTAL", "50")), help="Total request count")
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("BENCHMARK_CONCURRENCY", "50")), help="Concurrent request count")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("BENCHMARK_TIMEOUT", "30")), help="Per-request timeout in seconds")
    parser.add_argument("--output", default=os.getenv("BENCHMARK_OUTPUT", ""), help="Optional JSON output path")
    args = parser.parse_args()

    if not args.url:
        raise SystemExit("Missing target URL. Use --url or set BENCHMARK_URL.")

    started = time.perf_counter()
    results = asyncio.run(
        run_benchmark(
            url=args.url,
            method=args.method,
            query=args.query,
            user_prefix=args.user_prefix,
            total=args.total,
            concurrency=args.concurrency,
            timeout=args.timeout,
            max_price=args.max_price,
            min_price=args.min_price,
            keyword=args.keyword,
            api_key=args.api_key,
        )
    )
    total_elapsed = time.perf_counter() - started

    print_summary(results, args.total, args.concurrency)
    print(f"Wall time: {total_elapsed:.3f}s")

    if args.output:
        payload = {
            "url": args.url,
            "method": args.method,
            "query": args.query,
            "user_prefix": args.user_prefix,
            "keyword": args.keyword,
            "max_price": args.max_price,
            "min_price": args.min_price,
            "api_key_used": bool(args.api_key),
            "total": args.total,
            "concurrency": args.concurrency,
            "timeout": args.timeout,
            "wall_time": total_elapsed,
            "results": results,
        }
        with open(args.output, "w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        print(f"Saved detailed results to {args.output}")


if __name__ == "__main__":
    main()
