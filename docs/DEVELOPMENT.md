# Development

Use Python 3.12 or later. Keep upstreams local or explicitly controlled during development. Caching remains disabled unless deliberately enabled.

Suggested contributor checks:

```sh
python -m compileall -q app
python -m pytest
```

Before changing cache logic, add checks for exact key model separation, semantic context isolation, temperature/tool restrictions, Redis timeout/failure fallback, and TTL behavior. Before changing routing, cover missing and partial endpoint configuration. These commands are guidance, not a claim that they were run for a change.
