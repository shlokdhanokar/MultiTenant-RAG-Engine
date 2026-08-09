# RAG Engine — web UI

React + TypeScript + Vite front end for the multi-tenant RAG engine.

```bash
npm install
npm run dev      # http://localhost:5173, expects the API on :8000
npm run build    # type-check + production bundle into dist/
npm run lint
```

Seed the demo tenants first (`python scripts/seed_demo.py` from the repo root),
or it opens with nothing to query.

## Structure

The app opens directly on the workspace — there is no landing screen. Anything
a first-time visitor needs is reachable from there: the knowledge-base list
carries an **Add your own knowledge base** entry that routes to Upload, and
**Run demo** in the toolbar selects the flagship tenant, asks one of its own
sample questions, and runs the pipeline in one click.

| File | Role |
|---|---|
| `App.tsx` | Tab routing, tenant selection, and the demo trigger |
| `api.ts` | The entire API surface, typed; nothing else calls `fetch` |
| `components/Chat.tsx` | Conversation, and the stage walk that drives the pipeline diagram |
| `components/Inspector.tsx` | Retrieval trace: candidates, scores, timings, cost |
| `components/Compare.tsx` | Grounded vs. ungrounded, side by side |
| `components/Chunks.tsx` | Chunk boundaries and anchored images |
| `components/VectorSpace.tsx` | 2-D projection of the tenant's embeddings |
| `components/Evaluation.tsx` | Hit@1 / Hit@3 / MRR against the stored eval set |
| `components/DocumentPreview.tsx` | The original file, streamed from GridFS |
| `components/Upload.tsx` | Visitor upload into an ephemeral tenant |

`Chat` is driven from outside by an `autoSend` prop carrying a nonce. The tenant
switch and the question arrive in the same commit, so the effect waits for a
tenant to exist and records which nonce it has already sent — without that, a
later tenant switch would replay the last demo question.

## API base

`src/api.ts` resolves its base URL as:

```ts
import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? 'http://localhost:8000' : '')
```

Dev needs an absolute base because the Vite server and Flask run on different
ports. A production build defaults to same-origin relative paths, because both
deploy targets put the API under the UI's own origin. Set `VITE_API_BASE` only
for a genuinely cross-origin deployment.

## Deployment

Two targets serve the same bundle, both same-origin:

**nginx on the Oracle box** — `dist/` is copied to `/var/www/rag-ui` and served
with a SPA fallback; `/api/`, `/chat/`, `/upload/`, `/admin/`, `/image/` and
`/health` are proxied to gunicorn on `127.0.0.1:8000`.

**Vercel** — `vercel.json` builds the bundle and rewrites those same paths to
the Oracle instance. The proxy hop is server-side, so the browser only ever
talks to the `vercel.app` origin. That matters twice over: the browser never
resolves `duckdns.org` (dynamic-DNS names are blocked by filters like
FortiGuard, which is what makes the origin unreachable on some networks), and
an `http` origin behind an `https` page would be refused as mixed content.

The rewrite destination is the duckdns hostname over `https`, not the raw IP.
nginx only has a server block for that name, so the bare IP 404s every path,
and the Let's Encrypt certificate is issued for the name — `https` to the IP
would fail validation. Vercel resolves the name from its own network, which is
not behind the DNS filter, so the hop stays encrypted end to end.

```bash
npx vercel deploy --prod
```
