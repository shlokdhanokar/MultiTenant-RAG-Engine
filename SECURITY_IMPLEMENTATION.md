# Security implementation

What protects what, and why each control is shaped the way it is. This is a
description of the system as built — not a policy document and not a checklist.

The engine has two very different exposures: an authenticated tenant API, where
the threat is one tenant reaching another's data; and an unauthenticated public
demo, where the threat is a stranger spending the provider quota. They are
defended differently, and the sections below are grouped that way.

---

## Tenant isolation

**Enforced inside the vector search, not after it.**

`perform_semantic_retrieval` passes `knowledge_base_id` as a native Atlas
pre-filter within the `$vectorSearch` stage:

```python
{"$vectorSearch": {
    "index": "vector_index",
    "path": "embedding",
    "queryVector": query_embedding,
    "numCandidates": max(candidate_pool * 10, 150),
    "limit": candidate_pool,
    "filter": {"knowledge_base_id": knowledge_base_id},
}}
```

Filtering *after* the ANN search would still be correct, but it would not be
usable: a large tenant's chunks would fill the candidate pool before the filter
ran, and a small tenant would get zero results even when relevant content
exists. The failure mode is silent — an empty answer, not an error — which is
what makes it worth stating explicitly.

This depends on `knowledge_base_id` being declared as a `filter` field on the
Atlas index. If it is missing from the index definition, the pre-filter is
ignored and isolation is gone. See the index definition in
[README.md](README.md#-production-notes).

---

## API keys

Two tiers, both stored only as SHA-256 digests — a database dump yields no
usable key:

| Tier | Header | Scope |
|---|---|---|
| Master key | `apikey` | Tenant/admin operations: create projects, read usage |
| Project key | `x-apikey` | Chat and upload, scoped to one knowledge base |

`project_key_required` resolves the hash to the owning admin *and* to the
specific `projectId` the key was issued for, and pins both onto the request.
Routes therefore never take a caller-supplied project id — a key cannot be
pointed at a different knowledge base by changing the request body.

Third-party integration credentials (OAuth tokens, API keys for Shopify, Slack,
Calendly, Google) are encrypted at rest with Fernet under
`CREDENTIAL_ENCRYPTION_KEY` — see `phase4_integrations/crypto.py`.

---

## Signed image URLs

`GET /image/<image_id>` is unauthenticated by necessity: WhatsApp's servers
fetch these URLs themselves and there is no way to attach a header to that
fetch. A raw GridFS ObjectId would be enumerable, so the link carries its own
proof instead:

```
/image/<id>?exp=<unix-ts>&sig=<hmac-sha256(id.exp, IMAGE_URL_SIGNING_SECRET)>
```

Verification is constant-time and rejects both a bad signature and an expired
`exp` with 403. The TTL is 24 hours.

The signature covers `image_id` and `expiry` only — deliberately not the host.
The demo UI is served from two origins (nginx on the box, and a Vercel
deployment that reverse-proxies `/image/` back to it), so demo image links are
host-relative and ride whichever origin the visitor is on. Only the WhatsApp
payload builds absolute URLs from `APP_BASE_URL`, because Meta's servers have
no origin to be relative to.

`IMAGE_URL_SIGNING_SECRET` is required at boot; the app refuses to start
without it rather than defaulting to something guessable.

---

## Session and user linkage

Session hijacking is blocked at the database layer, in
`get_or_create_session`, rather than in each endpoint that happens to
remember to check:

```
Session lookup
    ├─ exists, session.userId == requested user_id → return it
    ├─ exists, mismatch                            → ValueError
    └─ does not exist                              → create, bound to user_id
```

`userId` is immutable once set. `/chat/v2` and `/chat/v3` translate that
`ValueError` into `400 {"error": "the sessionid or userid is invalid"}` rather
than letting it surface as a 500 — a distinguishable, parseable rejection
instead of an opaque failure.

Allowed: the same user across many sessions; a first message with no
`session_id`; an anonymous session where both sides are `None`. Blocked: any
request presenting a `session_id` bound to a different `user_id`, whether the
mismatch came from a stolen id, a swapped session, or a tampered token.

---

## Rate limiting and caller identity

The public demo is unauthenticated, so rate limiting is the only thing standing
between a script and the Gemini quota — and the box is a single 1-OCPU
instance, where a handful of concurrent embedding or PCA calls is enough to
make it unresponsive for everyone.

### Identifying the caller

Production runs behind two proxies:

```
visitor ──▶ Vercel edge ──▶ nginx (127.0.0.1) ──▶ gunicorn
```

Without `ProxyFix`, Werkzeug reports `127.0.0.1` as the peer for every request
and the entire internet shares one bucket — which turns the rate limit into a
self-inflicted denial of service the first time anyone runs a loop. The app
trusts exactly one hop, nginx, because that is the only address it can vouch
for; nginx overwrites the rightmost `X-Forwarded-For` entry with its actual
peer.

`rate_limit.client_key` then drops that entry and takes the rightmost of what
remains — the client IP the Vercel edge forwarded. If nothing remains, the peer
*was* the client (someone reaching the origin directly) and its address is
used.

**Known limitation.** A visitor who bypasses Vercel and hits the origin
directly can forge `X-Forwarded-For` and land in a bucket of their choosing.
Closing that requires an allowlist of Vercel's egress addresses, which Vercel
does not publish for Hobby projects. It is the reason for the next control.

### Shared budget

Every endpoint that spends money or CPU carries a *second* limit keyed to a
constant. Per-caller limits assume callers are distinguishable; this one
assumes nothing, so it is what actually bounds the worst case — a botnet, a
forged header, or simply the demo link doing better than expected.

### Per-route pricing

| Route | Per caller | Shared |
|---|---|---|
| `/api/demo/stats`, `/tenants` | 60/min | — |
| `/api/demo/chunks`, `/documents` | 30/min | — |
| `/api/demo/document/<file>` | 20/min | 400/hr |
| `/api/demo/chat` | 10/min, 100/hr | 500/hr |
| `/api/demo/compare` | 5/min, 40/hr | 200/hr |
| `/api/demo/projection` | 10/min | 300/hr |
| `/api/demo/projection/query` | 20/min | 600/hr |
| `/api/demo/eval` | 5/min, 30/hr | 150/hr |
| `/api/demo/upload` | 3/hr, 10/day | 60/hr |
| `/admin/register` | 5/hr | — |
| `/chat/v2`, `/chat/v3` | 30/min per project key | — |
| `/upload/document` | 20/hr per project key | — |

A blanket number would have been wrong in both directions: `/stats` is a single
indexed read, while `/upload` is a parse plus N embedding calls plus a GridFS
write, and `/compare` generates twice per request.

### Storage

Counters live in MongoDB (`RATELIMIT_STORAGE_URI`, defaulting to
`MONGODB_URI`). Gunicorn runs two workers; with in-process storage each keeps
its own counters, so every limit is silently doubled and which worker a request
lands on is arbitrary. Atlas is already a hard dependency, so this adds a
backend rather than a service.

Storage errors are swallowed rather than raised: a limiter that returns 500
when Atlas hiccups is worse than one that briefly stops limiting.

---

## Public demo scope

The demo API is unauthenticated on purpose — requiring a key to look at a demo
defeats the point. The controls are scope-based instead:

- Every route resolves its project through `_resolve_project`, which refuses
  anything not flagged `isDemo`. The authenticated tenant corpus is unreachable
  through these endpoints regardless of what id is supplied.
- `GET /api/demo/document/<project_id>/<file>` is keyed by project and
  filename, never by GridFS id. An id alone would let a visitor pull any file
  in the bucket, including another tenant's upload.
- Visitor uploads create *ephemeral* projects with an expiry; `_resolve_project`
  returns 410 past it, so a stale link cannot keep querying indefinitely.
- Documents are served with `Content-Disposition` chosen by type,
  `X-Frame-Options: SAMEORIGIN`, and `Cache-Control: private`.

---

## Transport and request limits

- **TLS** terminates at both hops. Let's Encrypt on the origin (auto-renewed by
  `certbot.timer` with a deploy hook that reloads nginx), and Vercel's own
  certificate at the edge. The Vercel → origin leg targets the origin's
  hostname over `https`, so it is encrypted rather than plaintext across the
  public internet.
- **`MAX_CONTENT_LENGTH = 25 MB`**, matched by `client_max_body_size 25M` in
  nginx. Note that an oversize upload arriving through the Vercel proxy
  surfaces as a 502, not a 413 — nginx rejects on `Content-Length` and closes
  mid-upload, and the router reports the dropped connection. The UI checks file
  size before sending so visitors get a real message.
- **`CORS_ORIGINS`** must be set on a public deployment. Defaulting to `*` lets
  any site spend the provider quota from its own visitors' browsers, which also
  spreads the calls across enough addresses to stay under the per-caller
  limits. Both deploy targets serve the UI same-origin, so the allowlist costs
  nothing.

---

## Secrets

`.env`, `*.pem`, `*.key`, `terraform/terraform.tfvars` and `terraform/*.tfstate*`
are gitignored — `tfstate` in particular because Terraform stores the values of
sensitive variables in it in plaintext.

Two secrets are required at boot and the app refuses to start without them:
`IMAGE_URL_SIGNING_SECRET` and `GEMINI_API_KEY`. `CREDENTIAL_ENCRYPTION_KEY` is
required before any integration credential can be read or written.

---

## What this does not defend against

Stated plainly, because a security document that only lists wins is not useful:

- **Forged `X-Forwarded-For` on the direct origin.** Covered above; mitigated by
  the shared budget, not eliminated.
- **Prompt injection through ingested documents.** Grounding constrains the
  model to retrieved context, but a document that *contains* adversarial
  instructions is retrieved context. There is no defense here beyond the
  per-tenant guardrail prompt.
- **Denial of service.** Rate limits bound cost and keep the box responsive
  under ordinary abuse; they are not DDoS protection.
- **Content of visitor uploads.** Files are parsed, not scanned. They land in an
  isolated ephemeral tenant and expire, which bounds the blast radius rather
  than preventing anything.
