# Architecture

## Request path

1. **API boundary** reads a bounded request body, validates messages, and rejects streaming.
2. **Router** counts query tokens plus small feature weights and selects the configured fast or reasoning endpoint.
3. **Cache** optionally checks a SHA-256 exact key, then checks recent entries for token-set cosine similarity under the same effective model and matching conversation context.
4. **Provider adapter** forwards the request to a configured endpoint with a bounded timeout, no redirects, and capped response size.
5. **Metrics** record request route/status, cache outcome, latency, and in-flight count without prompt labels.

```mermaid
sequenceDiagram
  actor Client
  participant API as Gateway API
  participant Router as Complexity Router
  participant Redis
  participant Model as Selected OpenAI-compatible model
  participant Metrics as Prometheus
  Client->>API: POST chat completion
  API->>Router: Validated messages and options
  Router-->>API: Endpoint + effective model
  opt CACHE_ENABLED=true
    API->>Redis: Exact digest lookup
    alt Exact miss and deterministic semantic request
      API->>Redis: Compare recent compatible query signatures
    end
  end
  alt Cache hit
    Redis-->>API: Cached response
  else Cache miss / cache unavailable
    API->>Model: Forward with selected model
    Model-->>API: Bounded JSON response
    opt Cache available
      API->>Redis: Store result with TTL
    end
  end
  API->>Metrics: Increment counters and observe latency
  API-->>Client: Response with route/cache headers
```

## Known limits

There is no authentication, tenant isolation, streaming, circuit breaker, distributed rate limit, learned router, or Redis vector index. Similarity uses a bounded recent-key list and token overlap; it is not embedding-based. Redis errors fall back to the upstream model. Process metrics are local to each replica.
