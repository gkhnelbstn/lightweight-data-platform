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

/** One check's most recent run. `stale` means its contract has run since
 * without it -- the rule was deleted and only its history remains; see
 * CLAUDE.md, "Results outlive checks". */
export interface CheckRow {
  check_id: string;
  contract_id: string;
  dimension: string;
  status: string;
  failed_rows: number;
  total_rows: number;
  fail_ratio: string | number | null;
  run_at: string;
  name: string | null;
  check_type: string | null;
  field: string | null;
  reason: string | null;
  sql: string | null;
  stale: boolean;
  /** What someone said about this check. 'open' when nobody has. */
  state: 'open' | 'acknowledged' | 'accepted';
  note: string | null;
  noted_run_at: string | null;
  noted_at: string | null;
}

export interface CheckRun {
  run_at: string;
  status: string;
  failed_rows: number;
  total_rows: number;
  fail_ratio: string | number | null;
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

export interface ContractProperty {
  name: string;
  logicalType?: string;
  physicalType?: string;
  description?: string;
  required?: boolean;
  unique?: boolean;
  primaryKey?: boolean;
  classification?: string | null;
}

/** Two numbers per column per day. Not a check -- nothing here passes or
 * fails and none of it reaches the score; see core/profile.py. `prev_*` is
 * yesterday's run, which is what makes a rising null fraction visible before
 * it breaks the `field_required` check on the same column. */
export interface ColumnProfile {
  table_name: string;
  column_name: string;
  rows: number;
  nulls: number;
  distinct_count: number;
  run_at: string;
  prev_nulls: number | null;
  prev_rows: number | null;
}

export interface ContractDetail {
  contract: ContractSummary;
  properties: ContractProperty[];
  profile: ColumnProfile[];
  rules: QualityRule[];
  checks: {
    check_id: string;
    dimension: string;
    status: string;
    failed_rows: number;
    total_rows: number;
    run_at: string;
    name: string | null;
    check_type: string | null;
    field: string | null;
    reason: string | null;
    sql: string | null;
  }[];
  history: { run_at: string; check_id: string; status: string; failed_rows: number }[];
  file: string;
  servers: { server: string; type: string }[];
}

export interface AuditEntry {
  change_type: string;
  action: string;
  description: string;
  value: Record<string, unknown>;
  caller_label: string | null;
  run_at: string;
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

/** What one replication pass moved. `upserted` rather than inserted and
 * updated: CDC delivers both as `on conflict do update` and the operation
 * code does not survive the merge -- see core/store.py's sync_runs. */
export interface SyncRun {
  mode: string;
  rows_read: number;
  upserted: number;
  deleted: number;
  /** Source time of the last change this pass applied. The lag that matters
   * for CDC: a pass at 14:05 that applied changes up to 14:02 is 3 minutes
   * behind, and no clock on this side knows that. */
  applied_through: string | null;
  run_at: string;
}

export interface SyncRule {
  contract_id: string;
  title: string;
  engine: string;
  identity: string[];
  rule: { server: string; filter?: string; columns?: string[]; identity?: string[] };
  problems: string[];
  /** Rows on each side. A green line is not a claim anyone can check; "90 of
   * 400 rows" is. */
  arriving?: {
    table: string;
    filter?: string | null;
    source?: number;
    target?: number;
    source_error?: string;
    target_error?: string;
  };
  runs?: SyncRun[];
  status?: {
    engine?: string;
    slot_active?: boolean;
    worker_running?: boolean;
    behind?: string | null;
    /** Tables that never left the initial copy. A live apply worker, an
     * active slot and zero lag say nothing about these -- see issue #35. */
    copying?: string[];
    streaming?: boolean;
    /** A `mode: view` target reads through to the source, so the only status
     * that means anything is whether the query works. ADR 0017. */
    mode?: string;
    reachable?: boolean;
    error?: string;
    last_synced?: string | null;
    unreachable?: string;
  };
}

/** A contract whose table keeps history: the columns that make an interval,
 * the business key that repeats across versions, and how much of it there is.
 * See core/versions.py -- none of this is inferred, the contract declares it. */
export interface VersionedContract {
  contract_id: string;
  title: string;
  table: string;
  key: string;
  attributes: string[];
  versions?: number;
  keys?: number;
  closed?: number;
  earliest?: string | null;
  latest_change?: string | null;
  unreachable?: string;
}

export interface ChangedKey {
  key: string | number;
  versions: number;
  last_changed: string | null;
}

/** One version. The attribute columns are dynamic -- they are whatever the
 * contract declares -- so they arrive alongside the fixed interval fields. */
export interface Version {
  valid_from: string;
  valid_to: string | null;
  is_current: boolean;
  changed: string[];
  [column: string]: unknown;
}

export interface RuleType {
  kind: string;
  dimension: string;
  label: string;
  parameters: { name: string; type: string; label: string }[];
}

export interface StructuredRule {
  contract_id: string;
  kind: string;
  column: string;
  params: Record<string, unknown>;
  dimension?: string;
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

export const getChecks = () => json<CheckRow[]>('/api/checks');

/** Acknowledging writes a note, not a statement, so it carries no token --
 * the guarded routes are guarded because they run SQL someone typed. */
export const setCheckStatus = (
  checkId: string,
  body: { state: CheckRow['state']; note: string; noted_run_at: string | null }
) =>
  json<{ check_id: string; state: string; note: string }>(
    `/api/checks/${encodeURIComponent(checkId)}/status`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }
  );

export const getCheckHistory = (checkId: string) =>
  json<CheckRun[]>(`/api/checks/${encodeURIComponent(checkId)}/history`);

export const getSample = (checkId: string) =>
  json<Sample>(`/api/checks/${encodeURIComponent(checkId)}/sample`);

/** Authoring writes SQL (raw rules) or a predicate (sync rules) that runs
 * against the source, so both need the token. */
function authoring(body: RuleDraft | SyncRuleDraft, token: string): RequestInit {
  return {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  };
}

export const getVersionedContracts = () =>
  json<VersionedContract[]>('/api/versions');

export const getChangedKeys = (contractId: string) =>
  json<{ contract: VersionedContract; summary: VersionedContract; changed: ChangedKey[] }>(
    `/api/versions/${encodeURIComponent(contractId)}`
  );

export const getVersions = (contractId: string, key: string) =>
  json<{ contract: VersionedContract; key: string; versions: Version[] }>(
    `/api/versions/${encodeURIComponent(contractId)}?key=${encodeURIComponent(key)}`
  );

export const getSyncRules = () => json<SyncRule[]>('/api/sync');

export interface SyncRuleDraft {
  contract_id: string;
  server: string;
  filter?: string;
  columns?: string[];
  identity?: string[];
  caller_label?: string;
}

/** filter is a predicate someone typed -- the same risk class as raw SQL, so
 * this route needs the token the same way /api/rules does. */
export const saveSyncRule = (draft: SyncRuleDraft, token: string) =>
  json<{ saved: string; rule: SyncRule['rule']; file: string; plan?: unknown }>(
    '/api/sync/rules',
    authoring(draft, token)
  );

export const getContractAudit = (id: string) =>
  json<AuditEntry[]>(`/api/contracts/${encodeURIComponent(id)}/audit`);

export const getRuleTypes = () =>
  json<{ rules: RuleType[]; dimensions: string[] }>('/api/rules/catalogue');

/**
 * Rules built from the form need no token: the vocabulary is fixed and the SQL
 * is composed by the service, so there is no statement to smuggle in. Only the
 * raw-SQL escape hatch below is guarded.
 */
function structured(rule: StructuredRule): RequestInit {
  return {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(rule),
  };
}

export const previewStructured = (rule: StructuredRule) =>
  json<PreviewResult & { description: string; query: string }>(
    '/api/rules/structured/preview',
    structured(rule)
  );

export const saveStructured = (rule: StructuredRule) =>
  json<{ saved: string; file: string }>('/api/rules/structured', structured(rule));

export const previewRule = (draft: RuleDraft, token: string) =>
  json<PreviewResult>('/api/rules/preview', authoring(draft, token));

export const saveRule = (draft: RuleDraft, token: string) =>
  json<{ saved: string; file: string }>('/api/rules', authoring(draft, token));

/** The two-way integration (#78, ADR 0021): api/integration.py. */
export interface FlowJob {
  status: string | null;
  id: string | null;
  started: string | null;
  finished: string | null;
  error: string | null;
  read: number;
  written: number;
}

export interface IntegrationFlow {
  flow: string;
  match: Record<string, string>;
  job: FlowJob | null;
}

export interface IntegrationSystem {
  table: string;
  system: string;
  title: string;
  in: IntegrationFlow[];
  out: IntegrationFlow[];
  /** Mapped columns the live table or its CDC capture no longer has. */
  drift: string[];
}

export interface Arrival {
  last_hour: number;
  committed_at: string | null;
  landed_at: string | null;
}

export interface HubConflict {
  at: string;
  key: Record<string, unknown>;
  codes: Record<string, unknown> | null;
  field: string;
  kept: unknown;
  kept_by: string | null;
  kept_at: string | null;
  lost: unknown;
  lost_by: string | null;
  lost_at: string | null;
  reason: string;
}

export interface HeldRow {
  system: string;
  local: Record<string, unknown>;
  reason: string;
  row: Record<string, unknown>;
  at: string;
}

export interface Tombstone {
  key: Record<string, unknown>;
  deleted_by: string;
  deleted_at: string | null;
  codes: Record<string, unknown> | null;
}

export interface Hub {
  id: string;
  title: string;
  authority: string;
  codes: Record<string, string>;
  systems: IntegrationSystem[];
  hub_error?: string;
  records?: number;
  arriving?: Record<string, Arrival>;
  conflicts?: HubConflict[];
  held?: HeldRow[];
  deleted?: Tombstone[];
}

/** A one-way aggregate (#81): many rows summed into one, per group. */
export interface Totals {
  flow: string;
  from: string;
  to: string;
  group: Record<string, string>;
  aggregates: Record<string, string>;
  jobs: { in: FlowJob | null; out: FlowJob | null };
  lines?: number;
  groups?: number;
  landed_at?: string | null;
  error?: string;
}

export interface IntegrationState {
  hubs: Hub[];
  totals?: Totals[];
  problems: string[];
  seatunnel_error: string | null;
}

export const getIntegration = () => json<IntegrationState>('/api/integration');

/** One record, or one held row, opened from the Integration tab:
 * api/integration_detail.py. */
export interface HistoryEntry {
  at: string;
  committed_at: string | null;
  system: string;
  /** What the hub did with it: applied, echo, lost, held, created, deleted,
   * unchanged. Null for rows that reached the hub before it was recorded. */
  outcome: string | null;
  kind: 'insert' | 'update' | 'delete';
  changes: { field: string; from: unknown; to: unknown }[];
}

export interface RecordDetail {
  key: Record<string, unknown>;
  codes: Record<string, unknown>;
  fields: Record<string, { value: unknown; by: string | null; at: string | null }>;
  deleted: { at: string | null; by: string } | null;
  history: HistoryEntry[];
  conflicts: HubConflict[];
}

export interface HeldDetail {
  reason: string;
  at: string;
  row: Record<string, unknown>;
  rule: Record<string, unknown>;
  candidates: {
    key: Record<string, unknown>;
    codes: Record<string, unknown>;
    fields: Record<string, unknown>;
    link: string;
  }[];
  link_new: string;
}

export const getRecord = (hub: string, key: Record<string, unknown>) =>
  json<RecordDetail>(
    `/api/integration/record?hub=${encodeURIComponent(hub)}&key=${encodeURIComponent(JSON.stringify(key))}`
  );

export const getHeld = (hub: string, system: string, local: Record<string, unknown>) =>
  json<HeldDetail>(
    `/api/integration/held?hub=${encodeURIComponent(hub)}&system=${encodeURIComponent(system)}` +
      `&local=${encodeURIComponent(JSON.stringify(local))}`
  );
