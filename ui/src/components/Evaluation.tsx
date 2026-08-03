import { useState } from 'react';
import type { EvalMetrics, EvalResult, Tenant } from '../api';
import { api } from '../api';
import { Banner, Empty, Spinner } from './common';

/**
 * Eval dashboard: runs a stored question set against live retrieval and reports
 * Hit@1 / Hit@3 / MRR for vector-only versus hybrid ranking.
 */
export function Evaluation({ tenant }: { tenant: Tenant | null }) {
  const [result, setResult] = useState<EvalResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (!tenant) return;
    setBusy(true); setError(null); setResult(null);
    try {
      setResult(await api.evaluate(tenant.projectId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!tenant) return <Empty title="Select a knowledge base" />;

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 15 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 600, marginBottom: 5 }}>Retrieval evaluation</h2>
        <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>
          A fixed set of questions with known correct sources, run against live retrieval.
          <strong style={{ color: 'var(--text)' }}> Hit@1</strong> is how often the right chunk ranks first;
          <strong style={{ color: 'var(--text)' }}> MRR</strong> rewards ranking it near the top.
          Measuring this is what makes a retrieval change verifiable instead of a guess.
        </p>
      </div>

      <div>
        <button className="btn btn-primary" onClick={run} disabled={busy}>
          {busy ? <><Spinner /> Running…</> : 'Run evaluation'}
        </button>
      </div>

      {error && <Banner kind="err">{error}</Banner>}

      {result && !result.available && <Banner kind="warn">{result.message}</Banner>}

      {result?.available && result.vectorOnly && result.hybrid && (
        <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: 15 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 11 }}>
            <MetricCard title="Vector only" m={result.vectorOnly} />
            <MetricCard title="Hybrid re-rank" m={result.hybrid} accent />
          </div>

          <Verdict a={result.vectorOnly} b={result.hybrid} />

          <div>
            <div className="section-label">Per-question results</div>
            <div className="card scroll-x">
              <table className="tbl">
                <thead>
                  <tr><th /><th>Question</th><th>Expected source</th><th>Vector</th><th>Hybrid</th></tr>
                </thead>
                <tbody>
                  {result.cases!.map((c, i) => (
                    <tr key={i}>
                      <td style={{ color: c.hit ? 'var(--green)' : 'var(--amber)' }}>{c.hit ? '✓' : '!'}</td>
                      <td>{c.query}</td>
                      <td style={{ color: 'var(--text-faint)', fontSize: 11.5 }}>{c.expectedTopic}</td>
                      <td className="mono" style={{ color: 'var(--text-dim)' }}>{c.vectorRank ?? '—'}</td>
                      <td className="mono" style={{ color: c.hybridRank === 1 ? 'var(--green)' : 'var(--text-dim)' }}>
                        {c.hybridRank ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function MetricCard({ title, m, accent }: { title: string; m: EvalMetrics; accent?: boolean }) {
  const pct = m.total ? Math.round((m.hit1 / m.total) * 100) : 0;
  return (
    <div className="card card-pad" style={accent ? { borderColor: 'var(--accent-dim)' } : undefined}>
      <div className="section-label" style={accent ? { color: 'var(--accent)' } : undefined}>{title}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
        <span className="mono" style={{ fontSize: 26, fontWeight: 600, color: accent ? 'var(--accent)' : 'var(--text)' }}>
          {pct}%
        </span>
        <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>Hit@1</span>
      </div>
      <div className="metrics" style={{ marginTop: 8 }}>
        <span className="metric"><span className="k">hit@1</span><span className="v">{m.hit1}/{m.total}</span></span>
        <span className="metric"><span className="k">hit@3</span><span className="v">{m.hit3}/{m.total}</span></span>
        <span className="metric"><span className="k">mrr</span><span className="v">{m.mrr.toFixed(3)}</span></span>
      </div>
    </div>
  );
}

/**
 * States plainly what the comparison shows, including when it shows nothing —
 * a dashboard that only ever claims improvement isn't a measurement.
 */
function Verdict({ a, b }: { a: EvalMetrics; b: EvalMetrics }) {
  const delta = b.hit1 - a.hit1;
  if (delta > 0) {
    return <Banner kind="info">Hybrid re-ranking recovered {delta} question{delta > 1 ? 's' : ''} that pure vector search ranked below first place.</Banner>;
  }
  if (delta < 0) {
    return <Banner kind="warn">Hybrid re-ranking performed worse than pure vector search on this set by {-delta} question{-delta > 1 ? 's' : ''} — the keyword weight is likely too high for this corpus.</Banner>;
  }
  return (
    <Banner kind="info">
      Both strategies score identically here. This knowledge base has few, clearly-separated
      topics, so dense similarity alone is already sufficient — the lexical signal earns its
      keep on larger corpora with overlapping content and exact identifiers.
    </Banner>
  );
}
