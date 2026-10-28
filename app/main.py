from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

app = FastAPI(title="LLM Cache and Semantic Router", version="0.1.0")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CACHE_ENABLED = os.environ.get("CACHE_ENABLED", "false").lower() == "true"
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.92"))
MAX_CACHE_SCAN = 500
MAX_BODY = 1_000_000
MAX_RESPONSE = 4_000_000
FAST_URL = os.environ.get("FAST_MODEL_URL", "").strip()
FAST_MODEL = os.environ.get("FAST_MODEL_NAME", "").strip()
REASON_URL = os.environ.get("REASONING_MODEL_URL", "").strip()
REASON_MODEL = os.environ.get("REASONING_MODEL_NAME", "").strip()
UPSTREAM_KEY = os.environ.get("UPSTREAM_API_KEY", "")
_redis = None
REQUESTS = Counter("llm_gateway_requests_total", "Gateway requests", ["route", "status"])
CACHE = Counter("llm_gateway_cache_total", "Cache lookups", ["result"])
LATENCY = Histogram("llm_gateway_request_duration_seconds", "Gateway request duration", buckets=(.01,.025,.05,.1,.25,.5,1,2,5,10,30,60))
ACTIVE = Gauge("llm_gateway_in_flight", "Requests currently in progress")


async def _cache_client():
    global _redis
    if not CACHE_ENABLED:
        return None
    if _redis is None:
        from redis.asyncio import Redis
        _redis = Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
        await _redis.ping()
    return _redis


def _normalize(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w']+", text.casefold()))


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / math.sqrt(len(a) * len(b)) if a and b else 0.0


def _query_text(payload: dict[str, Any]) -> str:
    return " ".join(str(item.get("content", "")) for item in payload.get("messages", []) if isinstance(item, dict))


def _route(payload: dict[str, Any]) -> tuple[str, str, str]:
    text = _query_text(payload)
    complexity = len(_tokens(text)) + 4 * bool(payload.get("tools")) + 3 * bool(payload.get("response_format"))
    if complexity <= int(os.environ.get("FAST_ROUTE_TOKEN_THRESHOLD", "35")) and FAST_URL and FAST_MODEL:
        return "fast", FAST_URL, FAST_MODEL
    if REASON_URL and REASON_MODEL:
        return "reasoning", REASON_URL, REASON_MODEL
    if FAST_URL and FAST_MODEL:
        return "fast", FAST_URL, FAST_MODEL
    raise HTTPException(503, "Configure at least one complete FAST_MODEL_URL/NAME or REASONING_MODEL_URL/NAME pair")


async def _exact_get(client, key: str) -> dict[str, Any] | None:
    raw = await client.get(f"llm-cache:exact:{key}")
    return json.loads(raw) if raw else None


async def _similar_get(client, query: str, context_key: str) -> dict[str, Any] | None:
    keys = await client.lrange("llm-cache:recent", 0, MAX_CACHE_SCAN - 1)
    for key in keys:
        raw = await client.get(key)
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if (isinstance(item, dict) and item.get("context_key") == context_key
                and isinstance(item.get("query"), str) and _similarity(query, item["query"]) >= SIMILARITY_THRESHOLD):
            return item.get("response") if isinstance(item.get("response"), dict) else None
    return None


async def _cache_put(client, key: str, query: str, context_key: str, result: dict[str, Any]) -> None:
    record_key = f"llm-cache:item:{key}"
    async with client.pipeline(transaction=True) as pipe:
        pipe.set(f"llm-cache:exact:{key}", json.dumps(result), ex=CACHE_TTL)
        pipe.set(record_key, json.dumps({"query": query, "context_key": context_key, "response": result}), ex=CACHE_TTL)
        pipe.lrem("llm-cache:recent", 0, record_key)
        pipe.lpush("llm-cache:recent", record_key)
        pipe.ltrim("llm-cache:recent", 0, MAX_CACHE_SCAN - 1)
        await pipe.execute()


@app.get("/health")
async def health() -> dict[str, Any]:
    state = "disabled"
    if CACHE_ENABLED:
        try:
            await _cache_client()
            state = "ready"
        except Exception:
            state = "unavailable"
    return {"status": "ok", "cache": state, "cache_enabled": CACHE_ENABLED,
            "fast_route_configured": bool(FAST_URL and FAST_MODEL),
            "reasoning_route_configured": bool(REASON_URL and REASON_MODEL)}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/chat/completions")
async def completions(request: Request) -> Response:
    started = time.perf_counter()
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise HTTPException(413, "Request body exceeds 1 MiB")
        body.extend(chunk)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(400, "Body must be valid JSON") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list) or not payload["messages"]:
        raise HTTPException(422, "Expected a non-empty messages array")
    if payload.get("stream") is True:
        raise HTTPException(422, "Streaming is not supported by this MVP")
    for index, message in enumerate(payload["messages"]):
        if not isinstance(message, dict) or not isinstance(message.get("role"), str) or not isinstance(message.get("content"), str):
            raise HTTPException(422, f"messages[{index}] must contain string role and content")
    route, upstream_url, model = _route(payload)
    effective_payload = dict(payload)
    effective_payload["model"] = model
    semantic_allowed = (type(payload.get("temperature")) in {int, float} and payload.get("temperature") == 0
                        and not payload.get("tools") and not payload.get("response_format"))
    cacheable = semantic_allowed and payload.get("n", 1) == 1
    normalized = _normalize(effective_payload)
    cache_key = hashlib.sha256(normalized.encode()).hexdigest()
    semantic_payload = json.loads(normalized)
    user_messages = [item for item in semantic_payload["messages"] if item.get("role") == "user"]
    query = user_messages[-1].get("content", "") if user_messages else ""
    if user_messages:
        user_messages[-1]["content"] = "<SEMANTIC_QUERY>"
    semantic_payload["model"] = model
    context_key = hashlib.sha256(_normalize(semantic_payload).encode()).hexdigest()
    ACTIVE.inc()
    try:
        cache_client = None
        if CACHE_ENABLED and cacheable:
            try:
                cache_client = await _cache_client()
                cached = await _exact_get(cache_client, cache_key)
                if cached is not None:
                    CACHE.labels("exact").inc()
                    REQUESTS.labels(route, "cache_hit").inc()
                    return Response(json.dumps(cached), media_type="application/json", headers={"X-Cache": "exact", "X-Route": route})
                cached = await _similar_get(cache_client, query, context_key) if semantic_allowed and query else None
                if cached is not None:
                    CACHE.labels("semantic").inc()
                    REQUESTS.labels(route, "cache_hit").inc()
                    return Response(json.dumps(cached), media_type="application/json", headers={"X-Cache": "semantic", "X-Route": route})
            except Exception:
                CACHE.labels("unavailable").inc()
                cache_client = None  # Cache failure falls back to the model.
        forwarded = dict(payload)
        forwarded["model"] = model
        headers = {"Content-Type": "application/json"}
        if UPSTREAM_KEY:
            headers["Authorization"] = f"Bearer {UPSTREAM_KEY}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120), follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", upstream_url, json=forwarded, headers=headers) as upstream:
                    if upstream.status_code >= 400:
                        raise HTTPException(502, f"Selected model returned HTTP {upstream.status_code}")
                    response_body = bytearray()
                    async for chunk in upstream.aiter_bytes():
                        response_body.extend(chunk)
                        if len(response_body) > MAX_RESPONSE:
                            raise HTTPException(502, "Model response exceeds 4 MiB")
            result = json.loads(response_body)
            if not isinstance(result, dict):
                raise ValueError("model response must be an object")
        except httpx.TimeoutException:
            REQUESTS.labels(route, "timeout").inc()
            raise HTTPException(504, "Selected model timed out") from None
        except httpx.HTTPError:
            REQUESTS.labels(route, "upstream_error").inc()
            raise HTTPException(502, "Could not reach selected model") from None
        except (json.JSONDecodeError, ValueError):
            REQUESTS.labels(route, "invalid_response").inc()
            raise HTTPException(502, "Selected model returned invalid JSON") from None
        if cache_client is not None and cacheable:
            try:
                await _cache_put(cache_client, cache_key, query, context_key, result)
            except Exception:
                CACHE.labels("write_error").inc()
        REQUESTS.labels(route, "success").inc()
        return Response(json.dumps(result), media_type="application/json", headers={"X-Cache": "miss", "X-Route": route})
    finally:
        ACTIVE.dec()
        LATENCY.observe(time.perf_counter() - started)
