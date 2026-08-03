import { useEffect, useRef, useState } from 'react';
import type { ChatResult, Tenant } from '../api';
import { api } from '../api';
import { Banner, Spinner, fmtCost, fmtMs } from './common';

export interface Turn {
  role: 'user' | 'ai';
  text: string;
  result?: ChatResult;
  error?: string;
  pending?: boolean;
}

export function Chat({ tenant, turns, setTurns, onResult, onActiveStage }: {
  tenant: Tenant | null;
  turns: Turn[];
  setTurns: React.Dispatch<React.SetStateAction<Turn[]>>;
  onResult: (r: ChatResult) => void;
  onActiveStage?: (stage: string | null) => void;
}) {
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [turns]);

  async function send(text: string) {
    const q = text.trim();
    if (!q || !tenant || busy) return;

    setInput('');
    setBusy(true);
    setTurns(t => [...t, { role: 'user', text: q }, { role: 'ai', text: '', pending: true }]);

    // Drive the architecture diagram: light each stage as the request moves
    // through it. Timings come back only after the round trip, so the walk is
    // paced optimistically and then settled by the real result.
    const walk = ['embed', 'search', 'rerank', 'generate'];
    let i = 0;
    onActiveStage?.(walk[0]);
    const timer = setInterval(() => {
      i += 1;
      onActiveStage?.(i < walk.length ? walk[i] : null);
      if (i >= walk.length) clearInterval(timer);
    }, 550);

    try {
      const result = await api.chat(q, tenant.projectId);
      setTurns(t => {
        const next = [...t];
        next[next.length - 1] = { role: 'ai', text: result.answer, result };
        return next;
      });
      onResult(result);
    } catch (e) {
      setTurns(t => {
        const next = [...t];
        next[next.length - 1] = { role: 'ai', text: '', error: (e as Error).message };
        return next;
      });
    } finally {
      clearInterval(timer);
      onActiveStage?.(null);
      setBusy(false);
    }
  }

  const samples = tenant?.demoSampleQuestions ?? [];

  return (
    <div className="pane">
      <div className="pane-head">
        <span className="pane-title">Chat</span>
        {tenant && <span className="pill mono">{tenant.projectName}</span>}
        <div style={{ flex: 1 }} />
        {turns.length > 0 && (
          <button className="btn btn-ghost btn-sm" onClick={() => setTurns([])}>Clear</button>
        )}
      </div>

      <div className="chat-scroll" ref={scrollRef}>
        {turns.length === 0 && (
          <div style={{ margin: 'auto', textAlign: 'center', maxWidth: 400 }}>
            <div style={{ fontSize: 30, marginBottom: 12, opacity: 0.55 }}>◆</div>
            <div style={{ color: 'var(--text-dim)', marginBottom: 6 }}>
              Ask anything about <strong style={{ color: 'var(--text)' }}>{tenant?.projectName ?? 'the knowledge base'}</strong>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>
              Every answer is grounded in the indexed documents. Watch the panel on the
              right to see exactly which chunks were retrieved and why.
            </div>
          </div>
        )}

        {turns.map((t, idx) => (
          <div key={idx} className={`msg ${t.role} fade-in`}>
            <div className={`avatar ${t.role}`}>{t.role === 'ai' ? 'AI' : 'YOU'}</div>
            <div style={{ minWidth: 0 }}>
              {t.pending ? (
                <div className="bubble" style={{ display: 'flex', alignItems: 'center', gap: 9, color: 'var(--text-dim)' }}>
                  <Spinner /> retrieving &amp; generating…
                </div>
              ) : t.error ? (
                <Banner kind="err">{t.error}</Banner>
              ) : (
                <>
                  <div className={`bubble${t.result?.refused ? ' refused' : ''}`}>
                    {t.text}
                    {t.result?.images.map(url => (
                      <img key={url} className="msg-img" src={url} alt="" loading="lazy" />
                    ))}
                  </div>

                  {t.result && (
                    <div className="metrics">
                      <span className="metric"><span className="k">total</span><span className="v">{fmtMs(t.result.timings.totalMs)}</span></span>
                      <span className="metric"><span className="k">gen</span><span className="v">{fmtMs(t.result.timings.generationMs)}</span></span>
                      <span className="metric"><span className="k">tok</span><span className="v">{t.result.usage?.total_tokens ?? '—'}</span></span>
                      <span className="metric"><span className="k">cost</span><span className="v">{fmtCost(t.result.usage?.cost_usd)}</span></span>
                      <span className="metric">
                        <span className="k">grounded</span>
                        <span className="v" style={{ color: t.result.refused ? 'var(--amber)' : 'var(--green)' }}>
                          {t.result.refused ? 'refused' : `${t.result.citations.length} src`}
                        </span>
                      </span>
                    </div>
                  )}

                  {t.result && t.result.citations.length > 0 && (
                    <div className="metrics">
                      {t.result.citations.map(c => (
                        <span key={c.chunkIndex} className="metric" title={c.text.slice(0, 300)}>
                          <span className="k">▸</span>
                          <span style={{ color: 'var(--text-dim)' }}>{c.topicName}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="composer">
        {turns.length === 0 && samples.length > 0 && (
          <div className="suggestions">
            {samples.map(s => (
              <button key={s} className="chip" onClick={() => send(s)} disabled={busy}>{s}</button>
            ))}
          </div>
        )}
        <div className="composer-row">
          <input
            className="input"
            placeholder={tenant ? 'Ask a question…' : 'Select a knowledge base first'}
            value={input}
            disabled={!tenant || busy}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input); } }}
          />
          <button className="btn btn-primary" onClick={() => send(input)} disabled={!tenant || busy || !input.trim()}>
            {busy ? <Spinner /> : 'Send'}
          </button>
        </div>
      </div>
    </div>
  );
}
