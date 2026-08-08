"""
Rate limiting for the public demo API.

Lives in its own module rather than in server.py because demo_api needs the
limiter to decorate its routes, and server.py imports demo_api from the bottom
of its own module body — a `from server import limiter` in demo_api would be
circular and break depending on which module got imported first.

Two things are being defended here. The demo is unauthenticated, so the only
thing standing between a script and the Gemini quota is these limits; and the
box is a single 1-OCPU Always Free instance, so a handful of concurrent
embedding or PCA calls is enough to make it unresponsive for everyone else.
"""
import os

from flask import request
from flask_limiter import Limiter

# The request chain in production is:
#
#     visitor -> Vercel edge -> nginx (127.0.0.1) -> gunicorn
#
# nginx appends its own peer to X-Forwarded-For, so the rightmost entry is
# always the address nginx actually accepted the connection from — that one is
# trustworthy. Anything to the left of it was supplied by that peer.
_VERCEL_HOPS = 1


def client_key():
    """
    Best-effort per-visitor identity for rate limiting.

    Werkzeug's remote_addr is useless behind the proxies — every request would
    look like 127.0.0.1 and the whole internet would share one bucket, which
    turns the rate limit into a self-inflicted denial of service the first time
    anyone runs a loop against it.

    So: drop the entry nginx appended (that is nginx's peer, the Vercel edge)
    and take the rightmost remaining entry, which is the client IP Vercel
    forwarded. If nothing remains, the peer *was* the client — someone reaching
    the origin directly rather than through Vercel — and its address is used.

    A visitor who bypasses Vercel and hits the origin directly can forge
    X-Forwarded-For and land in a bucket of their choosing. That cannot be
    closed without an allowlist of Vercel's egress addresses, which Vercel does
    not publish for Hobby projects. It is why the expensive endpoints also
    carry a shared_budget() limit, which no amount of spoofing can escape.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]

    if len(hops) > _VERCEL_HOPS:
        return hops[-(_VERCEL_HOPS + 1)]
    if hops:
        return hops[-1]
    return request.remote_addr or "unknown"


def shared_budget():
    """
    A single bucket for every caller, used alongside the per-visitor limits on
    the endpoints that spend money or CPU. Per-visitor limits assume callers
    are distinguishable; this one does not assume anything, so it is what
    actually bounds the worst case — a botnet, a spoofed X-Forwarded-For, or
    simply the demo link doing better than expected.
    """
    return "demo-shared"


# Limits are stored in MongoDB rather than in process memory because gunicorn
# runs two workers: with memory:// each worker keeps its own counters, so every
# limit is silently doubled and which one a request lands on is arbitrary.
# Atlas is already a hard dependency, so this adds a backend, not a service.
_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI") or os.getenv("MONGODB_URI") or "memory://"

limiter = Limiter(
    key_func=client_key,
    default_limits=["200 per hour"],
    storage_uri=_STORAGE_URI,
    # A limiter that 500s when Atlas hiccups would be worse than one that
    # briefly stops limiting.
    swallow_errors=True,
    headers_enabled=True,
)
