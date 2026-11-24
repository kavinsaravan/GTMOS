"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Rail } from "@/components/rail";
import { Button, Card, Field, Input, Notice, Textarea } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import type { Campaign } from "@/lib/types";

export default function NewCampaign() {
  const router = useRouter();
  const [busy, setBusy] = useState<"" | "saving" | "profiling">("");
  const [error, setError] = useState("");
  const [created, setCreated] = useState<string | null>(null);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = Object.fromEntries(
      ["product_name", "description", "geography", "customer_type", "pricing_notes", "exclusions"].map((k) => [
        k,
        String(f.get(k) ?? "").trim(),
      ]),
    );
    setError("");
    try {
      let id = created;
      if (!id) {
        setBusy("saving");
        id = (await api<Campaign>("POST", "campaigns", body)).id;
        setCreated(id); // a retry after an ICP failure reuses the saved campaign
      }
      setBusy("profiling");
      await api("POST", `campaigns/${id}/generate-icp`);
      router.push(`/campaigns/${id}/icp`);
    } catch (err) {
      setError(errorText(err));
      setBusy("");
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <Rail current={0} />
      <div className="space-y-3">
        <h1 className="font-serif text-5xl font-semibold tracking-tight text-stone-900">Find your next customers.</h1>
        <p className="max-w-xl text-lg text-stone-600">
          Describe what you sell. GTMOS builds a customer profile, finds companies with evidence they fit, and drafts
          outreach that you approve before anything leaves the building.
        </p>
      </div>

      <Card>
        <form onSubmit={submit} className="space-y-5" aria-busy={busy !== ""}>
          <Field label="Product name" required>
            <Input name="product_name" required maxLength={120} placeholder="Pipeline Guard" disabled={!!created} />
          </Field>
          <Field label="What does it do, and for whom?" required hint="One paragraph. Up to 4000 characters.">
            <Textarea
              name="description"
              required
              rows={5}
              maxLength={4000}
              placeholder="AI code review for platform engineering teams that…"
              disabled={!!created}
            />
          </Field>
          <Field label="Target geography" hint="e.g. US, DACH, global">
            <Input name="geography" maxLength={200} placeholder="US" disabled={!!created} />
          </Field>
          <details className="rounded-lg border border-stone-200 px-4 py-3">
            <summary className="cursor-pointer text-sm font-medium text-stone-700">Optional details</summary>
            <div className="mt-4 space-y-5">
              <Field label="Customer type">
                <Input name="customer_type" maxLength={300} placeholder="B2B software companies" disabled={!!created} />
              </Field>
              <Field label="Pricing notes">
                <Input name="pricing_notes" maxLength={500} disabled={!!created} />
              </Field>
              <Field label="Exclusions" hint="Companies or segments never to target.">
                <Input name="exclusions" maxLength={1000} placeholder="agencies, consultancies" disabled={!!created} />
              </Field>
            </div>
          </details>
          {error && <Notice>{error}</Notice>}
          <Button type="submit" busy={busy !== ""} className="w-full sm:w-auto">
            {busy === "saving" ? "Saving campaign" : busy === "profiling" ? "Generating customer profile" : "Generate Customer Profile"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
