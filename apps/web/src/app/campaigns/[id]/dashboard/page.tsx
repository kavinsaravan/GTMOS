"use client";
import * as Collapsible from "@radix-ui/react-collapsible";
import { ChevronDown } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo } from "react";
import { AgentPanel } from "@/components/agent-panel";
import { Badge, Card, Notice, Spinner, fmtDate } from "@/components/ui";
import { errorText, useApi } from "@/lib/api";
import type { AuditEvent, Draft, Lead } from "@/lib/types";

const COLUMNS = ["Matched", "Contacted", "Replied", "Meeting Booked", "Closed-Lost"] as const;
// Lead.status "new" is a matched lead. A lead only moves right once a real send/reply/meeting is recorded.
const COLUMN_OF: Record<string, (typeof COLUMNS)[number]> = {
  new: "Matched",
  matched: "Matched",
  contacted: "Contacted",
  replied: "Replied",
  meeting_booked: "Meeting Booked",
  closed_lost: "Closed-Lost",
};

const isAgent = (a: string) => a.startsWith("agent:") || a === "system";

function describe(e: AuditEvent): string {
  const d = e.details ?? {};
  const bits = Object.entries(d)
    .filter(([, v]) => v !== null && v !== undefined && typeof v !== "object")
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${String(v)}`);
  return bits.join(" · ");
}

export default function Dashboard() {
  const { id } = useParams<{ id: string }>();
  const leads = useApi<Lead[]>(`campaigns/${id}/leads`);
  const drafts = useApi<Draft[]>(`campaigns/${id}/drafts`);
  const events = useApi<AuditEvent[]>(`campaigns/${id}/events`);

  const latestDrafts = useMemo(() => {
    const m = new Map<string, Draft>();
    for (const d of drafts.data ?? []) {
      const cur = m.get(d.lead_id);
      if (!cur || d.version > cur.version) m.set(d.lead_id, d);
    }
    return [...m.values()];
  }, [drafts.data]);

  const sorted = useMemo(
    () => [...(events.data ?? [])].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at)),
    [events.data],
  );

  if ((leads.loading && !leads.data) || (events.loading && !events.data)) return <Spinner label="Loading command center" />;
  const err = leads.error ?? drafts.error ?? events.error;
  if (err) return <Notice>{errorText(err)}</Notice>;

  const L = leads.data ?? [];
  const nameOf = new Map(L.map((l) => [l.id, l.company_name]));
  const stats = [
    { label: "Researched leads", value: L.length },
    { label: "Evidence-backed matches", value: L.filter((l) => l.evidence.some((e) => e.scored)).length },
    { label: "Drafts pending review", value: latestDrafts.filter((d) => d.status === "pending_review" || d.status === "blocked").length },
    { label: "Approvals", value: latestDrafts.filter((d) => d.status === "approved").length },
    { label: "Confirmed sends", value: latestDrafts.filter((d) => d.status === "sent").length },
  ];
  const agentEvents = sorted.filter((e) => isAgent(e.actor));

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        {stats.map((s) => (
          <div key={s.label} className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
            <p className="text-3xl font-semibold tabular-nums text-stone-900">{s.value}</p>
            <p className="mt-1 text-xs text-stone-500">{s.label}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-4 md:grid-cols-5">
        {COLUMNS.map((col) => {
          const items = L.filter((l) => (COLUMN_OF[l.status] ?? "Matched") === col);
          return (
            <section key={col} aria-label={col} className="rounded-xl bg-stone-100 p-3">
              <h3 className="mb-3 flex items-center justify-between text-sm font-semibold text-stone-800">
                {col} <Badge>{items.length}</Badge>
              </h3>
              <ul className="space-y-2">
                {items.map((l) => (
                  <li key={l.id}>
                    <Link
                      href={`/campaigns/${id}/leads/${l.id}`}
                      className="block rounded-lg border border-stone-200 bg-white p-3 text-sm shadow-sm hover:border-stone-400"
                    >
                      <span className="font-medium text-stone-900">{l.company_name}</span>
                      <span className="mt-1 flex items-center gap-2 text-xs text-stone-500">
                        Fit {l.fit_score} · Tier {l.tier}
                      </span>
                    </Link>
                  </li>
                ))}
                {items.length === 0 && (
                  <li className="px-1 py-3 text-xs text-stone-500">
                    {col === "Matched" ? "No matched leads." : "Empty. Leads move here only after a recorded real send or reply."}
                  </li>
                )}
              </ul>
            </section>
          );
        })}
      </div>

      <AgentPanel campaignId={id} leads={L} />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Card title="Activity timeline">
          {sorted.length === 0 ? (
            <p className="text-sm text-stone-500">No activity yet.</p>
          ) : (
            <ol className="space-y-4">
              {sorted.map((e) => (
                <li key={e.id} className="relative border-l-2 border-stone-200 pl-4">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium text-stone-900">{e.action}</span>
                    <Badge tone={isAgent(e.actor) ? "accent" : "info"}>{e.actor}</Badge>
                    <time className="text-xs text-stone-500" dateTime={e.occurred_at}>
                      {fmtDate(e.occurred_at)}
                    </time>
                  </div>
                  <p className="mt-0.5 text-xs text-stone-600">
                    {typeof e.details?.lead_id === "string" && nameOf.get(e.details.lead_id)
                      ? `${nameOf.get(e.details.lead_id)} · `
                      : ""}
                    {describe(e)}
                  </p>
                  <p className="font-mono text-[11px] text-stone-400">
                    {e.entity_id} · {e.operation_id}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </Card>

        <Collapsible.Root defaultOpen className="self-start rounded-xl border border-stone-200 bg-white shadow-sm">
          <Collapsible.Trigger className="group flex w-full items-center justify-between px-5 py-3 text-left text-sm font-semibold text-stone-900">
            Agent activity ({agentEvents.length})
            <ChevronDown className="size-4 transition-transform group-data-[state=open]:rotate-180" aria-hidden />
          </Collapsible.Trigger>
          <Collapsible.Content className="border-t border-stone-100 px-5 py-4">
            <p className="mb-3 text-xs text-stone-500">Tool and validation summaries recorded by agents. No reasoning traces are stored.</p>
            <ul className="space-y-2 text-sm">
              {agentEvents.map((e) => (
                <li key={e.id} className="rounded-lg bg-stone-50 px-3 py-2">
                  <span className="font-medium text-stone-800">{e.actor}</span> <span className="text-stone-600">{e.action}</span>
                  <p className="text-xs text-stone-500">{describe(e) || "—"}</p>
                </li>
              ))}
              {agentEvents.length === 0 && <li className="text-stone-500">No agent actions yet.</li>}
            </ul>
          </Collapsible.Content>
        </Collapsible.Root>
      </div>
    </div>
  );
}
