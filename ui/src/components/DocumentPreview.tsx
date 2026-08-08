import { useEffect, useState } from 'react';
import type { SourceDocument, Tenant } from '../api';
import { api } from '../api';
import { Banner, Empty, Spinner } from './common';

/**
 * Shows the original documents behind a knowledge base.
 *
 * The point is provenance: answers cite chunks, and this is where a visitor
 * confirms those chunks came from a real document rather than the model's
 * memory. PDFs and images render in a frame; Office formats cannot be rendered
 * by the browser, so those offer a download instead of a broken frame.
 */
export function DocumentPreview({ tenant, onClose }: {
  tenant: Tenant | null;
  onClose?: () => void;
}) {
  const [docs, setDocs] = useState<SourceDocument[] | null>(null);
  const [active, setActive] = useState<SourceDocument | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenant) return;
    setDocs(null);
    setActive(null);
    setError(null);
    api.documents(tenant.projectId)
      .then(list => {
        setDocs(list);
        setActive(list.find(d => d.canPreviewInline) ?? list[0] ?? null);
      })
      .catch(e => setError((e as Error).message));
  }, [tenant?.projectId]);

  if (!tenant) return <Empty title="No knowledge base selected" />;
  if (error) return <Banner kind="err">Could not load documents: {error}</Banner>;
  if (!docs) return <div style={{ padding: 20, color: 'var(--text-faint)' }}><Spinner /> loading documents…</div>;
  if (!docs.length) {
    return (
      <Empty
        icon="📄"
        title="No stored originals"
        hint="This knowledge base has chunks indexed but no original file retained."
      />
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
        padding: '12px 16px', borderBottom: '1px solid var(--border)',
      }}>
        <span style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>Source documents</span>
        {docs.map(d => (
          <button
            key={d.sourceFile}
            className="chip"
            style={d.sourceFile === active?.sourceFile
              ? { borderColor: 'var(--accent-dim)', color: 'var(--accent)', background: 'var(--accent-glow)' }
              : undefined}
            onClick={() => setActive(d)}
            title={`${d.chunkCount} chunks · ${d.totalWords.toLocaleString()} words`}
          >
            📄 {d.sourceFile}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        {active && (
          <a
            className="chip"
            href={api.documentUrl(tenant.projectId, active.sourceFile)}
            download={active.sourceFile}
            style={{ textDecoration: 'none' }}
          >
            ↓ Download
          </a>
        )}
        {onClose && <button className="chip" onClick={onClose}>✕ Close</button>}
      </div>

      {active && (
        <div style={{
          display: 'flex', gap: 14, padding: '8px 16px', fontSize: 12,
          color: 'var(--text-faint)', borderBottom: '1px solid var(--border)',
        }}>
          <span>{active.chunkCount} chunks indexed</span>
          <span>{active.totalWords.toLocaleString()} words</span>
          {active.pages > 0 && <span>{active.pages} pages</span>}
          <span className="mono">{active.extension || 'file'}</span>
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, background: 'var(--bg-deep, #111)' }}>
        {active?.canPreviewInline ? (
          <iframe
            key={active.sourceFile}
            title={active.sourceFile}
            src={api.documentUrl(tenant.projectId, active.sourceFile)}
            style={{ width: '100%', height: '100%', border: 0, display: 'block' }}
          />
        ) : (
          <Empty
            icon="⬇"
            title={`${active?.extension ?? 'This format'} can't be previewed in a browser`}
            hint="Download it to view. The text was still parsed and indexed — open the Chunks tab to see exactly what the engine extracted."
          />
        )}
      </div>
    </div>
  );
}
