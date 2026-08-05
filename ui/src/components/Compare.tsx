import { useState } from 'react';
import type { CompareResult, Tenant } from '../api';
import { api } from '../api';
import { Banner, Empty, Spinner, fmtCost, fmtMs } from './common';

/**
 * Feature 14 + 6: the same question answered with and without retrieval.
 *
 * The ungrounded side is the honest failure mode of a bare LLM on private data —
 * fluent, confident, and unverifiable. Placing it beside the grounded answer is
 * the clearest single argument for the whole system.
 */
export function Compare({ tenant }: { tenant: Tenant | null }) {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<CompareResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(q: string) {
    const text = q.trim();
    if (!text || !tenant) return;
    setBusy(true); setError(null); setResult(null);
    try {
      setResult(await api.compare(text, tenant.projectId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!tenant) return <Empty title="Select a knowledge base" />;

  const samples = tenant.demoSampleQuestions ?? [];
  const outOfScope = [
    'What is the capital of France?',
    'Who won the 2022 football World Cup?',
    'What are your prices for next summer?',
  ];

  return (
    <div style={{ maxWidth: 940, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 15 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 600, marginBottom: 5 }}>Grounded vs. ungrounded</h2>
        <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>
          The same question, answered twice by the same model — once from its own training
          data, once from retrieved passages under a strict grounding prompt. Try a question
          the documents <em>can't</em> answer to see the difference that matters.
        </p>
      </div>

      <div className="composer-row">
        <input
          className="input"
          placeholder="Ask something…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') run(query); }}
        />
        <button className="btn btn-primary" onClick={() => run(query)} disabled={busy || !query.trim()}>
          {busy ? <Spinner /> : 'Compare'}
        </button>
      </div>

      <div>
        <div className="section-label">In the documents</div>
        <div className="suggestions">
          {samples.map(s => (
            <button key={s} className="chip" onClick={() => { setQuery(s); run(s); }} disabled={busy}>{s}</button>
          ))}
        </div>
        <div className="section-label" style={{ marginTop: 11 }}>Not in the documents — the interesting case</div>
        <div className="suggestions">
          {outOfScope.map(s => (
            <button key={s} className="chip" style={{ borderColor: 'rgba(251,191,36,0.35)' }}
                    onClick={() => { setQuery(s); run(s); }} disabled={busy}>{s}</button>
          ))}
        </div>
      </div>

      {error && <Banner kind="err">{error}</Banner>}

      {result && (
        <div className="fade-in" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <Side
            title="Without RAG"
            subtitle="model's own knowledge"
            tone="warn"
            answer={result.ungrounded.answer}
            latency={result.ungrounded.latencyMs}
            cost={result.ungrounded.usage?.cost_usd}
            sources={[]}
            note="No sources — nothing here can be checked against your documents."
          />
          <Side
            title="With RAG"
            subtitle="grounded in retrieved passages"
            tone="ok"
            answer={result.grounded.answer}
            latency={result.grounded.latencyMs}
            cost={result.grounded.usage?.cost_usd}
            sources={result.grounded.sources}
            note={result.grounded.sources.length ? undefined : 'No relevant passage was found, so the model declined to answer.'}
          />
        </div>
      )}
    </div>
  );
}

function Side({ title, subtitle, tone, answer, latency, cost, sources, note }: {
  title: string; subtitle: string; tone: 'ok' | 'warn';
  answer: string; latency: number; cost?: number; sources: string[]; note?: string;
}) {
  const color = tone === 'ok' ? 'var(--green)' : 'var(--amber)';
  return (
    <div className="card card-pad" style={{ borderColor: tone === 'ok' ? 'rgba(52,211,153,0.3)' : 'rgba(251,191,36,0.3)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: color }} />
        <span style={{ fontSize: 13, fontWeight: 600, color }}>{title}</span>
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginBottom: 11 }}>{subtitle}</div>

      <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{answer}</div>

      <div className="metrics">
        <span className="metric"><span className="k">latency</span><span className="v">{fmtMs(latency)}</span></span>
        <span className="metric"><span className="k">cost</span><span className="v">{fmtCost(cost)}</span></span>
        <span className="metric">
          <span className="k">sources</span>
          <span className="v" style={{ color }}>{sources.length || 'none'}</span>
        </span>
      </div>

      {sources.length > 0 && (
        <div className="metrics">
          {sources.map(s => (
            <span key={s} className="metric"><span className="k">▸</span><span style={{ color: 'var(--text-dim)' }}>{s}</span></span>
          ))}
        </div>
      )}

      {note && <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 9 }}>{note}</div>}
    </div>
  );
}
