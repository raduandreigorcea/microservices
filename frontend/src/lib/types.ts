/** Mirrors the pydantic response models the backend serves. */

export type JobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type JobMode = "idno_list" | "sweep";

export const FINAL_STATUSES: readonly JobStatus[] = [
  "succeeded",
  "failed",
  "cancelled",
];

export interface Job {
  id: string;
  mode: JobMode;
  status: JobStatus;
  params: Record<string, unknown>;
  cursor: Record<string, unknown>;
  companies_done: number;
  companies_total: number | null;
  requests_ok: number;
  requests_failed: number;
  rows_loaded: number;
  last_error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Person {
  role: string;
  party_type: string;
  full_name: string;
  share_percent: string | null;
}

export interface StatementSummary {
  year: number;
  source: string;
  status: string | null;
  origin: string | null;
  period_from: string | null;
  period_to: string | null;
  is_audited: boolean | null;
}

export interface LineItem {
  group_code: string;
  group_name: string | null;
  field_code: string;
  label: string | null;
  value_current: string | null;
  value_previous: string | null;
  value_extra_1: string | null;
  value_extra_2: string | null;
}

export interface Statement extends StatementSummary {
  source_ref: string | null;
  declaration_date: string | null;
  entity_name: string | null;
  line_items: LineItem[];
}

export interface CompanySummary {
  idno: string;
  name: string | null;
  legal_form: string | null;
  is_active: boolean | null;
  registered_at: string | null;
  city: string | null;
  caem_code: string | null;
  sources: string[] | null;
  transformed_at: string;
}

export interface Company extends CompanySummary {
  revision_date: string | null;
  address: string | null;
  street: string | null;
  postal_code: string | null;
  cuatm_code: string | null;
  cuatm_name: string | null;
  caem_name: string | null;
  ownership_name: string | null;
  cuiio: string | null;
  email: string | null;
  phone: string | null;
  website: string | null;
  employees: number | null;
  in_liquidation: boolean | null;
  licensed_activities: unknown[] | null;
  unlicensed_activities: unknown[] | null;
  people: Person[];
  statements: StatementSummary[];
}

export interface CompanyPage {
  items: CompanySummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface SourceData {
  id: string;
  source: string;
  resource: string;
  resource_key: string;
  request_url: string;
  http_status: number;
  content_type: string | null;
  body_sha256: string;
  first_seen_at: string;
  last_seen_at: string;
}

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  picture_url: string | null;
  role: string;
  is_active: boolean;
  email_verified: boolean;
  created_at: string;
}


export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  scope: string;
}

// --- ownership graph --------------------------------------------------------

export type NodeKind = "company" | "person" | "company_party";

export interface GraphNode {
  id: string;
  kind: NodeKind;
  label: string;
  idno: string | null;
  role: string | null;
  degree: number;
}

export interface GraphLink {
  source: string;
  target: string;
  role: string;
  share_percent: string | null;
}

export interface Graph {
  nodes: GraphNode[];
  links: GraphLink[];
  truncated: boolean;
  /** How many hops out the walk went. */
  depth: number;
  /** `neo4j` normally. `postgres` means the graph store was unreachable and
   *  the one-hop SQL query stood in. */
  source: "neo4j" | "postgres";
}
