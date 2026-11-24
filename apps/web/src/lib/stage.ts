import type { Campaign, Stage } from "./types";

const LABEL: Record<Stage, string> = {
  intake: "Intake",
  icp: "ICP review",
  icp_approved: "ICP approved",
  discovery: "Discovering",
  leads_ready: "Leads ready",
};

export const stageLabel = (s: Stage) => LABEL[s] ?? s;

/** Rail index (Intake, ICP, Discover, Review, Track) for a campaign stage. */
export function railIndex(s: Stage): number {
  switch (s) {
    case "intake":
      return 1;
    case "icp":
      return 1;
    case "icp_approved":
    case "discovery":
      return 2;
    default:
      return 3;
  }
}

export function stageHref(c: Pick<Campaign, "id" | "stage">): string {
  if (c.stage === "leads_ready" || c.stage === "discovery") return `/campaigns/${c.id}/leads`;
  return `/campaigns/${c.id}/icp`;
}
