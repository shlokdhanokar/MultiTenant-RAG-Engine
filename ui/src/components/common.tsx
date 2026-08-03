import type { ReactNode } from 'react';

export function Pane({ title, actions, children, pad }: {
  title: string; actions?: ReactNode; children: ReactNode; pad?: boolean;
}) {
  return (
    <div className="pane">
      <div className="pane-head">
        <span className="pane-title">{title}</span>
        <div style={{ flex: 1 }} />
        {actions}
      </div>
      <div className={`pane-body${pad ? ' pad' : ''}`}>{children}</div>
    </div>
  );
}

export function Stat({ label, value, sub, color }: {
  label: string; value: ReactNode; sub?: string; color?: string;
}) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value" style={color ? { color } : undefined}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function ScoreBar({ label, value, kind, max = 1 }: {
  label: string; value: number; kind: 'vec' | 'kw' | 'total'; max?: number;
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="score-row">
      <span className="lbl">{label}</span>
      <div className="bar-track"><div className={`bar-fill ${kind}`} style={{ width: `${pct}%` }} /></div>
      <span className="num">{value.toFixed(3)}</span>
    </div>
  );
}

export function Empty({ icon, title, hint }: { icon?: string; title: string; hint?: string }) {
  return (
    <div className="empty">
      <div>
        {icon && <div style={{ fontSize: 28, marginBottom: 10, opacity: 0.5 }}>{icon}</div>}
        <div style={{ color: 'var(--text-dim)', marginBottom: 5 }}>{title}</div>
        {hint && <div style={{ fontSize: 12.5, maxWidth: 330 }}>{hint}</div>}
      </div>
    </div>
  );
}

export function Banner({ kind, children }: { kind: 'err' | 'warn' | 'info'; children: ReactNode }) {
  return <div className={`banner ${kind}`}>{children}</div>;
}

export function Spinner() { return <span className="spinner" />; }

export const fmtMs = (ms?: number) => (ms == null ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`);
export const fmtCost = (usd?: number) => (usd == null ? '—' : usd < 0.01 ? `$${usd.toFixed(5)}` : `$${usd.toFixed(4)}`);
