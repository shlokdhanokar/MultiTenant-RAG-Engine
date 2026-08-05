import { useEffect, useState } from 'react';
import './styles.css';
import type { ChatResult, Stats, Tenant } from './api';
import { api } from './api';
import { Banner, Spinner } from './components/common';
import { Chat, type Turn } from './components/Chat';
import { Inspector } from './components/Inspector';
import { Upload } from './components/Upload';
import { Chunks } from './components/Chunks';
import { VectorSpace } from './components/VectorSpace';
import { Evaluation } from './components/Evaluation';
import { Compare } from './components/Compare';

type Tab = 'chat' | 'compare' | 'chunks' | 'vectors' | 'eval' | 'upload';

const TABS: { id: Tab; label: string }[] = [
  { id: 'chat',    label: 'Chat' },
  { id: 'compare', label: 'RAG on/off' },
  { id: 'chunks',  label: 'Chunks' },
  { id: 'vectors', label: 'Vector space' },
  { id: 'eval',    label: 'Evaluation' },
  { id: 'upload',  label: 'Upload' },
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

  // Each knowledge base is an isolated tenant — carrying a conversation across a
  // switch would misrepresent that boundary.
  function switchTenant(id: string) {
    setTenantId(id);
    setTurns([]);
    setLastResult(null);
  }

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
              <div className="pane-body pad" style={{ flex: '0 0 auto', maxHeight: 232 }}>
                <TenantList tenants={tenants} activeId={tenantId} onSelect={switchTenant} />
              </div>
              <div style={{ borderTop: '1px solid var(--border)', flex: 1, minHeight: 0, display: 'flex' }}>
                <Chat
                  tenant={tenant}
                  turns={turns}
                  setTurns={setTurns}
                  onResult={setLastResult}
                  onActiveStage={setActiveStage}
                />
              </div>
            </div>
            <Inspector result={lastResult} activeStage={activeStage} />
          </div>
        ) : (
          <div style={{ height: '100%', overflowY: 'auto', padding: 20 }}>
            {tab !== 'upload' && (
              <div style={{ maxWidth: 940, margin: '0 auto 18px' }}>
                <TenantStrip tenants={tenants} activeId={tenantId} onSelect={switchTenant} />
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
function TenantList({ tenants, activeId, onSelect }: {
  tenants: Tenant[]; activeId: string | null; onSelect: (id: string) => void;
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
    </div>
  );
}

function TenantStrip({ tenants, activeId, onSelect }: {
  tenants: Tenant[]; activeId: string | null; onSelect: (id: string) => void;
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
    </div>
  );
}
