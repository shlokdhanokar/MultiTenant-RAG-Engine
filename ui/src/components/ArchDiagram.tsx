import type { Timings } from '../api';
import { fmtMs } from './common';

const NODES = [
  { id: 'query',    label: 'QUERY',     x: 8,   w: 60 },
  { id: 'embed',    label: 'EMBED',     x: 88,  w: 66 },
  { id: 'search',   label: 'ATLAS kNN', x: 174, w: 84 },
  { id: 'rerank',   label: 'RE-RANK',   x: 278, w: 72 },
  { id: 'generate', label: 'GEMINI',    x: 370, w: 68 },
  { id: 'answer',   label: 'ANSWER',    x: 458, w: 64 },
];

const TIMING_KEY: Record<string, keyof Timings> = {
  embed: 'embedQueryMs',
  search: 'vectorSearchMs',
  rerank: 'rerankMs',
  generate: 'generationMs',
};

/**
 * The pipeline as a live diagram: nodes light up while a query flows through
 * them, and settle showing the real per-stage latency once it completes.
 */
export function ArchDiagram({ active, timings }: { active: string | null; timings: Timings | null }) {
  const done = !active && !!timings;

  return (
    <div>
      <div className="section-label">Pipeline</div>
      <svg viewBox="0 0 530 62" style={{ width: '100%', height: 'auto', display: 'block' }}>
        {NODES.slice(0, -1).map((n, i) => {
          const next = NODES[i + 1];
          const from = n.x + n.w;
          const to = next.x;
          const lit = active === n.id || active === next.id;
          return (
            <g key={`e-${n.id}`}>
              <line className={`arch-edge${lit ? ' lit' : ''}`} x1={from} y1={22} x2={to} y2={22} />
              <polygon
                className={`arch-edge${lit ? ' lit' : ''}`}
                points={`${to},22 ${to - 4},19 ${to - 4},25`}
                fill="currentColor"
              />
            </g>
          );
        })}

        {NODES.map(n => {
          const lit = active === n.id;
          const key = TIMING_KEY[n.id];
          const ms = key && timings ? timings[key] : undefined;
          return (
            <g key={n.id}>
              <rect className={`arch-node${lit ? ' lit' : ''}`} x={n.x} y={8} width={n.w} height={28} rx={5} />
              <text
                className={`arch-label${lit ? ' lit' : ''}`}
                x={n.x + n.w / 2}
                y={26}
                textAnchor="middle"
              >
                {n.label}
              </text>
              {done && ms != null && (
                <text className="arch-label" x={n.x + n.w / 2} y={51} textAnchor="middle" style={{ fontSize: 9, opacity: 0.75 }}>
                  {fmtMs(ms)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
