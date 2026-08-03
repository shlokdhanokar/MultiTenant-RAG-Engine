import type { ChatResult } from '../api';
import { Empty, Pane, ScoreBar, Stat, fmtCost, fmtMs } from './common';
import { ArchDiagram } from './ArchDiagram';

/**
 * The right-hand pane: everything the engine did to produce the last answer.
 * Candidates are shown in re-ranked order with all three scores, so a promotion
 * or demotion caused by lexical overlap is visible rather than implied.
 */
export function Inspector({ result, activeStage }: { result: ChatResult | null; activeStage: string | null }) {
  return (
    <Pane title="Retrieval Inspector">
      <div style={{ padding: 14, borderBottom: '1px solid var(--border)' }}>
        <ArchDiagram active={activeStage} timings={result?.timings ?? null} />
      </div>

      {!result ? (
        <Empty
          icon="⌁"
          title="No query yet"
          hint="Ask a question and this panel will show the candidate chunks, their vector and keyword scores, the blended ranking, and where the time and tokens went."
        />
      ) : (
        <div style={{ padding: 14 }}>
          <div className="section-label">Pipeline latency</div>
          <div className="stat-grid" style={{ marginBottom: 16 }}>
            <Stat label="Embed" value={fmtMs(result.timings.embedQueryMs)} />
            <Stat label="Vector search" value={fmtMs(result.timings.vectorSearchMs)} />
            <Stat label="Re-rank" value={fmtMs(result.timings.rerankMs)} />
            <Stat label="Generate" value={fmtMs(result.timings.generationMs)} />
            <Stat label="Total" value={fmtMs(result.timings.totalMs)} color="var(--accent)" />
            <Stat
              label="Cost"
              value={fmtCost(result.usage?.cost_usd)}
              sub={result.usage ? `${result.usage.input_tokens} in / ${result.usage.output_tokens} out` : undefined}
              color="var(--green)"
            />
          </div>

          <div className="section-label">
            Candidates — {result.candidates.length} retrieved, top {result.candidates.filter(c => c.selected).length} sent to the model
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {result.candidates.map((c, i) => (
              <div key={`${c.chunkIndex}-${i}`} className={`cand${c.selected ? ' selected' : ''} fade-in`}>
                <div className="cand-head">
                  <span className="rank">{i + 1}</span>
                  <span className="cand-title">{c.topicName}</span>
                  {c.selected && (
                    <span className="pill" style={{ borderColor: 'var(--accent-dim)', color: 'var(--accent)' }}>used</span>
                  )}
                </div>
                <div className="cand-preview">{c.preview}…</div>
                <ScoreBar label="vec" value={c.vectorScore} kind="vec" />
                <ScoreBar label="kw" value={c.keywordScore} kind="kw" />
                <ScoreBar label="final" value={c.rerankScore} kind="total" />
              </div>
            ))}
          </div>

          <RankShift result={result} />
        </div>
      )}
    </Pane>
  );
}

/**
 * Makes the re-ranker's effect legible: if the hybrid blend moved a chunk
 * relative to pure vector similarity, say so explicitly. Silence here is
 * meaningful too — it means the lexical signal didn't change the outcome.
 */
function RankShift({ result }: { result: ChatResult }) {
  const moves = result.candidates
    .map((c, hybridIdx) => {
      const vecIdx = result.vectorOnlyOrder.indexOf(c.topicName);
      return vecIdx >= 0 && vecIdx !== hybridIdx ? { name: c.topicName, from: vecIdx + 1, to: hybridIdx + 1 } : null;
    })
    .filter(Boolean) as { name: string; from: number; to: number }[];

  return (
    <div style={{ marginTop: 16 }}>
      <div className="section-label">Hybrid re-rank effect</div>
      {moves.length === 0 ? (
        <div className="card card-pad" style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>
          Keyword blending did not change the ordering for this query — dense
          similarity alone already ranked these chunks the same way.
        </div>
      ) : (
        <div className="card card-pad" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {moves.map(m => (
            <div key={m.name} style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 12.5 }}>
              <span className="mono" style={{ color: m.to < m.from ? 'var(--green)' : 'var(--amber)', fontSize: 11 }}>
                {m.to < m.from ? '▲' : '▼'} #{m.from}→#{m.to}
              </span>
              <span style={{ color: 'var(--text-dim)' }}>{m.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
