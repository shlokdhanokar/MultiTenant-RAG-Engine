import { useEffect, useState } from 'react';
import './styles.css';
import type { ChatResult, Stats, Tenant } from './api';
import { api } from './api';
import { Banner, Spinner } from './components/common';
import { Chat, type AutoSend, type Turn } from './components/Chat';
import { Inspector } from './components/Inspector';
import { Upload } from './components/Upload';
import { Chunks } from './components/Chunks';
import { VectorSpace } from './components/VectorSpace';
import { Evaluation } from './components/Evaluation';
import { Compare } from './components/Compare';
import { DocumentPreview } from './components/DocumentPreview';

type Tab = 'chat' | 'document' | 'compare' | 'chunks' | 'vectors' | 'eval' | 'upload';

const TABS: { id: Tab; label: string }[] = [
  { id: 'chat',     label: 'Chat' },
  { id: 'document', label: 'Document' },
  { id: 'compare',  label: 'RAG on/off' },
  { id: 'chunks',   label: 'Chunks' },
  { id: 'vectors',  label: 'Vector space' },
  { id: 'eval',     label: 'Evaluation' },
  { id: 'upload',   label: 'Upload' },
];

const ICONS: Record<string, string> = { palm: '🌴', cross: '✚', file: '📄' };

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('chat');
  const [bootError, setBootError] = useState<string | null>(null);

  const [turns, setTurns] = useState<Turn[]>([]);
  const [lastResult, setLastResult] = useState<ChatResult | null>(null);
  const [activeStage, setActiveStage] = useState<string | null>(null);
  const [autoSend, setAutoSend] = useState<AutoSend | null>(null);
  const [chatBusy, setChatBusy] = useState(false);

  async function loadTenants(selectId?: string) {
    const list = await api.tenants();
    setTenants(list);
    setTenantId(prev => selectId ?? prev ?? list[0]?.projectId ?? null);
  }

  useEffect(() => {
    Promise.all([api.stats().then(setStats), loadTenants()])
      .catch(e => setBootError((e as Error).message));
  }, []);

  const tenant = tenants.find(t => t.projectId === tenantId) ?? null;

  // The curated tenants, in seed order. Visitor uploads are excluded: they are
  // temporary and expire, so a demo must never depend on one being there.
  const demoTenants = tenants.filter(t => !t.isEphemeral);

  // Each knowledge base is an isolated tenant — carrying a conversation across a
  // switch would misrepresent that boundary.
  function switchTenant(id: string) {
    setTenantId(id);
    setTurns([]);
    setLastResult(null);
  }

  /**
   * One click to a finished answer, for someone who has thirty seconds and no
   * intention of reading the UI first.
   *
   * The tenant is the first curated one rather than a hardcoded projectId —
   * that is the flagship demo either way, and it survives the demo data being
   * reseeded under different ids. The question is drawn at random from that
   * tenant's own sample set so a second click demonstrates something new
   * instead of replaying the first.
   */
  function runDemo() {
    const target = demoTenants[0];
    const questions = target?.demoSampleQuestions ?? [];
    if (!target || !questions.length) return;

    switchTenant(target.projectId);
    setTab('chat');
    setAutoSend({
      query: questions[Math.floor(Math.random() * questions.length)],
      nonce: Date.now(),
    });
  }

  const canRunDemo = !chatBusy && demoTenants.some(t => t.demoSampleQuestions?.length);

  if (bootError) {
    return (
      <div className="app">
        <div className="empty">
          <div style={{ maxWidth: 460 }}>
            <Banner kind="err">Could not reach the API: {bootError}</Banner>
            <p style={{ marginTop: 12, fontSize: 12.5, color: 'var(--text-faint)' }}>
              Make sure the backend is running (<span className="mono">python server.py</span>)
              and that <span className="mono">VITE_API_BASE</span> points at it.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">R</span>
          <div>
            <div>RAG Engine</div>
            <div className="brand-sub">multi-tenant retrieval platform</div>
          </div>
        </div>

        <nav className="tabs">
          {TABS.map(t => (
            <button key={t.id} className={`tab${tab === t.id ? ' active' : ''}`} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>

        <div className="topbar-spacer" />

        <button
          className="btn btn-primary btn-sm btn-demo"
          onClick={runDemo}
          disabled={!canRunDemo}
          title="Pick the demo knowledge base, ask it a question, and run the full pipeline"
        >
          {chatBusy ? <Spinner /> : '▶'} Run demo
        </button>

        {stats && (
          <>
            <span className="pill mono" title="Generation model">{stats.chatModel}</span>
            <span className="pill mono" title="Embedding model and dimensionality">
              {stats.embeddingModel} · {stats.embeddingDimensions}d
            </span>
            <span className="pill"><span className="dot live" />live</span>
          </>
        )}
      </header>

      <div className="body">
        {tab === 'chat' ? (
          <div className="split">
            <div className="pane">
              <div className="pane-head"><span className="pane-title">Knowledge bases</span></div>
              <div className="pane-body pad" style={{ flex: '0 0 auto', maxHeight: 268 }}>
                <TenantList
                  tenants={tenants}
                  activeId={tenantId}
                  onSelect={switchTenant}
                  onAdd={() => setTab('upload')}
                />
              </div>
              <div style={{ borderTop: '1px solid var(--border)', flex: 1, minHeight: 0, display: 'flex' }}>
                <Chat
                  tenant={tenant}
                  turns={turns}
                  setTurns={setTurns}
                  onResult={setLastResult}
                  onActiveStage={setActiveStage}
                  autoSend={autoSend}
                  onBusy={setChatBusy}
                />
              </div>
            </div>
            <Inspector result={lastResult} activeStage={activeStage} />
          </div>
        ) : tab === 'document' ? (
          <div className="pane" style={{ margin: 0, borderRadius: 0 }}>
            <DocumentPreview tenant={tenant} onClose={() => setTab('chat')} />
          </div>
        ) : (
          <div style={{ height: '100%', overflowY: 'auto', padding: 20 }}>
            {tab !== 'upload' && (
              <div style={{ maxWidth: 940, margin: '0 auto 18px' }}>
                <TenantStrip
                  tenants={tenants}
                  activeId={tenantId}
                  onSelect={switchTenant}
                  onAdd={() => setTab('upload')}
                />
              </div>
            )}
            {tab === 'compare' && <Compare tenant={tenant} />}
            {tab === 'chunks'  && <Chunks tenant={tenant} />}
            {tab === 'vectors' && <VectorSpace tenant={tenant} />}
            {tab === 'eval'    && <Evaluation tenant={tenant} />}
            {tab === 'upload'  && (
              <Upload
                stats={stats}
                onIngested={r => {
                  loadTenants(r.projectId).then(() => {
                    setTurns([]);
                    setLastResult(null);
                    setTab('chat');
                  });
                }}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Multi-tenant switcher — the same engine, isolated knowledge bases. */
function TenantList({ tenants, activeId, onSelect, onAdd }: {
  tenants: Tenant[]; activeId: string | null; onSelect: (id: string) => void; onAdd: () => void;
}) {
  if (!tenants.length) return <div style={{ color: 'var(--text-faint)' }}><Spinner /> loading…</div>;
  return (
    <div className="tenant-list">
      {tenants.map(t => (
        <button key={t.projectId}
                className={`tenant${t.projectId === activeId ? ' active' : ''}`}
                onClick={() => onSelect(t.projectId)}>
          <span className="tenant-icon">{ICONS[t.demoIcon ?? 'file'] ?? '📄'}</span>
          <span style={{ minWidth: 0, flex: 1 }}>
            <div className="tenant-name" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {t.projectName}
            </div>
            <div className="tenant-meta">
              {t.chunkCount} chunks · {t.documents.length} doc{t.documents.length === 1 ? '' : 's'}
              {t.isEphemeral && ' · temporary'}
            </div>
          </span>
        </button>
      ))}

      {/* Sits with the knowledge bases rather than only behind the Upload tab:
          bringing your own document is the thing most visitors actually want
          to try, and a tab label does not say that. */}
      <button className="tenant tenant-add" onClick={onAdd}>
        <span className="tenant-icon">＋</span>
        <span style={{ minWidth: 0, flex: 1 }}>
          <div className="tenant-name">Add your own knowledge base</div>
          <div className="tenant-meta">upload a document · queryable at once</div>
        </span>
      </button>
    </div>
  );
}

function TenantStrip({ tenants, activeId, onSelect, onAdd }: {
  tenants: Tenant[]; activeId: string | null; onSelect: (id: string) => void; onAdd: () => void;
}) {
  return (
    <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
      {tenants.map(t => (
        <button key={t.projectId}
                className="chip"
                style={t.projectId === activeId
                  ? { borderColor: 'var(--accent-dim)', color: 'var(--accent)', background: 'var(--accent-glow)' }
                  : undefined}
                onClick={() => onSelect(t.projectId)}>
          {ICONS[t.demoIcon ?? 'file'] ?? '📄'} {t.projectName}
        </button>
      ))}
      <button className="chip chip-add" onClick={onAdd}>＋ Add your own</button>
    </div>
  );
}
