"use client";
import { useEffect, useState } from "react";
import { Badge, Button, Card, Notice, fmtDate } from "@/components/ui";
import { api, errorText, useApi } from "@/lib/api";
import type { Lead, OrchStatus, Orchestration } from "@/lib/types";

const ACTIVE: OrchStatus[] = ["queued", "planning", "researching", "reviewing"];
const TONE: Record<OrchStatus, "neutral" | "info" | "good" | "bad" | "warn"> = {
  queued: "neutral",
  planning: "info",
  researching: "info",
  reviewing: "info",
  awaiting_approval: "good",
  failed: "bad",
  abstained: "warn",
};
const LABEL: Record<OrchStatus, string> = {
  queued: "Queued",
  planning: "Planning",
  researching: "Researching",
  reviewing: "Reviewing",
  awaiting_approval: "Awaiting human approval",
  failed: "Failed",
  abstained: "Abstained: not contacting",
};

/** LangGraph agent runs for a campaign. The trace is what the worker recorded as the Planner,
 * Researcher and Reviewer nodes handed the lead along; ids shown are the LangGraph thread id (the
 * run id) and the graph node that produced each step. */
export function AgentPanel({ campaignId, leads }: { campaignId: string; leads: Lead[] }) {
  const runs = useApi<Orchestration[]>(`campaigns/${campaignId}/orchestrations`);
  const [leadId, setLeadId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reload = runs.reload;
  const anyActive = (runs.data ?? []).some((r) => ACTIVE.includes(r.status));

  useEffect(() => {
    if (!anyActive) return;
    const t = setInterval(() => void reload(), 4000);
    return () => clearInterval(t);
  }, [anyActive, reload]);

  const sorted = [...(runs.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const selected = leadId || leads[0]?.id || "";

  async function start() {
    setBusy(true);
    setError(null);
    try {
      await api("POST", `campaigns/${campaignId}/orchestrations`, { lead_id: selected });
      await reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Agent runs">
      <p className="mb-3 text-xs text-stone-500">
        Planner, Researcher and Reviewer are nodes in a LangGraph chain and hand the lead along in order. They never approve or
        send; a human approves every draft. The worker must be running locally (<code>agents/start.sh</code>) to pick up a run.
      </p>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="agent-lead">Lead</label>
        <select
          id="agent-lead"
          value={selected}
          onChange={(e) => setLeadId(e.target.value)}
          className="rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm"
        >
          {leads.map((l) => (
            <option key={l.id} value={l.id}>{l.company_name}</option>
          ))}
        </select>
        <Button onClick={start} busy={busy} disabled={!selected}>Run agents</Button>
      </div>
      {error && <Notice>{error}</Notice>}
      {runs.error && <Notice tone="warn">{errorText(runs.error)}</Notice>}
      {sorted.length === 0 && !runs.loading && <p className="text-sm text-stone-500">No agent runs yet.</p>}
      <ul className="space-y-4">
        {sorted.map((r) => (
          <li key={r.id} className="rounded-lg border border-stone-200 p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-stone-900">{r.lead_name}</span>
              <Badge tone={TONE[r.status]}>{LABEL[r.status]}</Badge>
              <time className="text-xs text-stone-500" dateTime={r.created_at}>{fmtDate(r.created_at)}</time>
            </div>
            {r.thread_id && <p className="mt-1 font-mono text-[11px] text-stone-400">Thread {r.thread_id}</p>}
            <ol className="mt-2 space-y-1.5">
              {r.trace.map((t, i) => (
                <li key={i} className="text-xs text-stone-700">
                  <Badge tone="accent">{t.agent}</Badge> <span className="font-medium">{t.kind.replace(/_/g, " ")}</span>
                  {t.mentioned && <span className="text-stone-500"> → {t.mentioned}</span>}
                  <span className="text-stone-600"> · {t.summary}</span>
                  {t.step_id && (
                    <span className="font-mono text-[10px] text-stone-400"> · step {t.step_id.slice(0, 16)}</span>
                  )}
                </li>
              ))}
            </ol>
          </li>
        ))}
      </ul>
    </Card>
  );
}
