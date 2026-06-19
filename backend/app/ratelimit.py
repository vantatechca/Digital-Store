"""Lightweight in-memory rate limiting (per client IP, per route).

Good enough for a single-instance deploy (Render Starter). Resets on restart.
Use as a route dependency:  Depends(rate_limit("checkout", 20, 60))
"""
import time
from collections import defaultdict

from fastapi import HTTPException, Request

_hits: dict[str, list[float]] = defaultdict(list)


def rate_limit(name: str, limit: int, window_seconds: int):
    def dependency(request: Request):
        ip = request.client.host if request.client else "unknown"
        key = f"{name}:{ip}"
        now = time.time()
        recent = [t for t in _hits[key] if now - t < window_seconds]
        if len(recent) >= limit:
            raise HTTPException(429, "Too many requests — please slow down and try again shortly.")
        recent.append(now)
        _hits[key] = recent
    return dependency
