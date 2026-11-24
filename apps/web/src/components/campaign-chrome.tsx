"use client";
import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Rail } from "./rail";
import { Notice, Spinner } from "./ui";
import { errorText, useApi } from "@/lib/api";
import { railIndex } from "@/lib/stage";
import type { Campaign } from "@/lib/types";

export function CampaignChrome({ id, children }: { id: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const { data: campaign, error } = useApi<Campaign>(`campaigns/${id}`);
  const base = `/campaigns/${id}`;
  const tabs = [
    { href: `${base}/icp`, label: "Customer profile" },
    { href: `${base}/leads`, label: "Leads" },
    { href: `${base}/dashboard`, label: "Command center" },
  ];
  const onDashboard = pathname.endsWith("/dashboard");
  const step = onDashboard ? 4 : campaign ? railIndex(campaign.stage) : 1;

  if (error) return <Notice>{errorText(error)}</Notice>;
  return (
    <div className="space-y-6">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-stone-500">Campaign</p>
            <h1 className="font-serif text-3xl font-semibold text-stone-900">
              {campaign?.product_name ?? <span className="text-stone-400">Loading…</span>}
            </h1>
          </div>
          <Rail current={step} />
        </div>
        <nav className="flex gap-1 border-b border-stone-200" aria-label="Campaign sections">
          {tabs.map((t) => {
            const active = pathname === t.href || pathname.startsWith(`${t.href}/`);
            return (
              <Link
                key={t.href}
                href={t.href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
                  active
                    ? "border-stone-900 text-stone-900"
                    : "border-transparent text-stone-500 hover:text-stone-800",
                )}
              >
                {t.label}
              </Link>
            );
          })}
        </nav>
      </div>
      {campaign ? children : <Spinner />}
    </div>
  );
}
