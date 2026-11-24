import type { DraftStatus, Recommendation } from "./types";

export const RECOMMENDATION: Record<Recommendation, { label: string; tone: "good" | "warn" | "bad" | "neutral" }> = {
  contact_now: { label: "Contact now", tone: "good" },
  verify_first: { label: "Verify first", tone: "warn" },
  hold_rival_active: { label: "Hold: rival active", tone: "bad" },
  skip: { label: "Skip", tone: "neutral" },
};

export const DRAFT_STATUS: Record<DraftStatus, { label: string; tone: "good" | "warn" | "bad" | "neutral" | "info" }> = {
  pending_review: { label: "Pending review", tone: "warn" },
  blocked: { label: "Blocked by Reviewer", tone: "bad" },
  approved: { label: "Approved", tone: "good" },
  rejected: { label: "Rejected", tone: "bad" },
  superseded: { label: "Superseded", tone: "neutral" },
  sent: { label: "Sent", tone: "info" },
};

export const SOURCE_TYPE: Record<string, string> = {
  primary: "Company site",
  third_party: "Third-party",
  aggregator: "List / job board",
};

export const GMAIL_STATE: Record<string, string> = {
  created: "Gmail draft created",
  no_recipient: "No verified contact email, so no Gmail draft was created",
  not_connected: "Gmail is not connected",
  failed: "Gmail draft failed; approve again to retry",
};

export function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
