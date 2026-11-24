"use client";
import { useState } from "react";
import { Badge, Button, Notice } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { RECOMMENDATION } from "@/lib/labels";
import type { Resolution } from "@/lib/types";

/** For a verify_first lead: spend credits on one targeted search for the missing fact, then show
 * what was found or why the system abstains. Evidence is copied from search results, never invented. */
export function ResolvePanel({ campaignId, leadId, onChange }: { campaignId: string; leadId: string; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Resolution | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api<Resolution>("POST", `campaigns/${campaignId}/leads/${leadId}/resolve`, {}));
      onChange();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
      <p className="text-stone-700">
        One fact is missing before this lead is safe to contact. Research it with a targeted search (2 credits).
      </p>
      <Button onClick={run} busy={busy}>Resolve uncertainty</Button>
      {error && <Notice>{error}</Notice>}
      {result && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={result.outcome === "resolved" ? "good" : "warn"}>
              {result.outcome === "resolved" ? "Resolved" : "Abstained: not contacting"}
            </Badge>
            <span className="text-stone-600">
              {RECOMMENDATION[result.before].label} → {RECOMMENDATION[result.after].label}
            </span>
          </div>
          {(["known", "uncertain", "would_make_actionable"] as const).map((k) => (
            <div key={k}>
              <p className="text-xs font-medium uppercase tracking-wide text-stone-500">
                {k === "known" ? "What we know" : k === "uncertain" ? "What was uncertain" : "What would make it actionable"}
              </p>
              <ul className="list-disc pl-5 text-stone-700">
                {result.brief[k].map((t, i) => (
                  <li key={i}>{t}</li>
                ))}
              </ul>
            </div>
          ))}
          <p className="text-xs text-stone-500">The graph recommendation above refreshes after the next graph sync.</p>
        </div>
      )}
    </div>
  );
}
