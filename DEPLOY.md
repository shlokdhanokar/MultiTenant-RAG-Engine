# Deploying

Two pieces deploy separately: the **API** (Docker container) and the **UI**
(static bundle). The UI is compiled against the API's URL at build time, so the
API has to exist first.

Everything below has been verified locally: the image builds, the container
boots under Gunicorn, and a grounded chat returns through it in ~1.2s.

---

## Prerequisites

| Thing | Where | Notes |
|---|---|---|
| MongoDB Atlas cluster | cloud.mongodb.com | Free M0 is enough |
| Atlas Vector Search index | Atlas UI | Definition below — **the app returns nothing without it** |
| Groq API key | console.groq.com/keys | Free, no card |
| Gemini API key | aistudio.google.com/apikey | Free. Required even on Groq — embeddings are Gemini-only |

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
| `GROQ_API_KEY` | from console.groq.com |
| `GEMINI_API_KEY` | from aistudio.google.com |
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

| Setting | Value |
|---|---|
| Root directory | `ui` |
| Build command | `npm run build` |
| Output directory | `dist` |
| Environment variable | `VITE_API_BASE=https://<your-service>.onrender.com` |

`VITE_API_BASE` is compiled into the bundle at build time, not read at runtime —
changing it later requires a rebuild, not just a restart.

Optionally set `CORS_ORIGINS` on the API to the UI's origin; it defaults to `*`,
which is fine for a public demo but wider than it needs to be.

---

## Free-tier behaviour worth knowing

Render's free plan stops the instance after ~15 minutes idle. The next visitor
waits for a container boot **and** provider warmup — roughly 20–30 seconds — and
that visitor is usually the person you sent the link to. Warm responses are
~1.2s. If the link is being actively shared, the `starter` plan removes the
sleep entirely; change `plan: free` in `render.yaml`.

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
| Images 404 in the deployed UI | `APP_BASE_URL` not set to the service's public URL |
| Browser console CORS errors | `VITE_API_BASE` doesn't match the API's actual origin |
| First request ~25s, later ones fast | Free-tier cold start — expected |
| `ServerSelectionTimeoutError` in logs | Atlas Network Access doesn't allow the host |
| 429 from Groq | Free tier is ~1,000 requests/day |
