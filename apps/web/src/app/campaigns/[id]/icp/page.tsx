"use client";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Badge, Button, Card, Empty, Input, Notice, Spinner, Textarea } from "@/components/ui";
import { api, errorText, useApi } from "@/lib/api";
import type { Campaign, Icp, IcpFields } from "@/lib/types";

const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

function Confidence({ value }: { value?: number }) {
  if (value === undefined) return null;
  const pct = Math.round(value * 100);
  const tone = value >= 0.8 ? "good" : value >= 0.6 ? "warn" : "bad";
  return (
    <Badge tone={tone} title="The model's own confidence in this inferred field">
      {pct}% confidence
    </Badge>
  );
}

interface Draft {
  industries: string;
  min: string;
  max: string;
  personas: { title: string; rationale: string }[];
  signals: string;
  exclusions: string;
}

const toDraft = (f: IcpFields): Draft => ({
  industries: f.industries.join("\n"),
  min: String(f.company_size.min_employees),
  max: String(f.company_size.max_employees),
  personas: f.buyer_personas.map((p) => ({ ...p })),
  signals: f.buying_signals.join("\n"),
  exclusions: f.exclusions.join("\n"),
});

export default function IcpReview() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const icp = useApi<Icp>(`campaigns/${id}/icp`);
  const campaign = useApi<Campaign>(`campaigns/${id}`);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState<"" | "save" | "approve" | "discover">("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  const loadedId = icp.data?.id;
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (icp.data) setDraft(toDraft(icp.data.fields));
  }, [loadedId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (icp.loading && !icp.data) return <Spinner label="Loading customer profile" />;
  if (icp.error) return <Notice>{errorText(icp.error)}</Notice>;
  if (!icp.data || !draft) return <Empty title="No customer profile yet" />;

  const locked = campaign.data ? !["icp", "intake"].includes(campaign.data.stage) : false;
  const conf = icp.data.fields.confidence_by_field;

  const patchBody = () => ({
    industries: lines(draft.industries),
    company_size: { min_employees: Number(draft.min), max_employees: Number(draft.max) },
    buyer_personas: draft.personas.filter((p) => p.title.trim()).map((p) => ({ title: p.title.trim(), rationale: p.rationale.trim() })),
    buying_signals: lines(draft.signals),
    exclusions: lines(draft.exclusions),
  });

  async function run(kind: "save" | "approve") {
    setError("");
    setSaved(false);
    setBusy(kind);
    try {
      const next = await api<Icp>("PATCH", `campaigns/${id}/icp`, { ...patchBody(), approve: kind === "approve" });
      icp.set(next);
      if (kind === "save") {
        setSaved(true);
        setBusy("");
        return;
      }
      setBusy("discover");
      await api("POST", `campaigns/${id}/discover`);
      router.push(`/campaigns/${id}/leads`);
    } catch (e) {
      setError(errorText(e));
      setBusy("");
      void campaign.reload();
    }
  }

  const set = (patch: Partial<Draft>) => {
    setSaved(false);
    setDraft({ ...draft, ...patch });
  };
  const disabled = locked || busy !== "";

  return (
    <div className="space-y-6">
      <Card>
        <div className="flex flex-wrap items-center gap-2 text-sm text-stone-600">
          <Badge tone="accent">Version {icp.data.version}</Badge>
          <Badge tone={icp.data.source === "human" ? "info" : "neutral"}>
            {icp.data.source === "human" ? "Edited by you" : "Model-generated"}
          </Badge>
          <span>
            via {icp.data.llm.provider} / {icp.data.llm.model}
          </span>
        </div>
        <p className="mt-3 max-w-3xl text-stone-700">{icp.data.fields.reasoning_summary}</p>
      </Card>

      {locked && (
        <Notice tone="info">
          Discovery has started, so this profile is locked.{" "}
          <a className="underline" href={`/campaigns/${id}/leads`}>
            View leads
          </a>
        </Notice>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Industries" action={<Confidence value={conf.industries} />}>
          <Textarea rows={6} value={draft.industries} disabled={disabled} onChange={(e) => set({ industries: e.target.value })} aria-label="Industries, one per line" />
          <p className="mt-2 text-xs text-stone-500">One per line.</p>
        </Card>

        <Card title="Company size" action={<Confidence value={conf.company_size} />}>
          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1 text-sm">
              <span className="text-stone-700">Min employees</span>
              <Input type="number" min={1} value={draft.min} disabled={disabled} onChange={(e) => set({ min: e.target.value })} />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-stone-700">Max employees</span>
              <Input type="number" min={1} value={draft.max} disabled={disabled} onChange={(e) => set({ max: e.target.value })} />
            </label>
          </div>
        </Card>

        <Card title="Buyer titles" action={<Confidence value={conf.buyer_personas} />} className="md:col-span-2">
          <div className="space-y-3">
            {draft.personas.map((p, i) => (
              <div key={i} className="grid gap-2 sm:grid-cols-[14rem_1fr]">
                <Input
                  value={p.title}
                  disabled={disabled}
                  aria-label={`Buyer title ${i + 1}`}
                  onChange={(e) => set({ personas: draft.personas.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)) })}
                />
                <Input
                  value={p.rationale}
                  disabled={disabled}
                  aria-label={`Rationale for buyer title ${i + 1}`}
                  onChange={(e) => set({ personas: draft.personas.map((x, j) => (j === i ? { ...x, rationale: e.target.value } : x)) })}
                />
              </div>
            ))}
          </div>
        </Card>

        <Card title="Buying signals" action={<Confidence value={conf.buying_signals} />}>
          <Textarea rows={7} value={draft.signals} disabled={disabled} onChange={(e) => set({ signals: e.target.value })} aria-label="Buying signals, one per line" />
          <p className="mt-2 text-xs text-stone-500">One per line. Leads are scored on evidence for these.</p>
        </Card>

        <Card title="Exclusions" action={<Confidence value={conf.exclusions} />}>
          <Textarea rows={7} value={draft.exclusions} disabled={disabled} onChange={(e) => set({ exclusions: e.target.value })} aria-label="Exclusions, one per line" />
          <p className="mt-2 text-xs text-stone-500">Your own exclusions are always kept.</p>
        </Card>
      </div>

      {error && <Notice>{error}</Notice>}
      {saved && <Notice tone="good">Saved as a new version.</Notice>}
      {busy === "discover" && (
        <Notice tone="info">
          Searching the web and verifying companies. This runs live searches and can take up to half a minute.
        </Notice>
      )}

      {!locked && (
        <div className="flex flex-wrap gap-3">
          <Button variant="secondary" busy={busy === "save"} disabled={disabled} onClick={() => run("save")}>
            Save changes
          </Button>
          <Button busy={busy === "approve" || busy === "discover"} disabled={disabled} onClick={() => run("approve")}>
            {busy === "discover" ? "Finding leads" : "Approve ICP & Find Leads"}
          </Button>
        </div>
      )}
    </div>
  );
}
