import type { Stats, Tenant } from '../api';
import { Spinner } from './common';

const ICONS: Record<string, string> = { palm: '🌴', cross: '✚', file: '📄' };

/**
 * Entry screen. The app previously opened straight into a six-tab workspace,
 * which asks a first-time visitor to understand the whole pipeline before they
 * can try anything. This puts the two actual starting points up front — try a
 * prepared knowledge base, or bring your own document — and gets out of the way
 * once one is chosen.
 */
export function Landing({ tenants, stats, onPickTenant, onPreviewTenant, onUpload }: {
  tenants: Tenant[];
  stats: Stats | null;
  onPickTenant: (projectId: string) => void;
  onPreviewTenant: (projectId: string) => void;
  onUpload: () => void;
}) {
  return (
    <div style={{ height: '100%', overflowY: 'auto' }}>
      <div style={{ maxWidth: 880, margin: '0 auto', padding: '56px 24px 64px' }}>

        <div style={{ textAlign: 'center', marginBottom: 40 }}>
          <div style={{ fontSize: 34, fontWeight: 600, letterSpacing: '-0.02em', marginBottom: 10 }}>
            Multi-Tenant RAG Engine
          </div>
          <p style={{ color: 'var(--text-dim)', fontSize: 14.5, lineHeight: 1.6, maxWidth: 560, margin: '0 auto' }}>
            Ask questions against isolated knowledge bases and watch how the answer
            gets built — retrieval scores, re-ranking, citations, and token cost.
          </p>
          {stats && (
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 18, flexWrap: 'wrap' }}>
              <span className="pill mono">{stats.chatModel}</span>
              <span className="pill mono">{stats.embeddingModel} · {stats.embeddingDimensions}d</span>
              <span className="pill">{stats.totalChunks.toLocaleString()} chunks indexed</span>
            </div>
          )}
        </div>

        <SectionLabel
          n="1"
          title="Test RAG on a demo knowledge base"
          hint="Each is a separate tenant with its own documents, persona and guardrails — served by one engine."
        />

        {!tenants.length ? (
          <div style={{ color: 'var(--text-faint)', padding: '18px 2px' }}><Spinner /> loading knowledge bases…</div>
        ) : (
          <div style={{
            display: 'grid', gap: 12, marginBottom: 40,
            gridTemplateColumns: 'repeat(auto-fit, minmax(248px, 1fr))',
          }}>
            {tenants.filter(t => !t.isEphemeral).map(t => (
              // A div rather than a button: the card carries its own nested
              // actions, and a button inside a button is invalid HTML.
              <div
                key={t.projectId}
                role="button"
                tabIndex={0}
                onClick={() => onPickTenant(t.projectId)}
                onKeyDown={e => {
                  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPickTenant(t.projectId); }
                }}
                style={{
                  textAlign: 'left', padding: 18, borderRadius: 12, cursor: 'pointer',
                  border: '1px solid var(--border)', background: 'var(--bg-raised, transparent)',
                  display: 'flex', flexDirection: 'column', gap: 8,
                }}
              >
                <div style={{ fontSize: 26 }}>{ICONS[t.demoIcon ?? 'file'] ?? '📄'}</div>
                <div style={{ fontWeight: 600, fontSize: 15 }}>{t.projectName}</div>
                {t.projectDescription && (
                  <div style={{ fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.5 }}>
                    {t.projectDescription}
                  </div>
                )}
                <div style={{ flex: 1 }} />
                <div style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
                  {t.chunkCount} chunks · {t.documents.length} doc{t.documents.length === 1 ? '' : 's'}
                </div>

                <div style={{ display: 'flex', gap: 7, marginTop: 4, flexWrap: 'wrap' }}>
                  <span className="chip" style={{ borderColor: 'var(--accent-dim)', color: 'var(--accent)' }}>
                    Ask questions →
                  </span>
                  {t.documents.length > 0 && (
                    <button
                      className="chip"
                      onClick={e => { e.stopPropagation(); onPreviewTenant(t.projectId); }}
                      title={t.documents.join(', ')}
                    >
                      📄 Preview document
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        <SectionLabel
          n="2"
          title="Or test it on your own document"
          hint="Upload a file and it becomes a temporary knowledge base you can query immediately. Expires automatically."
        />

        <button
          onClick={onUpload}
          style={{
            width: '100%', padding: '26px 20px', borderRadius: 12, cursor: 'pointer',
            border: '1px dashed var(--border)', background: 'transparent', color: 'inherit',
            font: 'inherit', display: 'flex', alignItems: 'center', gap: 16,
          }}
        >
          <span style={{ fontSize: 26 }}>⬆</span>
          <span style={{ textAlign: 'left' }}>
            <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4 }}>Add a custom document</div>
            <div style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
              {stats?.supportedFormats?.length
                ? `${stats.supportedFormats.join(' · ')} — up to 25 MB`
                : 'PDF · DOCX · PPTX · XLSX · images — up to 25 MB'}
            </div>
          </span>
        </button>

      </div>
    </div>
  );
}

function SectionLabel({ n, title, hint }: { n: string; title: string; hint: string }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <span style={{
          width: 21, height: 21, borderRadius: '50%', display: 'grid', placeItems: 'center',
          fontSize: 11.5, fontWeight: 600, background: 'var(--accent-glow)', color: 'var(--accent)',
        }}>{n}</span>
        <span style={{ fontWeight: 600, fontSize: 15.5 }}>{title}</span>
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--text-faint)', marginTop: 5, marginLeft: 30 }}>{hint}</div>
    </div>
  );
}
