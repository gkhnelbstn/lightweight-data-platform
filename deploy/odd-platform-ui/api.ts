/**
 * The contract-quality service that sits beside this platform.
 *
 * Not generated from `odd-platform-specification`, because it is not this
 * platform's API. It is a small, stable surface and it is typed by hand here so
 * that a change on that side is a compile error rather than a blank panel.
 *
 * The base URL is resolved at runtime rather than baked in by Vite, so one
 * image works in every environment: same host as the platform, port 8077,
 * unless `window.__DQ_API__` says otherwise.
 */

declare global {
  interface Window {
    __DQ_API__?: string;
  }
}

export function dqApiUrl(): string {
  if (window.__DQ_API__) return window.__DQ_API__.replace(/\/$/, '');
  return `${window.location.protocol}//${window.location.hostname}:8077`;
}

export interface ContractSummary {
  id: string;
  title: string;
  owner: string | null;
  domain: string | null;
  source_table: string;
  server_type: string;
  rules: number;
  properties: number;
  score?: string | number | null;
  checks_total?: number | null;
  checks_failed?: number | null;
  checks_errored?: number | null;
  sla_met?: boolean | null;
  sla_min?: string | number | null;
  run_at?: string | null;
}

export interface OpenFailure {
  check_id: string;
  contract_id: string;
  dimension: string;
  failed_rows: number;
  total_rows: number;
  run_at: string;
  name: string | null;
  check_type: string | null;
  field: string | null;
  reason: string | null;
}

export interface Overview {
  trend: { run_at: string; score: string | number }[];
  contracts: ContractSummary[];
  open_failures: OpenFailure[];
  dimensions: string[];
}

export interface QualityRule {
  type?: string;
  description?: string;
  query?: string;
  mustBe?: number;
  dimension?: string;
}

export interface ContractDetail {
  contract: ContractSummary;
  properties: { name: string; classification?: string | null }[];
  rules: QualityRule[];
  checks: {
    check_id: string;
    dimension: string;
    status: string;
    failed_rows: number;
    total_rows: number;
    name: string | null;
    reason: string | null;
  }[];
  file: string;
}

export interface Sample {
  check_id: string;
  name: string | null;
  reason: string | null;
  failed_rows: number | null;
  run_at: string | null;
  scope: string | null;
  sql: string | null;
  columns: string[];
  rows: (string | number | null)[][];
  masked: string[];
  note?: string;
}

export interface RuleDraft {
  contract_id: string;
  description: string;
  query: string;
  dimension: string;
  must_be: number;
}

export interface PreviewResult {
  ok: boolean;
  result?: string;
  reason?: string;
  error?: string;
  failed_rows?: number | null;
  row_count?: number | null;
  compiled_sql?: string | null;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${dqApiUrl()}${path}`, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return body as T;
}

export const getOverview = () => json<Overview>('/api/overview');

export const getContract = (id: string) =>
  json<ContractDetail>(`/api/contracts/${encodeURIComponent(id)}`);

export const getSample = (checkId: string) =>
  json<Sample>(`/api/checks/${encodeURIComponent(checkId)}/sample`);

/** Authoring writes SQL that runs against the source, so it needs the token. */
function authoring(draft: RuleDraft, token: string): RequestInit {
  return {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(draft),
  };
}

export const previewRule = (draft: RuleDraft, token: string) =>
  json<PreviewResult>('/api/rules/preview', authoring(draft, token));

export const saveRule = (draft: RuleDraft, token: string) =>
  json<{ saved: string; file: string }>('/api/rules', authoring(draft, token));
