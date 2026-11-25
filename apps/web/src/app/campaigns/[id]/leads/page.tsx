"use client";
import { ArrowUpDown, Network } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { Badge, Button, Card, Empty, Notice, Spinner, tierTone } from "@/components/ui";
import { api, errorText, useApi } from "@/lib/api";
import { DRAFT_STATUS, RECOMMENDATION } from "@/lib/labels";
import type { Decisions, Draft, Lead } from "@/lib/types";

type SortKey = "fit" | "name" | "citations";

export default function Leads() {
  const { id } = useParams<{ id: string }>();
  const leads = useApi<Lead[]>(`campaigns/${id}/leads`);
  const drafts = useApi<Draft[]>(`campaigns/${id}/drafts`);
  const decisions = useApi<Decisions>(`campaigns/${id}/graph/decisions`);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "fit", dir: -1 });
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState("");

  const decisionByLead = useMemo(
    () => new Map((decisions.data?.decisions ?? []).map((d) => [d.lead_id, d])),
    [decisions.data],
  );
  const draftByLead = useMemo(() => {
    const m = new Map<string, Draft>();
    for (const d of drafts.data ?? []) {
      const cur = m.get(d.lead_id);
      if (!cur || d.version > cur.version) m.set(d.lead_id, d);
    }
    return m;
  }, [drafts.data]);

  const rows = useMemo(() => {
    const list = [...(leads.data ?? [])];
    const val = (l: Lead) => (sort.key === "fit" ? l.fit_score : sort.key === "citations" ? l.evidence.length : l.company_name.toLowerCase());
    list.sort((a, b) => (val(a) < val(b) ? -1 : val(a) > val(b) ? 1 : 0) * sort.dir);
    return list;
  }, [leads.data, sort]);

  async function analyse() {
    setSyncing(true);
    setSyncError("");
    try {
      await api("POST", `campaigns/${id}/graph/sync`);
      await decisions.reload();
    } catch (e) {
      setSyncError(errorText(e));
    } finally {
      setSyncing(false);
    }
  }

  async function retryDiscovery() {
    setRetrying(true);
    setRetryError("");
    try {
      await api("POST", `campaigns/${id}/discover`);
      await leads.reload();
      await decisions.reload();
    } catch (e) {
      setRetryError(errorText(e));
    } finally {
      setRetrying(false);
    }
  }

  const toggle = (key: SortKey) => setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : key === "name" ? 1 : -1 }));

  if (leads.loading && !leads.data) return <Spinner label="Loading leads" />;
  if (leads.error) return <Notice>{errorText(leads.error)}</Notice>;
  if (!leads.data || leads.data.length === 0) {
    return (
      <Empty title="No leads yet">
        Approve the customer profile to start discovery.{" "}
        <Link className="underline" href={`/campaigns/${id}/icp`}>
          Go to profile
        </Link>
        <div className="mt-4 space-y-3">
          <p className="text-xs text-stone-500">
            Already approved and nothing appeared? The last discovery run may have failed. Retrying can take up to 30 seconds and is only
            charged if it succeeds.
          </p>
          <Button variant="secondary" busy={retrying} onClick={retryDiscovery}>
            Retry discovery
          </Button>
          {retryError && <Notice>{retryError}</Notice>}
        </div>
      </Empty>
    );
  }

  const summary = decisions.data?.summary;
  const th = "px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-stone-500";

  return (
    <div className="space-y-5">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="max-w-2xl">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-stone-900">
              <Network className="size-4" aria-hidden /> Graph decision check
            </h2>
            {summary ? (
              <p className="mt-1 text-sm text-stone-600">
                The relationship graph changed the outreach recommendation for{" "}
                <strong className="text-stone-900">
                  {summary.changed} of {summary.leads}
                </strong>{" "}
                leads versus ranking by fit tier alone ({summary.contact_now} contact now, {summary.verify_first} verify first,{" "}
                {summary.hold_rival_active} hold, {summary.skip} skip).
              </p>
            ) : (
              <p className="mt-1 text-sm text-stone-600">
                Project these leads, their evidence and competitors into the graph, then query it to decide who to contact.
              </p>
            )}
          </div>
          <Button variant="secondary" busy={syncing} onClick={analyse}>
            {summary ? "Re-run graph analysis" : "Run graph analysis"}
          </Button>
        </div>
        {syncError && (
          <div className="mt-3">
            <Notice>{syncError}</Notice>
          </div>
        )}
      </Card>

      <div className="overflow-x-auto rounded-xl border border-stone-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-stone-200 text-sm">
          <thead className="bg-stone-50">
            <tr>
              <th className={th} scope="col">
                <button className="inline-flex items-center gap-1" onClick={() => toggle("name")}>
                  Company <ArrowUpDown className="size-3" aria-hidden />
                </button>
              </th>
              <th className={th} scope="col" aria-sort={sort.key === "fit" ? (sort.dir === -1 ? "descending" : "ascending") : "none"}>
                <button className="inline-flex items-center gap-1" onClick={() => toggle("fit")}>
                  Fit <ArrowUpDown className="size-3" aria-hidden />
                </button>
              </th>
              <th className={th} scope="col">Tier</th>
              <th className={th} scope="col">Trigger</th>
              <th className={th} scope="col">
                <button className="inline-flex items-center gap-1" onClick={() => toggle("citations")}>
                  Citations <ArrowUpDown className="size-3" aria-hidden />
                </button>
              </th>
              <th className={th} scope="col">Research</th>
              <th className={th} scope="col">Graph says</th>
              <th className={th} scope="col">Outreach</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-100">
            {rows.map((l) => {
              const scored = l.evidence.find((e) => e.scored);
              const dec = decisionByLead.get(l.id);
              const draft = draftByLead.get(l.id);
              return (
                <tr key={l.id} className="hover:bg-stone-50">
                  <td className="px-4 py-3">
                    <Link href={`/campaigns/${id}/leads/${l.id}`} className="font-medium text-stone-900 hover:underline">
                      {l.company_name}
                    </Link>
                    <div className="text-xs text-stone-500">{l.domain}</div>
                  </td>
                  <td className="px-4 py-3 tabular-nums">
                    <div className="flex items-center gap-2">
                      <span className="w-7 font-semibold">{l.fit_score}</span>
                      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-stone-200" aria-hidden>
                        <span className="block h-full bg-stone-800" style={{ width: `${l.fit_score}%` }} />
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone={tierTone(l.tier)}>Tier {l.tier}</Badge>
                  </td>
                  <td className="max-w-xs px-4 py-3 text-stone-700">
                    {scored ? scored.signal : <span className="text-stone-400">No supported trigger</span>}
                  </td>
                  <td className="px-4 py-3 tabular-nums">{l.evidence.length}</td>
                  <td className="px-4 py-3">
                    <Badge tone={l.verification_status === "verified" ? "good" : "warn"}>
                      {l.verification_status === "verified" ? "Domain verified" : "Unverified"}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    {dec ? (
                      <div className="flex flex-col items-start gap-1">
                        <Badge tone={RECOMMENDATION[dec.recommendation].tone}>{RECOMMENDATION[dec.recommendation].label}</Badge>
                        {dec.changed && (
                          <span className="text-xs text-stone-500">was: {RECOMMENDATION[dec.baseline].label}</span>
                        )}
                      </div>
                    ) : (
                      <span className="text-stone-400">–</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {draft ? (
                      <Badge tone={DRAFT_STATUS[draft.status].tone}>{DRAFT_STATUS[draft.status].label}</Badge>
                    ) : (
                      <span className="text-stone-400">No draft</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
