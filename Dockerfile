FROM python:3.11-slim

WORKDIR /app

# Install the library + web extras. Copy metadata first for better layer caching.
COPY pyproject.toml README.md ./
COPY ratelimiter ./ratelimiter
RUN pip install --no-cache-dir ".[web]"

COPY demo_app ./demo_app

EXPOSE 8000
CMD ["uvicorn", "demo_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
