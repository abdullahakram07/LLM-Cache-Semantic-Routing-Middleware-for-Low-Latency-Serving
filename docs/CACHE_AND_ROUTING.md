# Cache and routing details

## Cache

Caching is disabled by default (`CACHE_ENABLED=false`). Cache lookup/write requires `temperature: 0`, `n: 1` (or omitted), no tools, and no response format. When enabled, exact cache entries and semantic candidate records are stored in Redis with `CACHE_TTL_SECONDS` (default 300). Candidate search checks at most 500 recent records. Semantic threshold defaults to `0.92` and can be changed with `SIMILARITY_THRESHOLD`.

Exact keys include request fields and the selected model. Semantic hits require temperature zero or omitted, no tools, no response format, a matching model, and the same context with the final user query removed from the context fingerprint. The final user queries are compared using token-set cosine similarity. Similarity is inherently lossy: a hit may not be appropriate even when the score exceeds the threshold.

Redis stores prompts, context, and responses for cache lifetime. Apply Redis authentication/network isolation, appropriate encryption, retention, and tenant boundaries before production. Do not share a cache across tenants unless the key and isolation model explicitly include tenant identity.

## Routing

The baseline counts distinct word tokens in message content and adds fixed complexity weight for tools/response format. It selects FAST below `FAST_ROUTE_TOKEN_THRESHOLD` (default 35) if configured; otherwise selects reasoning, then falls back to the sole configured endpoint. The model name is set by the gateway. This heuristic should be calibrated against a reviewed workload and cost/quality objectives before relying on it.

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `FAST_MODEL_URL` / `FAST_MODEL_NAME` | unset | Fast endpoint/model pair |
| `REASONING_MODEL_URL` / `REASONING_MODEL_NAME` | unset | Reasoning endpoint/model pair |
| `FAST_ROUTE_TOKEN_THRESHOLD` | `35` | Word-count cutoff for fast route |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `CACHE_ENABLED` | `false` | Enable prompt/response caching |
| `CACHE_TTL_SECONDS` | `300` | Cache entry lifetime |
| `SIMILARITY_THRESHOLD` | `0.92` | Token-set similarity cutoff |
| `UPSTREAM_API_KEY` | unset | Bearer credential forwarded to upstream |
