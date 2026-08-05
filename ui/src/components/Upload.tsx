import { useRef, useState } from 'react';
import type { Stats, UploadResult } from '../api';
import { api } from '../api';
import { Banner, Spinner, fmtMs } from './common';

/** Stage names must match the backend's reported stages, in pipeline order. */
const STAGES = [
  { key: 'parse',  label: 'Parse document structure' },
  { key: 'chunk',  label: 'Semantic chunking' },
  { key: 'images', label: 'Anchor images to sections' },
  { key: 'embed',  label: 'Generate embeddings' },
  { key: 'index',  label: 'Index into vector store' },
];

export function Upload({ stats, onIngested }: {
  stats: Stats | null;
  onIngested: (r: UploadResult) => void;
}) {
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [liveStage, setLiveStage] = useState<number>(-1);
  const fileRef = useRef<HTMLInputElement>(null);

  async function ingest(file: File) {
    setBusy(true);
    setError(null);
    setResult(null);
    setLiveStage(0);

    // The backend returns all stage timings at once, so step the indicator
    // forward optimistically to reflect that work is genuinely happening.
    const ticker = setInterval(() => setLiveStage(s => Math.min(s + 1, STAGES.length - 1)), 900);

    try {
      const res = await api.upload(file);
      setResult(res);
      onIngested(res);
      setLiveStage(STAGES.length);
    } catch (e) {
      setError((e as Error).message);
      setLiveStage(-1);
    } finally {
      clearInterval(ticker);
      setBusy(false);
    }
  }

  const accept = stats ? stats.supportedFormats.map(f => `.${f}`).join(',') : undefined;

  return (
    <div style={{ maxWidth: 640, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 600, marginBottom: 5 }}>Test it on your own document</h2>
        <p style={{ color: 'var(--text-dim)', fontSize: 13 }}>
          Upload a file the engine has never seen. It will be parsed, chunked, embedded and
          indexed into a private knowledge base you can immediately query.
        </p>
      </div>

      <div
        className={`dropzone${over ? ' over' : ''}`}
        onDragOver={e => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={e => {
          e.preventDefault();
          setOver(false);
          const f = e.dataTransfer.files?.[0];
          if (f && !busy) ingest(f);
        }}
        onClick={() => !busy && fileRef.current?.click()}
      >
        <input
          ref={fileRef}
          type="file"
          accept={accept}
          style={{ display: 'none' }}
          onChange={e => { const f = e.target.files?.[0]; if (f) ingest(f); e.target.value = ''; }}
        />
        <div style={{ fontSize: 26, marginBottom: 9, opacity: 0.6 }}>⇪</div>
        <div style={{ marginBottom: 5 }}>{busy ? 'Processing…' : 'Drop a file here, or click to browse'}</div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--text-faint)' }}>
          {stats ? stats.supportedFormats.join(' · ').toUpperCase() : 'loading formats…'}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 7 }}>max 25 MB</div>
      </div>

      {error && <Banner kind="err">{error}</Banner>}

      {(busy || result) && (
        <div className="fade-in">
          <div className="section-label">Ingestion pipeline</div>
          <div className="stage-list">
            {STAGES.map((s, i) => {
              const backend = result?.stages.find(x => x.stage === s.key);
              const state = result ? 'done' : i < liveStage ? 'done' : i === liveStage ? 'active' : 'pending';
              return (
                <div key={s.key} className={`stage ${state}`}>
                  <span className="stage-dot" />
                  <span className="stage-name">{s.label}</span>
                  {backend && <span className="stage-detail">{backend.detail}</span>}
                  {backend && <span className="stage-ms">{fmtMs(backend.ms)}</span>}
                  {!backend && state === 'active' && <Spinner />}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {result && (
        <div className="card card-pad fade-in">
          <div className="stat-grid">
            <div className="stat"><div className="label">Chunks</div><div className="value">{result.chunksCreated}</div></div>
            <div className="stat"><div className="label">Images</div><div className="value">{result.imagesExtracted}</div></div>
            <div className="stat"><div className="label">Pages</div><div className="value">{result.totalPages}</div></div>
            <div className="stat"><div className="label">Format</div><div className="value" style={{ fontSize: 16 }}>{result.fileType.toUpperCase()}</div></div>
          </div>
          <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--text-dim)' }}>
            <strong style={{ color: 'var(--green)' }}>Ready.</strong> Switch to the Chat tab —
            “{result.fileName}” is now selectable as a knowledge base.
          </div>
          <div style={{ marginTop: 6, fontSize: 11.5, color: 'var(--text-faint)' }}>
            Temporary knowledge bases are deleted automatically a few hours after upload.
          </div>
        </div>
      )}

      <MultiFormatNote stats={stats} />
    </div>
  );
}

/** Feature 13: makes the format-specific parsing strategies explicit. */
function MultiFormatNote({ stats }: { stats: Stats | null }) {
  const rows = [
    ['PDF',    'PyMuPDF',      'Font-size heuristics vs. document median'],
    ['DOCX',   'python-docx',  'Heading styles, with a bold/size fallback'],
    ['PPTX',   'python-pptx',  'Slide titles as topics; speaker notes included'],
    ['XLSX',   'openpyxl',     'Sheet per topic; rows as “Header: value” pairs'],
    ['Images', 'Tesseract OCR', 'OCR text; also used for scanned PDFs'],
  ];
  return (
    <div>
      <div className="section-label">How each format is parsed</div>
      <div className="card scroll-x">
        <table className="tbl">
          <thead><tr><th>Format</th><th>Parser</th><th>Heading strategy</th></tr></thead>
          <tbody>
            {rows.map(([f, p, s]) => (
              <tr key={f}>
                <td className="mono" style={{ color: 'var(--accent)' }}>{f}</td>
                <td className="mono" style={{ color: 'var(--text-dim)', fontSize: 11.5 }}>{p}</td>
                <td style={{ color: 'var(--text-dim)' }}>{s}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {stats && (
        <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 8 }}>
          A scanned PDF (near-zero extractable text) is detected automatically and routed
          through OCR, rather than silently indexing as an empty knowledge base.
        </div>
      )}
    </div>
  );
}
