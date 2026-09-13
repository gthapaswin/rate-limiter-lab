# rate-limiter-lab

Four rate-limiting algorithms behind one interface, each runnable against an
in-memory backend (single process) or a Redis backend (multi-instance correct,
via atomic Lua scripting). Includes a benchmark harness that drives constant,
bursty, and ramping traffic through every algorithm/backend combination and a
FastAPI middleware that ships the result as reusable production code.

> Status: under construction — built in phases. This README is filled in with
> real measured findings once the benchmark runs (Phase 8–9).

## The four algorithms

| Algorithm | Core idea | Weakness to watch |
|---|---|---|
| Fixed Window Counter | Count per fixed clock bucket, reset at the boundary | ~2x burst across a boundary |
| Sliding Window Log | Timestamp per request, count those in the trailing window | Accurate but O(n) memory per client |
| Sliding Window Counter | Weighted blend of current + previous window | O(1) memory, slightly approximate |
| Token Bucket | Bucket refills at a fixed rate; each request spends a token | Allows tuned bursts up to bucket size |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"

# exercise the library directly
python -c "from ratelimiter import build_limiter; \
rl = build_limiter('fixed_window','memory',limit=5,window=60); \
print([rl.allow_request('me') for _ in range(7)])"
```

## Run the demo app

```bash
uvicorn demo_app.main:app --reload
# in another shell, send more than the limit and watch for 429s:
for i in $(seq 1 15); do curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/ping; done
```

Configuration is via environment variables (`RL_ALGORITHM`, `RL_BACKEND`,
`RL_LIMIT`, `RL_WINDOW`, `RL_REDIS_URL`).

## Architecture

See `ratelimiter/base.py`. Each algorithm expresses its atomic "read counter →
decide → write" step twice: once in Python (run under a lock by the in-memory
backend) and once in Lua (run atomically by Redis). Backends know nothing about
the algorithms; algorithms know nothing about storage. That is what makes the
in-memory ↔ Redis swap a one-line config change.
