import type { Decision, Lead } from "@/lib/types";

/** Compact relationship view for one lead: its evidence signals on the left, graph-derived peers/rivals on the right. */
export function LeadGraph({ lead, decision }: { lead: Lead; decision?: Decision }) {
  const signals = [...new Set(lead.evidence.filter((e) => e.scored).map((e) => e.signal))].slice(0, 4);
  const peers = (decision?.peers ?? []).slice(0, 3);
  const rivals = [...new Set([...(decision?.rivals ?? []).map((r) => r.name), ...lead.competitors.map((c) => c.name)])].slice(0, 3);
  const right = [...peers.map((n) => ({ n, kind: "peer" as const })), ...rivals.map((n) => ({ n, kind: "rival" as const }))];
  const rows = Math.max(signals.length, right.length, 1);
  const h = 40 + rows * 46;
  const cy = h / 2;
  const y = (i: number, n: number) => (n === 1 ? cy : 30 + (i * (h - 60)) / (n - 1));
  const clip = (s: string, n = 22) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

  return (
    <svg viewBox={`0 0 360 ${h}`} className="w-full" role="img" aria-label={`Relationships for ${lead.company_name}`}>
      {signals.map((s, i) => (
        <g key={s}>
          <line x1={110} y1={y(i, signals.length)} x2={180} y2={cy} stroke="#a8a29e" />
          <rect x={4} y={y(i, signals.length) - 14} width={106} height={28} rx={6} fill="#ecfdf5" stroke="#6ee7b7" />
          <text x={57} y={y(i, signals.length) + 4} textAnchor="middle" fontSize={9} fill="#065f46">
            <title>{s}</title>
            {clip(s, 20)}
          </text>
        </g>
      ))}
      {right.map(({ n, kind }, i) => (
        <g key={`${kind}-${n}`}>
          <line x1={250} y1={y(i, right.length)} x2={180} y2={cy} stroke="#a8a29e" strokeDasharray={kind === "rival" ? "4 3" : undefined} />
          <rect
            x={250}
            y={y(i, right.length) - 14}
            width={106}
            height={28}
            rx={6}
            fill={kind === "rival" ? "#fff1f2" : "#eef2ff"}
            stroke={kind === "rival" ? "#fda4af" : "#a5b4fc"}
          />
          <text x={303} y={y(i, right.length) + 4} textAnchor="middle" fontSize={9} fill={kind === "rival" ? "#9f1239" : "#3730a3"}>
            <title>{`${kind === "rival" ? "Competitor" : "Peer"}: ${n}`}</title>
            {clip(n, 20)}
          </text>
        </g>
      ))}
      <circle cx={180} cy={cy} r={34} fill="#1c1917" />
      <text x={180} y={cy + 3} textAnchor="middle" fontSize={10} fontWeight={600} fill="#fff">
        {clip(lead.company_name, 12)}
      </text>
      <text x={57} y={12} textAnchor="middle" fontSize={8} fill="#78716c">
        SUPPORTED SIGNALS
      </text>
      <text x={303} y={12} textAnchor="middle" fontSize={8} fill="#78716c">
        RELATED COMPANIES
      </text>
    </svg>
  );
}
