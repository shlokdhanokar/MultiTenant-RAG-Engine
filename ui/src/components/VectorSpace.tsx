import { useEffect, useState } from 'react';
import type { ProjectionPoint, Tenant } from '../api';
import { api } from '../api';
import { Banner, Empty, Spinner } from './common';

const PAD = 42;
const SIZE = 460;

/** Maps normalized [-1,1] coordinates into the SVG viewport. */
const toXY = (x: number, y: number) => ({
  cx: PAD + ((x + 1) / 2) * (SIZE - PAD * 2),
  cy: PAD + ((1 - y) / 2) * (SIZE - PAD * 2),
});

/**
 * Renders the knowledge base's embedding space in 2D via PCA.
 *
 * PCA rather than t-SNE/UMAP because it's a fixed linear projection: a query
 * embedded afterwards can be mapped through the *same* basis, so "my question
 * landed next to these chunks" is a real geometric statement rather than an
 * artifact of re-fitting the layout.
 */
export function VectorSpace({ tenant }: { tenant: Tenant | null }) {
  const [points, setPoints] = useState<ProjectionPoint[] | null>(null);
  const [dims, setDims] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<ProjectionPoint | null>(null);

  const [query, setQuery] = useState('');
  const [queryPt, setQueryPt] = useState<{ x: number; y: number; query: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!tenant) return;
    setPoints(null); setError(null); setQueryPt(null);
    api.projection(tenant.projectId)
      .then(r => { setPoints(r.points); setDims(r.dimensions); })
      .catch(e => setError((e as Error).message));
  }, [tenant?.projectId]);

  async function plotQuery() {
    if (!tenant || !query.trim()) return;
    setBusy(true);
    try {
      setQueryPt(await api.projectQuery(tenant.projectId, query.trim()));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!tenant) return <Empty title="Select a knowledge base" />;
  if (error) return <div style={{ padding: 16 }}><Banner kind="err">{error}</Banner></div>;
  if (!points) return <div className="empty"><Spinner /></div>;
  if (points.length < 2) return <Empty title="Not enough chunks to project a vector space" />;

  // Nearest chunks to the query, by 2D distance — an approximation of the
  // true high-dimensional ranking, shown only as a visual aid.
  const nearest = queryPt
    ? [...points]
        .map(p => ({ p, d: Math.hypot(p.x - queryPt.x, p.y - queryPt.y) }))
        .sort((a, b) => a.d - b.d)
        .slice(0, 3)
    : [];

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 15 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 600, marginBottom: 5 }}>Embedding space</h2>
        <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>
          Each chunk is a {dims}-dimensional vector. This is a PCA projection down to two
          dimensions — chunks about similar things sit near each other. Plot a query to see
          where it lands relative to the content.
        </p>
      </div>

      <div className="composer-row">
        <input
          className="input"
          placeholder="Type a question to plot it in this space…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') plotQuery(); }}
        />
        <button className="btn btn-primary" onClick={plotQuery} disabled={busy || !query.trim()}>
          {busy ? <Spinner /> : 'Plot'}
        </button>
      </div>

      <div className="card" style={{ padding: 12, display: 'flex', justifyContent: 'center' }}>
        <svg viewBox={`0 0 ${SIZE} ${SIZE}`} style={{ width: '100%', maxWidth: SIZE, height: 'auto' }}>
          <defs>
            <radialGradient id="qGlow">
              <stop offset="0%"   stopColor="var(--amber)" stopOpacity="0.55" />
              <stop offset="100%" stopColor="var(--amber)" stopOpacity="0" />
            </radialGradient>
          </defs>

          {[0.25, 0.5, 0.75].map(f => (
            <g key={f} stroke="var(--border)" strokeWidth="1">
              <line x1={PAD + f * (SIZE - PAD * 2)} y1={PAD} x2={PAD + f * (SIZE - PAD * 2)} y2={SIZE - PAD} />
              <line x1={PAD} y1={PAD + f * (SIZE - PAD * 2)} x2={SIZE - PAD} y2={PAD + f * (SIZE - PAD * 2)} />
            </g>
          ))}
          <rect x={PAD} y={PAD} width={SIZE - PAD * 2} height={SIZE - PAD * 2}
                fill="none" stroke="var(--border-bright)" strokeWidth="1" />

          {queryPt && nearest.map(({ p }) => {
            const a = toXY(queryPt.x, queryPt.y);
            const b = toXY(p.x, p.y);
            return <line key={p.chunkIndex} x1={a.cx} y1={a.cy} x2={b.cx} y2={b.cy}
                         stroke="var(--amber)" strokeWidth="1" strokeDasharray="3 3" opacity="0.5" />;
          })}

          {points.map(p => {
            const { cx, cy } = toXY(p.x, p.y);
            const isNear = nearest.some(n => n.p.chunkIndex === p.chunkIndex);
            const active = hover?.chunkIndex === p.chunkIndex;
            return (
              <g key={p.chunkIndex}
                 onMouseEnter={() => setHover(p)}
                 onMouseLeave={() => setHover(null)}
                 style={{ cursor: 'pointer' }}>
                <circle cx={cx} cy={cy} r={active ? 9 : 6}
                        fill={isNear ? 'var(--green)' : 'var(--accent)'}
                        opacity={active ? 1 : 0.82}
                        style={{ transition: 'r 0.15s' }} />
                <circle cx={cx} cy={cy} r={13} fill="transparent" />
                <text x={cx} y={cy - 13} textAnchor="middle"
                      style={{ fill: 'var(--text-faint)', fontSize: 9, fontFamily: 'var(--mono)' }}>
                  {p.chunkIndex}
                </text>
              </g>
            );
          })}

          {queryPt && (() => {
            const { cx, cy } = toXY(queryPt.x, queryPt.y);
            return (
              <g>
                <circle cx={cx} cy={cy} r={26} fill="url(#qGlow)" />
                <path d={`M${cx - 7},${cy} L${cx + 7},${cy} M${cx},${cy - 7} L${cx},${cy + 7}`}
                      stroke="var(--amber)" strokeWidth="2" strokeLinecap="round" />
                <text x={cx} y={cy + 22} textAnchor="middle"
                      style={{ fill: 'var(--amber)', fontSize: 9.5, fontFamily: 'var(--mono)' }}>
                  QUERY
                </text>
              </g>
            );
          })()}
        </svg>
      </div>

      <div className="card card-pad" style={{ minHeight: 62 }}>
        {hover ? (
          <div className="fade-in">
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 3 }}>
              <span className="mono" style={{ color: 'var(--accent)', marginRight: 7 }}>#{hover.chunkIndex}</span>
              {hover.topicName}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>{hover.preview}…</div>
          </div>
        ) : queryPt ? (
          <div className="fade-in">
            <div className="section-label">Nearest chunks to “{queryPt.query}”</div>
            {nearest.map(({ p, d }) => (
              <div key={p.chunkIndex} style={{ display: 'flex', gap: 9, fontSize: 12.5, marginTop: 3 }}>
                <span className="mono" style={{ color: 'var(--green)' }}>{d.toFixed(3)}</span>
                <span style={{ color: 'var(--text-dim)' }}>{p.topicName}</span>
              </div>
            ))}
            <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 8 }}>
              Distances are measured in the projected 2D space, so they approximate — but do
              not exactly reproduce — the {dims}-dimensional ranking used for real retrieval.
            </div>
          </div>
        ) : (
          <div style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>
            Hover a point to inspect its chunk, or plot a query above.
          </div>
        )}
      </div>
    </div>
  );
}
