// Shapes returned by the GTMOS API (envelope already unwrapped by lib/api.ts).

export type Stage = "intake" | "icp" | "icp_approved" | "discovery" | "leads_ready";

export interface Campaign {
  id: string;
  product_name: string;
  stage: Stage;
  created_at: string;
  criteria: {
    product_name: string;
    description: string;
    geography: string;
    customer_type: string;
    pricing_notes: string;
    exclusions: string;
  };
}

export interface IcpFields {
  industries: string[];
  company_size: { min_employees: number; max_employees: number };
  buyer_personas: { title: string; rationale: string }[];
  buying_signals: string[];
  exclusions: string[];
  reasoning_summary: string;
  confidence_by_field: Record<string, number>;
}

export interface Icp {
  id: string;
  version: number;
  fields: IcpFields;
  source: "model" | "human";
  edited_fields: string[];
  approved_at: string | null;
  llm: { provider: string; model: string; fallback_reason: string | null };
}

export interface Evidence {
  quote: string;
  scored: boolean;
  signal: string;
  source_id: string;
  source_type: string;
  title: string;
  url: string;
}

export interface ScoreComponent {
  max: number;
  points: number;
  reason: string;
}

export interface Contact {
  email: string;
  first_name: string | null;
  last_name: string | null;
  position: string | null;
  seniority: string | null;
  verification_status: string | null;
  confidence: number | null;
  linkedin: string | null;
}

export interface Lead {
  id: string;
  company_name: string;
  domain: string;
  fit_score: number;
  tier: "A" | "B" | "C";
  fit_reason: string;
  status: string;
  verification_status: string;
  employees?: number | null;
  employees_source?: string;
  evidence: Evidence[];
  source_urls: string[];
  competitors: { name: string; source_id?: string; quote?: string; url?: string }[];
  score_breakdown: Record<string, ScoreComponent>;
  industry_match: string;
  geography_match: string;
  contacts?: Contact[];
  enrichment?: { status: string; enriched_at: string; usable_contacts: number };
}

export interface GraphNode {
  id: string;
  label: string;
  name: string;
  fit_score?: number;
  tier?: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
}

export interface GraphView {
  nodes: GraphNode[];
  edges: GraphEdge[];
  counts: { nodes: number; edges: number };
  source: string;
}

export type Recommendation = "contact_now" | "verify_first" | "hold_rival_active" | "skip";

export interface Decision {
  company_id: string;
  lead_id: string;
  name: string;
  domain: string;
  tier: string;
  fit_score: number;
  baseline: Recommendation;
  recommendation: Recommendation;
  changed: boolean;
  reasons: string[];
  hook: { signal: string; quote: string; url: string; source_type: string | null } | null;
  peers: string[];
  rivals: { name: string; outreach_status: string | null }[];
}

export interface Decisions {
  decisions: Decision[];
  source: string;
  summary: {
    leads: number;
    changed: number;
    contact_now: number;
    verify_first: number;
    hold_rival_active: number;
    skip: number;
  };
}

export type DraftStatus = "pending_review" | "blocked" | "approved" | "rejected" | "superseded" | "sent";

export interface Draft {
  id: string;
  lead_id: string;
  company_name: string;
  domain: string;
  version: number;
  status: DraftStatus;
  subject: string;
  body: string;
  source: "model" | "human";
  facts: { id: number; quote: string; signal: string; source_type: string; title: string; url: string }[];
  fact_ids: number[];
  recipient: {
    email: string | null;
    name: string | null;
    role: string | null;
    source: string;
    verification_status: string | null;
  };
  reviewer: {
    status: "passed" | "edited" | "blocked";
    removed: { sentence: string; reason: string; by: string }[];
    flags: string[];
  };
  edit_warnings?: { where: string; sentence: string; reason: string }[];
  gmail?: { status: "created" | "no_recipient" | "not_connected" | "failed"; error_code?: string; draft_id?: string };
  approved_by?: string;
  approved_at?: string;
  rejected_at?: string;
  rejection_reason?: string;
  created_at: string;
}

export interface Billing {
  configured: boolean;
  costs: Record<string, number>;
  wallet?: { balance: string; status: string } | null;
  error?: string;
}

export type OrchStatus = "queued" | "planning" | "researching" | "reviewing" | "awaiting_approval" | "failed" | "abstained";

export interface OrchEvent {
  agent: "planner" | "researcher" | "reviewer";
  kind: string;
  at: string;
  summary: string;
  thread_id?: string | null;
  step_id?: string | null;
  mentioned?: string | null;
}

export interface Orchestration {
  id: string;
  lead_id: string;
  lead_name: string;
  status: OrchStatus;
  thread_id?: string;
  draft_id?: string;
  trace: OrchEvent[];
  created_at: string;
  updated_at: string;
}

export interface AuditEvent {
  id: string;
  action: string;
  actor: string;
  entity_id: string;
  occurred_at: string;
  details: Record<string, unknown>;
  operation_id: string;
}

export interface Resolution {
  lead_id: string;
  outcome: "resolved" | "abstain";
  before: Recommendation;
  after: Recommendation;
  attempts: number;
  reasons: string[];
  steps: { fact: string; found: boolean; detail: string }[];
  brief: { known: string[]; uncertain: string[]; would_make_actionable: string[] };
}
