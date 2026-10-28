# Deployment

## Docker Compose

Set at least one complete endpoint/model pair and run:

```sh
export FAST_MODEL_URL='https://fast.example/v1/chat/completions'
export FAST_MODEL_NAME='fast-model'
export REASONING_MODEL_URL='https://reasoning.example/v1/chat/completions'
export REASONING_MODEL_NAME='reasoner'
docker compose up --build
```

The gateway publishes only to localhost. Redis is private to the Compose network and configured as ephemeral (no persistence). Provide a production Redis URL and secrets through a secret manager; do not commit `.env` files.

## Production checklist

Add authentication/authorization, tenant-scoped cache keys, upstream host allowlists, Redis TLS/auth/eviction policy, model-specific credentials, rate limits, request deadlines, log redaction, deployment health checks, cache invalidation, and an incident/retention policy. Model URLs are environment-controlled and the service should not be exposed to untrusted clients without egress controls.
