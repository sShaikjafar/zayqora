"""Simple in-memory rate limiting per client IP (spec 17.3).
Good for a single server process. Use a shared store such as Redis when running several instances.
Behind a proxy, start uvicorn with --proxy-headers so the real client IP is used."""
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_hits: dict = defaultdict(deque)


def reset() -> None:
    _hits.clear()


def limit(name: str, max_calls: int, per_seconds: int):
    def dependency(request: Request) -> None:
        if os.getenv("RATE_LIMIT_DISABLED") == "1":
            return
        ip = request.client.host if request.client else "unknown"
        if os.getenv("TRUST_PROXY") == "1":  # behind Vercel / a reverse proxy: use the visitor's real address
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            ip = forwarded or ip
        now = time.monotonic()
        window = _hits[(name, ip)]
        while window and now - window[0] > per_seconds:
            window.popleft()
        if len(window) >= max_calls:
            raise HTTPException(
                429, "Too many requests. Please wait a moment and try again.",
                headers={"Retry-After": str(per_seconds)},
            )
        window.append(now)

    return dependency
