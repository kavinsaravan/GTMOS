"use client";
import { Check, ExternalLink, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Input, Notice, Textarea, fmtDate } from "./ui";
import { api, errorText } from "@/lib/api";
import { DRAFT_STATUS, GMAIL_STATE, SOURCE_TYPE, host } from "@/lib/labels";
import type { Draft, Lead } from "@/lib/types";

type Action = "" | "generate" | "enrich" | "edit" | "approve" | "reject";

export function DraftPanel({
  campaignId,
  lead,
  versions,
  onChange,
}: {
  campaignId: string;
  lead: Lead;
  versions: Draft[];
  onChange: () => Promise<void>;
}) {
  const latest = versions.reduce<Draft | null>((a, d) => (!a || d.version > a.version ? d : a), null);
  const [busy, setBusy] = useState<Action>("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [edits, setEdits] = useState<{ id: string; subject: string; body: string } | null>(null);

  const base = `campaigns/${campaignId}`;
  const run = async (kind: Action, fn: () => Promise<unknown>, done?: string) => {
    setBusy(kind);
    setError("");
    setInfo("");
    try {
      await fn();
      await onChange();
      if (done) setInfo(done);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy("");
    }
  };

  const enrich = () =>
    run("enrich", async () => {
      const r = await api<Lead>("POST", `${base}/leads/${lead.id}/enrich`);
      if (!r.contacts?.length) throw new Error("No usable contact found for this domain.");
    }, "Contact found.");

  const generate = () => run("generate", () => api("POST", `${base}/leads/${lead.id}/draft`));

  if (!latest) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-stone-600">
          No draft yet. The Writer uses only this lead&apos;s cited evidence, then a separate Reviewer removes anything
          unsupported.
        </p>
        {error && <Notice>{error}</Notice>}
        <Button busy={busy === "generate"} onClick={generate} disabled={lead.evidence.length === 0}>
          Generate draft
        </Button>
        {lead.evidence.length === 0 && <p className="text-xs text-stone-500">This lead has no evidence to write from.</p>}
      </div>
    );
  }

  const editable = latest.status === "pending_review" || latest.status === "blocked";
  const current = edits && edits.id === latest.id ? edits : { id: latest.id, subject: latest.subject, body: latest.body };
  const dirty = current.subject !== latest.subject || current.body !== latest.body;
  const st = DRAFT_STATUS[latest.status];
  const recipient = latest.recipient;
  const contact = recipient.email;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={st.tone}>{st.label}</Badge>
        <Badge>v{latest.version}</Badge>
        <Badge tone={latest.source === "human" ? "info" : "neutral"}>{latest.source === "human" ? "Edited by you" : "AI-written"}</Badge>
      </div>

      <div className="rounded-lg bg-stone-50 px-3 py-2 text-sm">
        <span className="text-stone-500">To: </span>
        {contact ? (
          <>
            <span className="font-medium">{contact}</span>
            <span className="text-stone-500">
              {" "}
              ({recipient.role ?? "contact"}, {recipient.verification_status ?? "unverified"})
            </span>
          </>
        ) : (
          <span className="text-stone-700">
            {recipient.role ?? "Unknown role"} <span className="text-stone-500">(no contact email found)</span>
          </span>
        )}
        {!contact && latest.status !== "rejected" && (
          <div className="mt-2">
            <Button variant="secondary" className="!px-2.5 !py-1 text-xs" busy={busy === "enrich"} onClick={enrich}>
              Find contact
            </Button>
          </div>
        )}
      </div>

      {latest.status === "blocked" && (
        <Notice tone="warn">
          The Reviewer found too little supported content to keep this draft. Edit it yourself or reject it.
        </Notice>
      )}

      <label className="block space-y-1">
        <span className="text-xs font-medium text-stone-600">Subject</span>
        <Input
          value={current.subject}
          disabled={!editable || busy !== ""}
          onChange={(e) => setEdits({ ...current, subject: e.target.value })}
        />
      </label>
      <label className="block space-y-1">
        <span className="text-xs font-medium text-stone-600">Body</span>
        <Textarea
          rows={11}
          value={current.body}
          disabled={!editable || busy !== ""}
          onChange={(e) => setEdits({ ...current, body: e.target.value })}
        />
      </label>

      {latest.edit_warnings && latest.edit_warnings.length > 0 && (
        <Notice tone="warn">
          <p className="font-medium">Rule check on your edit</p>
          <ul className="mt-1 list-disc pl-5">
            {latest.edit_warnings.map((w, i) => (
              <li key={i}>
                {w.where}: {w.reason}
              </li>
            ))}
          </ul>
        </Notice>
      )}

      <div className="rounded-lg border border-stone-200 px-3 py-2 text-sm">
        <p className="flex items-center gap-1.5 font-medium text-stone-800">
          <ShieldCheck className="size-4 text-emerald-700" aria-hidden /> Reviewer: {latest.reviewer?.status ?? "n/a"}
        </p>
        {latest.reviewer?.removed?.length > 0 ? (
          <ul className="mt-1 space-y-1 text-stone-600">
            {latest.reviewer.removed.map((r, i) => (
              <li key={i}>
                Removed “{r.sentence}…” — {r.reason}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-stone-500">No sentences removed.</p>
        )}
      </div>

      <details className="text-sm">
        <summary className="cursor-pointer font-medium text-stone-700">Evidence behind this draft ({latest.facts.length})</summary>
        <ul className="mt-2 space-y-2">
          {latest.facts.map((f) => (
            <li key={f.id} className="rounded-lg bg-stone-50 p-2.5">
              <p className="text-stone-800">“{f.quote}”</p>
              <a href={f.url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs text-indigo-700 hover:underline">
                {host(f.url)} <ExternalLink className="size-3" aria-hidden /> · {SOURCE_TYPE[f.source_type] ?? f.source_type}
              </a>
            </li>
          ))}
        </ul>
      </details>

      {latest.status === "approved" && (
        <Notice tone={latest.gmail?.status === "created" ? "good" : "info"}>
          <p>
            Approved by {latest.approved_by} on {fmtDate(latest.approved_at)}. Nothing has been sent.
          </p>
          <p className="mt-1">{GMAIL_STATE[latest.gmail?.status ?? ""] ?? "Gmail draft pending"}</p>
        </Notice>
      )}
      {latest.status === "rejected" && (
        <Notice tone="info">
          Rejected {fmtDate(latest.rejected_at)}
          {latest.rejection_reason ? `: ${latest.rejection_reason}` : ""}. This draft will not be used.
        </Notice>
      )}

      {error && <Notice>{error}</Notice>}
      {info && <Notice tone="good">{info}</Notice>}

      {editable && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {dirty && (
              <Button
                variant="secondary"
                busy={busy === "edit"}
                disabled={busy !== ""}
                onClick={() =>
                  run("edit", async () => {
                    await api("PATCH", `${base}/drafts/${latest.id}`, { subject: current.subject, body: current.body });
                    setEdits(null);
                  })
                }
              >
                Save edit as new version
              </Button>
            )}
            <Button
              busy={busy === "approve"}
              disabled={busy !== "" || dirty || latest.status === "blocked"}
              title={dirty ? "Save your edit first" : latest.status === "blocked" ? "Edit the draft before approving" : undefined}
              onClick={() => run("approve", () => api("POST", `${base}/drafts/${latest.id}/approve`))}
            >
              <Check className="size-4" aria-hidden /> Approve
            </Button>
            <Button variant="danger" disabled={busy !== ""} onClick={() => setRejecting((v) => !v)}>
              <X className="size-4" aria-hidden /> Reject
            </Button>
          </div>
          {rejecting && (
            <div className="space-y-2">
              <Input placeholder="Reason (optional)" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
              <Button
                variant="danger"
                busy={busy === "reject"}
                onClick={() =>
                  run("reject", async () => {
                    await api("POST", `${base}/drafts/${latest.id}/reject`, { reason });
                    setRejecting(false);
                  })
                }
              >
                Confirm rejection
              </Button>
            </div>
          )}
        </div>
      )}

      {latest.status === "approved" && latest.gmail?.status === "failed" && (
        <Button variant="secondary" busy={busy === "approve"} onClick={() => run("approve", () => api("POST", `${base}/drafts/${latest.id}/approve`))}>
          Retry Gmail draft
        </Button>
      )}
      {latest.status === "rejected" && (
        <Button variant="secondary" busy={busy === "generate"} onClick={generate}>
          Generate a new draft
        </Button>
      )}

      {versions.length > 1 && (
        <p className="text-xs text-stone-500">
          {versions.length} versions kept: {[...versions].sort((a, b) => a.version - b.version).map((v) => `v${v.version} ${DRAFT_STATUS[v.status].label.toLowerCase()}`).join(" → ")}
        </p>
      )}
    </div>
  );
}
