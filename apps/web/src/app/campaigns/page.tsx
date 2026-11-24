"use client";
import Link from "next/link";
import { Badge, Card, Empty, Notice, Spinner, fmtDate } from "@/components/ui";
import { errorText, useApi } from "@/lib/api";
import type { Campaign } from "@/lib/types";
import { stageHref, stageLabel } from "@/lib/stage";

export default function Campaigns() {
  const { data, error, loading } = useApi<Campaign[]>("campaigns");
  return (
    <div className="space-y-6">
      <h1 className="font-serif text-3xl font-semibold text-stone-900">Campaigns</h1>
      {loading && <Spinner />}
      {error && <Notice>{errorText(error)}</Notice>}
      {data && data.length === 0 && (
        <Empty title="No campaigns yet">
          <Link href="/" className="underline">
            Create the first one
          </Link>
        </Empty>
      )}
      {data && data.length > 0 && (
        <Card>
          <ul className="divide-y divide-stone-100">
            {[...data]
              .sort((a, b) => b.created_at.localeCompare(a.created_at))
              .map((c) => (
                <li key={c.id}>
                  <Link
                    href={stageHref(c)}
                    className="flex items-center justify-between gap-4 py-3 hover:bg-stone-50"
                  >
                    <div>
                      <p className="font-medium text-stone-900">{c.product_name}</p>
                      <p className="text-xs text-stone-500">{fmtDate(c.created_at)}</p>
                    </div>
                    <Badge tone="accent">{stageLabel(c.stage)}</Badge>
                  </Link>
                </li>
              ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
