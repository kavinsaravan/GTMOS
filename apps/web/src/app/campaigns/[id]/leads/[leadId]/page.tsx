"use client";
import { ArrowLeft, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Badge, Card, Empty, Notice, Spinner, tierTone } from "@/components/ui";
import { DraftPanel } from "@/components/draft-panel";
import { LeadGraph } from "@/components/lead-graph";
import { ResolvePanel } from "@/components/resolve-panel";
import { errorText, useApi } from "@/lib/api";
import { RECOMMENDATION, SOURCE_TYPE, host } from "@/lib/labels";
import type { Decisions, Draft, Lead } from "@/lib/types";

export default function LeadDetail() {
  const { id, leadId } = useParams<{ id: string; leadId: string }>();
  const leads = useApi<Lead[]>(`campaigns/${id}/leads`);
  const drafts = useApi<Draft[]>(`campaigns/${id}/drafts`);
  const decisions = useApi<Decisions>(`campaigns/${id}/graph/decisions`);

  if (leads.loading && !leads.data) return <Spinner label="Loading lead" />;
  if (leads.error) return <Notice>{errorText(leads.error)}</Notice>;
  const lead = leads.data?.find((l) => l.id === leadId);
  if (!lead) return <Empty title="Lead not found" />;

  const dec = decisions.data?.decisions.find((d) => d.lead_id === lead.id);
  const versions = (drafts.data ?? []).filter((d) => d.lead_id === lead.id);
  const reload = async () => {
    await Promise.all([drafts.reload(), leads.reload()]);
  };

  const employeesVerified = lead.employees_source === "evidence";

  return (
    <div className="space-y-5">
      <Link href={`/campaigns/${id}/leads`} className="inline-flex items-center gap-1 text-sm text-stone-600 hover:text-stone-900">
        <ArrowLeft className="size-4" aria-hidden /> All leads
      </Link>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.1fr)]">
        {/* Left: overview and evidence */}
        <div className="space-y-5">
          <Card>
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="font-serif text-2xl font-semibold text-stone-900">{lead.company_name}</h2>
                <a href={`https://${lead.domain}`} target="_blank" rel="noopener noreferrer" className="text-sm text-indigo-700 hover:underline">
                  {lead.domain}
                </a>
              </div>
              <div className="text-right">
                <p className="text-3xl font-semibold tabular-nums">{lead.fit_score}</p>
                <Badge tone={tierTone(lead.tier)}>Tier {lead.tier}</Badge>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Badge tone={lead.verification_status === "verified" ? "good" : "warn"}>
                {lead.verification_status === "verified" ? "Domain verified" : "Domain unverified"}
              </Badge>
              <Badge tone={employeesVerified ? "good" : "neutral"} title="Where the headcount comes from">
                {lead.employees
                  ? `~${lead.employees} employees (${employeesVerified ? "stated in a source" : "inferred"})`
                  : "Headcount not found"}
              </Badge>
              <Badge>Industry: {lead.industry_match}</Badge>
              <Badge>Geography: {lead.geography_match}</Badge>
            </div>
          </Card>

          <Card title="Why it matches">
            <ul className="space-y-3">
              {Object.entries(lead.score_breakdown).map(([k, v]) => (
                <li key={k}>
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium capitalize text-stone-800">{k.replace("_", " / ")}</span>
                    <span className="tabular-nums text-stone-600">
                      {v.points}/{v.max}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-stone-200" aria-hidden>
                    <div className="h-full bg-stone-800" style={{ width: `${(v.points / v.max) * 100}%` }} />
                  </div>
                  <p className="mt-1 text-xs text-stone-500">{v.reason}</p>
                </li>
              ))}
            </ul>
          </Card>

          <Card title={`Evidence (${lead.evidence.length})`}>
            <ul className="space-y-3">
              {lead.evidence.map((e, i) => (
                <li key={i} className="rounded-lg bg-stone-50 p-3 text-sm">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <Badge tone={e.scored ? "good" : "neutral"}>{e.scored ? "Counted" : "Context only"}</Badge>
                    <Badge tone={e.source_type === "primary" ? "info" : "neutral"}>{SOURCE_TYPE[e.source_type] ?? e.source_type}</Badge>
                  </div>
                  <p className="font-medium text-stone-800">{e.signal}</p>
                  <p className="mt-1 text-stone-600">“{e.quote}”</p>
                  <a href={e.url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs text-indigo-700 hover:underline">
                    {host(e.url)} <ExternalLink className="size-3" aria-hidden />
                  </a>
                </li>
              ))}
              {lead.evidence.length === 0 && <li className="text-sm text-stone-500">No evidence was found for this company.</li>}
            </ul>
          </Card>
        </div>

        {/* Center: graph */}
        <div className="space-y-5">
          <Card title="Relationships">
            <LeadGraph lead={lead} decision={dec} />
          </Card>
          <Card title="Graph recommendation">
            {dec ? (
              <div className="space-y-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={RECOMMENDATION[dec.recommendation].tone}>{RECOMMENDATION[dec.recommendation].label}</Badge>
                  {dec.changed ? (
                    <span className="text-stone-600">changed from “{RECOMMENDATION[dec.baseline].label}” by the graph query</span>
                  ) : (
                    <span className="text-stone-500">same as the fit-tier baseline</span>
                  )}
                </div>
                <ul className="list-disc pl-5 text-stone-700">
                  {dec.reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
                {dec.hook && (
                  <div className="rounded-lg bg-stone-50 p-3 text-stone-700">
                    <p className="text-xs font-medium uppercase tracking-wide text-stone-500">Outreach hook</p>
                    <p className="mt-1 font-medium">{dec.hook.signal}</p>
                    <p className="mt-1 text-stone-600">“{dec.hook.quote}”</p>
                    <a href={dec.hook.url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs text-indigo-700 hover:underline">
                      {host(dec.hook.url)} <ExternalLink className="size-3" aria-hidden />
                    </a>
                  </div>
                )}
                {dec.recommendation === "verify_first" && (
                  <ResolvePanel campaignId={id} leadId={lead.id} onChange={() => void leads.reload()} />
                )}
                {dec.rivals.length > 0 && (
                  <p className="text-stone-600">
                    Competitors in the graph: {dec.rivals.map((r) => (r.outreach_status ? `${r.name} (${r.outreach_status})` : r.name)).join(", ")}
                  </p>
                )}
              </div>
            ) : (
              <p className="text-sm text-stone-500">
                Run the graph analysis from the{" "}
                <Link className="underline" href={`/campaigns/${id}/leads`}>
                  leads table
                </Link>{" "}
                to see a relationship-based recommendation.
              </p>
            )}
          </Card>
        </div>

        {/* Right: outreach */}
        <Card title="Outreach draft">
          {drafts.loading && !drafts.data ? (
            <Spinner />
          ) : drafts.error ? (
            <Notice>{errorText(drafts.error)}</Notice>
          ) : (
            <DraftPanel campaignId={id} lead={lead} versions={versions} onChange={reload} />
          )}
        </Card>
      </div>
    </div>
  );
}
