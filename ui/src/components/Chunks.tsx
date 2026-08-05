import { useEffect, useState } from 'react';
import type { ChunkDoc, Tenant } from '../api';
import { api } from '../api';
import { Banner, Empty, Spinner } from './common';

/**
 * Chunk explorer: shows how each document was split, the heading hierarchy that
 * drove the split, and which images anchored to which section — the output of
 * the physical-position image mapping.
 */
export function Chunks({ tenant }: { tenant: Tenant | null }) {
  const [docs, setDocs] = useState<ChunkDoc[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(0);

  useEffect(() => {
    if (!tenant) return;
    setDocs(null);
    setError(null);
    setOpen(0);
    api.chunks(tenant.projectId)
      .then(r => setDocs(r.documents))
      .catch(e => setError((e as Error).message));
  }, [tenant?.projectId]);

  if (!tenant) return <Empty title="Select a knowledge base" />;
  if (error) return <div style={{ padding: 16 }}><Banner kind="err">{error}</Banner></div>;
  if (!docs) return <div className="empty"><Spinner /></div>;

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 600, marginBottom: 5 }}>Chunk explorer</h2>
        <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>
          Documents are split on heading boundaries rather than at a fixed token count, so a
          chunk holds one coherent topic. Images anchor to the section they physically sit
          under in the source, which is what keeps media attached to the right text.
        </p>
      </div>

      {docs.map(doc => (
        <div key={doc.sourceFile}>
          <div className="section-label">
            {doc.sourceFile} — {doc.chunkCount} chunks, {doc.totalWords.toLocaleString()} words
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            {doc.chunks.map(c => {
              const isOpen = open === c.chunk_index;
              return (
                <div key={c.chunk_index} className="card">
                  <button
                    onClick={() => setOpen(isOpen ? null : c.chunk_index)}
                    style={{
                      width: '100%', display: 'flex', alignItems: 'center', gap: 10,
                      padding: '11px 13px', textAlign: 'left', color: 'inherit',
                    }}
                  >
                    <span className="rank">{c.chunk_index}</span>
                    <span style={{ flex: 1, fontSize: 13, fontWeight: 500 }}>{c.topic_name}</span>
                    <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
                      {c.word_count}w · p{c.page_start + 1}
                      {c.page_end !== c.page_start ? `–${c.page_end + 1}` : ''}
                    </span>
                    {c.imageUrls.length > 0 && (
                      <span className="pill" style={{ borderColor: 'var(--accent-dim)', color: 'var(--accent)' }}>
                        {c.imageUrls.length} img
                      </span>
                    )}
                    <span style={{ color: 'var(--text-faint)', fontSize: 11 }}>{isOpen ? '▲' : '▼'}</span>
                  </button>

                  {isOpen && (
                    <div className="fade-in" style={{ padding: '0 13px 13px', borderTop: '1px solid var(--border)' }}>
                      <div style={{
                        fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.6,
                        whiteSpace: 'pre-wrap', marginTop: 11,
                      }}>
                        {c.text}
                      </div>
                      {c.imageUrls.length > 0 && (
                        <div style={{ marginTop: 12 }}>
                          <div className="section-label">Anchored images</div>
                          <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap' }}>
                            {c.imageUrls.map(u => (
                              <img
                                key={u}
                                src={u}
                                alt=""
                                loading="lazy"
                                style={{
                                  maxWidth: 190, borderRadius: 'var(--r-sm)',
                                  border: '1px solid var(--border)',
                                }}
                              />
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
