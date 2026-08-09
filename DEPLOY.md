# Deploying

Three topologies, in increasing order of how much of it you own:

| | API | UI | Notes |
|---|---|---|---|
| **[§1](#1-deploy-the-api-render) + [§2](#2-deploy-the-ui-vercel-or-netlify)** | Render (Docker) | Vercel/Netlify, cross-origin | Managed, but the free plan sleeps |
| **[terraform/](terraform/README.md)** | Oracle Always Free VM | same nginx origin | Free, no cold starts, you run the box |
| **[§3](#3-putting-vercel-in-front-of-a-self-hosted-origin)** | Oracle VM | Vercel, proxied to the VM | What the live demo runs |

The live demo serves the UI from <https://multi-tenant-rag-engine.vercel.app>,
which reverse-proxies the API to an Oracle instance at
<https://multi-tenant-rag.duckdns.org>. Prefer that route unless you
specifically want managed hosting — it is free, has no cold starts, and the
whole box is reproducible from `terraform/deploy.sh`.

> The §1 + §2 route is documented as written and its config files
> (`Dockerfile`, `render.yaml`) are in the repo, but the live deployment does
> not exercise it, so treat it as a recipe rather than a tested path.

---

## Prerequisites

| Thing | Where | Notes |
|---|---|---|
| MongoDB Atlas cluster | cloud.mongodb.com | Free M0 is enough |
| Atlas Vector Search index | Atlas UI | Definition below — **the app returns nothing without it** |
| Gemini API key | aistudio.google.com/apikey | Free. Required always — embeddings are Gemini-only, and it generates too on the default `LLM_PROVIDER=gemini` |
| Groq / OpenAI key | console.groq.com/keys · platform.openai.com | Optional, only if you switch `LLM_PROVIDER` |

### Atlas Vector Search index

On the `chunks` collection, create a **Vector Search** index named
`vector_index`:

```json
{
  "fields": [
    { "type": "vector", "path": "embedding", "numDimensions": 1536, "similarity": "cosine" },
    { "type": "filter", "path": "knowledge_base_id" }
  ]
}
```

`numDimensions` must equal `GEMINI_EMBEDDING_DIMENSIONS`. The `filter` entry is
what scopes search to one tenant — without it, retrieval silently leaks across
tenants and small knowledge bases return nothing.

### Atlas network access

Add `0.0.0.0/0` under Network Access, or the static egress IPs of your host.
A cluster that only allows your laptop's IP will refuse the deployed container.

---

## 1. Deploy the API (Render)

1. **New → Web Service**, connect the GitHub repo.
2. Render reads `render.yaml` automatically: Docker runtime, health check on
   `/health`, free plan.
3. Set the secrets Render marks as required (`sync: false`):

| Variable | Value |
|---|---|
| `MONGODB_URI` | `mongodb+srv://…` |
| `MONGODB_DB_NAME` | `rag_db` |
| `GEMINI_API_KEY` | from aistudio.google.com |
| `LLM_PROVIDER` | `gemini` (default) |
| `CREDENTIAL_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `IMAGE_URL_SIGNING_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `APP_BASE_URL` | **the service's own https URL** (see below) |

4. **`APP_BASE_URL` is the one that bites.** Image links are HMAC-signed
   against it, so if it keeps the `http://localhost:8000` default every image
   in the demo 404s for everyone but you. Render only tells you the URL after
   the first deploy — so deploy once, copy the URL, set `APP_BASE_URL` to it,
   and redeploy.

5. Confirm: `curl https://<your-service>.onrender.com/health` → `{"status":"ok"}`

### Seed the demo knowledge bases

The demo tenants live in MongoDB, not in the image, so seed them from your
machine against the same cluster:

```bash
python scripts/seed_demo.py
```

Verify: `curl https://<your-service>.onrender.com/api/demo/tenants`

---

## 2. Deploy the UI (Vercel or Netlify)

This pairs with §1: the UI sits on a different origin from the API and is
compiled to point at it. (§3 does the opposite — same origin, no build-time
URL — and the two should not be mixed.)

| Setting | Value |
|---|---|
| Root directory | `ui` |
| Build command | `npm run build` |
| Output directory | `dist` |
| Environment variable | `VITE_API_BASE=https://<your-service>.onrender.com` |

`VITE_API_BASE` is compiled into the bundle at build time, not read at runtime —
changing it later requires a rebuild, not just a restart.

Set `CORS_ORIGINS` on the API to the UI's origin. It is genuinely needed here,
since the two are cross-origin — and leaving the `*` default on a public
deployment lets any site spend your provider quota from its own visitors'
browsers, spread across enough addresses to stay under the per-caller rate
limits.

---

## 3. Putting Vercel in front of a self-hosted origin

This is what the live demo does, and it is a different shape from §2: instead of
compiling the origin's URL into the bundle, Vercel serves the bundle *and*
reverse-proxies the API paths back to the origin. The browser only ever talks to
`*.vercel.app`.

Two problems it solves at once. The origin's hostname is a DuckDNS name, and
network filters such as FortiGuard block dynamic-DNS domains wholesale — the
site fails to load on exactly the corporate and campus networks it gets demoed
on. And an `http` origin behind an `https` page is refused as mixed content.
Moving name resolution and TLS termination to Vercel's edge removes both.

`ui/vercel.json` holds the whole configuration:

```json
{
  "rewrites": [
    { "source": "/api/:path*",   "destination": "https://your-origin.example.org/api/:path*" },
    { "source": "/health",       "destination": "https://your-origin.example.org/health" },
    { "source": "/image/:path*", "destination": "https://your-origin.example.org/image/:path*" }
  ]
}
```

```bash
cd ui && npx vercel deploy --prod
```

Points worth knowing before you copy it:

- **Leave `VITE_API_BASE` unset.** A production build already falls back to
  same-origin relative paths, which is what makes the rewrites apply.
- **Use a hostname, not the origin's raw IP.** nginx matches on `server_name`,
  so the bare IP 404s every path, and the TLS certificate is issued for the name
  — `https` to an IP fails validation.
- **`APP_BASE_URL` stays the origin's own URL.** The demo API's image links are
  host-relative so they ride the proxy; only the WhatsApp payload signs absolute
  URLs, because Meta's servers fetch those directly.
- **New projects have Vercel Authentication on.** Every URL 302s to an SSO login
  until you turn it off: `npx vercel project protection disable --sso`.
- **There is no 4.5 MB request limit on rewrites to an external destination.**
  That cap belongs to Vercel Functions, which a static site with rewrites never
  invokes; uploads up to nginx's `client_max_body_size` (25 MB here) pass
  through. What Vercel *does* impose is a 120s proxied-request timeout.
- **An oversize upload surfaces as a bare 502**, not a 413: nginx rejects on
  `Content-Length` and closes mid-upload, and the router reports the dropped
  upstream connection rather than the status. The UI checks file size before
  sending so visitors get a real message.

---

## Free-tier behaviour worth knowing

**Render** stops the instance after ~15 minutes idle. The next visitor waits for
a container boot **and** provider warmup — roughly 20–30 seconds — and that
visitor is usually the person you sent the link to. If the link is being
actively shared, the `starter` plan removes the sleep; change `plan: free` in
`render.yaml`. This is the reason the live demo moved to Oracle.

**Oracle Always Free** does not sleep, but `VM.Standard.E2.1.Micro` is 1 OCPU
and 1 GB. `deploy.sh` adds 4 GB of swap because `pip install`, OCR and the Vite
build all exceed 1 GB, and runs Gunicorn with two workers recycled every ~200
requests to bound what PyMuPDF leaks on large PDFs. The public IP is
*ephemeral* — it can change across a stop/start, which is what the DuckDNS cron
exists to survive. Keep the account active by logging in every 30 days.

**Vercel Hobby** gives 100 GB of fast data transfer and up to 10 GB of fast
origin transfer per month, and a 120-second proxied-request timeout on all
plans. New projects have Vercel Authentication enabled, which 302s every URL to
an SSO login until you disable it.

**Gemini's free tier** is roughly 250k tokens/minute. One RAG call spends ~9k on
retrieved context, which is why `LLM_PROVIDER` defaults to `gemini` rather than
Groq — Groq's 12k/minute rate-limits after about one query every 45 seconds.

---

## Running it locally

```bash
pip install -r requirements.txt
cp .env.example .env      # fill in the keys
python scripts/seed_demo.py
python server.py          # http://localhost:8000

cd ui && npm install && npm run dev   # http://localhost:5173
```

Or the container as it actually ships:

```bash
docker build -t rag-engine .
docker run --rm --env-file .env -p 8000:8000 rag-engine
```

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Chat returns "couldn't find any information" | Atlas index missing, misnamed, or `numDimensions` ≠ `GEMINI_EMBEDDING_DIMENSIONS` |
| Images 404 in the deployed UI | `APP_BASE_URL` not set to the service's public URL (§1/§2 only — §3 serves them host-relative) |
| Browser console CORS errors | `VITE_API_BASE` doesn't match the API's actual origin, or `CORS_ORIGINS` doesn't include it |
| First request ~25s, later ones fast | Free-tier cold start — expected |
| `ServerSelectionTimeoutError` in logs | Atlas Network Access doesn't allow the host |
| One visitor's requests 429 everyone else | No `ProxyFix`, so every request looks like it comes from the proxy and shares one bucket. See [SECURITY_IMPLEMENTATION.md](SECURITY_IMPLEMENTATION.md#rate-limiting-and-caller-identity) |
| Rate limits appear to be double what you configured | Counters in process memory with more than one Gunicorn worker — set `RATELIMIT_STORAGE_URI` |
| Large upload fails with a bare 502 | Over `client_max_body_size`; nginx closes mid-upload and the proxy reports the dropped connection rather than a 413 |
| Every URL redirects to a Vercel login | Deployment Protection on a new project — `npx vercel project protection disable --sso` |
| The pretty `*.vercel.app` URL still serves the old build | It is an alias, not a project domain, so it does not follow new production deploys until it is registered |
| 429 during a demo | You are on `LLM_PROVIDER=groq`: its free tier is 12k tokens/min and one RAG call costs ~9k. Switch to `gemini` (~250k/min). |
| 429 `insufficient_quota` from OpenAI | The key is valid but has no billing credits — OpenAI has no free generation tier. |
