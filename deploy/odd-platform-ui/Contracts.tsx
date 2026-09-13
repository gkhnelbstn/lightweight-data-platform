import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { MenuItem, Typography } from '@mui/material';
import {
  AppSelect,
  AppTabs,
  Button,
  EmptyContentPlaceholder,
  Input,
  LabeledInfoItem,
  Table,
  TestRunStatusItem,
} from 'components/shared/elements';
import { DataEntityRunStatus } from 'generated-sources';
import type {
  AuditEntry,
  ContractDetail,
  ContractProperty,
  Overview,
  PreviewResult,
  RuleDraft,
  RuleType,
  Sample,
  StructuredRule,
  SyncRule,
  SyncRuleDraft,
} from './api';
import {
  getContract,
  getContractAudit,
  getOverview,
  getRuleTypes,
  getSample,
  getSyncRules,
  previewRule,
  previewStructured,
  saveRule,
  saveStructured,
  saveSyncRule,
} from './api';

/** A preview, whichever route produced it. */
type PreviewShape = PreviewResult & { description?: string; query?: string };
import * as S from './Contracts.styles';

/**
 * Contracts, their rules, and the rows a rule failed on.
 *
 * This platform reports quality and does not let anyone change it: there is no
 * "create test" anywhere in the UI, because a test arrives through ingestion
 * and belongs to whatever produced it. The contract behind these tests is
 * editable, though, and this is where that belongs -- next to the dashboard
 * that says a check failed, rather than on another port.
 *
 * Everything here talks to the contract service; see ./api.ts. Saving a rule
 * writes it back into the contract file and re-runs it, so the numbers above
 * change on the next refresh. Built from `components/shared/elements` --
 * this platform's own design system -- rather than bare HTML controls, so
 * this panel looks like it belongs on the page it lives on.
 */

const fmt = (v: unknown) =>
  v === null || v === undefined ? '—' : Number(v).toFixed(3);

/** Our three check outcomes onto the four this platform already has icons,
 * colors and a legend for. 'error' (could not run) reads as BROKEN, not
 * FAILED -- CLAUDE.md invariant 5: a check that could not run is not one
 * that failed. */
const runStatus = (status: string): DataEntityRunStatus => {
  if (status === 'pass') return DataEntityRunStatus.SUCCESS;
  if (status === 'error') return DataEntityRunStatus.BROKEN;
  return DataEntityRunStatus.FAILED;
};

type SortKey = 'title' | 'score' | 'tests';

export const Contracts: React.FC = () => {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ContractDetail | null>(null);
  const [samples, setSamples] = useState<Record<string, Sample | string>>({});
  const [ruleTypes, setRuleTypes] = useState<RuleType[]>([]);
  const [filterText, setFilterText] = useState('');
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' } | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getOverview()
      .then(setOverview)
      .catch((e: Error) => setError(e.message));
    // The rule vocabulary belongs to the service; fetching it means adding a
    // rule kind is a change in one place rather than two.
    getRuleTypes()
      .then(r => setRuleTypes(r.rules))
      .catch(() => setRuleTypes([]));
  }, []);

  const select = useCallback((id: string) => {
    setSelected(prev => (prev === id ? null : id));
    setDetail(null);
    if (id) getContract(id).then(setDetail).catch(() => setDetail(null));
  }, []);

  // Client-side: eleven contracts today, and the backend keeps none of ODD's
  // own tsvector search machinery for our own YAML files. Revisit if the
  // count ever grows past what scanning in the browser can do instantly.
  const visibleContracts = useMemo(() => {
    const needle = filterText.trim().toLowerCase();
    let rows = !needle
      ? overview?.contracts ?? []
      : (overview?.contracts ?? []).filter(
          c =>
            c.title.toLowerCase().includes(needle) ||
            c.id.toLowerCase().includes(needle) ||
            c.source_table?.toLowerCase().includes(needle)
        );
    if (sort) {
      rows = [...rows].sort((a, b) => {
        const va =
          sort.key === 'title'
            ? a.title
            : sort.key === 'score'
              ? (Number(a.score) ?? -1)
              : (a.checks_total ?? -1);
        const vb =
          sort.key === 'title'
            ? b.title
            : sort.key === 'score'
              ? (Number(b.score) ?? -1)
              : (b.checks_total ?? -1);
        const cmp = va < vb ? -1 : va > vb ? 1 : 0;
        return sort.dir === 'asc' ? cmp : -cmp;
      });
    }
    return rows;
  }, [overview?.contracts, filterText, sort]);

  const toggleSort = useCallback((key: SortKey) => {
    setSort(prev =>
      prev?.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }
    );
  }, []);

  // A click opens the panel far below a long list; without this, "select a
  // contract" and "see what you selected" can be two screens apart.
  useEffect(() => {
    if (selected && detail) panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [selected, detail]);

  const showRows = useCallback(async (checkId: string) => {
    if (samples[checkId]) {
      setSamples(({ [checkId]: _drop, ...rest }) => rest);
      return;
    }
    try {
      // Awaited before the updater, not inside it: a state updater is not an
      // async function and `tsc` says so.
      const sample = await getSample(checkId);
      setSamples(prev => ({ ...prev, [checkId]: sample }));
    } catch (e) {
      setSamples(prev => ({ ...prev, [checkId]: (e as Error).message }));
    }
  }, [samples]);

  const reload = useCallback(() => {
    getOverview().then(setOverview).catch(() => undefined);
    if (selected) getContract(selected).then(setDetail).catch(() => undefined);
  }, [selected]);

  if (error) {
    return (
      <Typography variant='body1' color='texts.secondary'>
        Contract service unreachable: {error}
      </Typography>
    );
  }
  if (!overview) {
    return (
      <Typography variant='body2' color='texts.secondary'>
        Loading contracts…
      </Typography>
    );
  }

  return (
    <>
      <Typography variant='h4'>Contract quality over time</Typography>
      <Trend points={overview.trend} />

      <S.Actions>
        <Typography variant='h4'>Contracts</Typography>
        <Input
          variant='search-lg'
          placeholder='Filter by name, id or source table'
          value={filterText}
          onChange={e => setFilterText(e.target.value)}
          handleCleanUp={() => setFilterText('')}
        />
      </S.Actions>
      <Typography variant='subtitle2' color='texts.secondary'>
        The tests above are derived from these. Select one to see its rules, add
        another, or open the rows a check failed on.
        {filterText && ` Showing ${visibleContracts.length} of ${overview.contracts.length}.`}
      </Typography>

      <div>
        <Table.HeaderContainer>
          <Table.Cell $flex={2.2}>
            <S.SortableHeader onClick={() => toggleSort('title')}>
              Contract{sort?.key === 'title' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
            </S.SortableHeader>
          </Table.Cell>
          <Table.Cell $flex={1.6}>
            <Typography variant='caption'>Source</Typography>
          </Table.Cell>
          <Table.Cell $flex={0.8} $justifyContent='flex-end'>
            <S.SortableHeader onClick={() => toggleSort('score')}>
              Score{sort?.key === 'score' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
            </S.SortableHeader>
          </Table.Cell>
          <Table.Cell $flex={0.8} $justifyContent='flex-end'>
            <Typography variant='caption'>SLA</Typography>
          </Table.Cell>
          <Table.Cell $flex={1.8}>
            <S.SortableHeader onClick={() => toggleSort('tests')}>
              Tests{sort?.key === 'tests' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
            </S.SortableHeader>
          </Table.Cell>
        </Table.HeaderContainer>
        {visibleContracts.length === 0 && (
          <Typography variant='body2' color='texts.secondary' sx={{ py: 2 }}>
            No contract matches &quot;{filterText}&quot;.
          </Typography>
        )}
        {visibleContracts.map(c => (
          <Table.RowContainer
            key={c.id}
            role='button'
            tabIndex={0}
            aria-expanded={selected === c.id}
            onClick={() => select(c.id)}
            onKeyDown={e => {
              // A row is a toggle, not a link -- Space and Enter both open it,
              // the way a real <button> would; Space's default (page scroll)
              // is the one thing worth preventing.
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                select(c.id);
              }
            }}
            sx={{
              cursor: 'pointer',
              backgroundColor: theme =>
                selected === c.id ? theme.palette.backgrounds.secondary : 'transparent',
              '&:focus-visible': { outline: '2px solid currentColor', outlineOffset: '-2px' },
            }}
          >
            <Table.Cell $flex={2.2}>
              <div>
                <Typography variant='body1'>{c.title}</Typography>
                <Typography variant='caption' color='texts.secondary'>
                  {c.id}
                </Typography>
              </div>
            </Table.Cell>
            <Table.Cell $flex={1.6}>
              <Typography variant='body2'>
                {c.source_table} ({c.server_type})
              </Typography>
            </Table.Cell>
            <Table.Cell $flex={0.8} $justifyContent='flex-end'>
              <Typography
                variant='h4'
                color={c.sla_met === false ? 'error.main' : 'success.main'}
              >
                {fmt(c.score)}
              </Typography>
            </Table.Cell>
            <Table.Cell $flex={0.8} $justifyContent='flex-end'>
              <Typography variant='body2' color='texts.secondary'>
                ≥ {fmt(c.sla_min)}
              </Typography>
            </Table.Cell>
            <Table.Cell $flex={1.8}>
              {c.checks_total == null ? (
                <Typography variant='body2' color='texts.secondary'>
                  —
                </Typography>
              ) : (
                <S.Actions>
                  <TestRunStatusItem
                    size='small'
                    typeName={DataEntityRunStatus.SUCCESS}
                    count={c.checks_total - (c.checks_failed ?? 0) - (c.checks_errored ?? 0)}
                  />
                  {!!c.checks_failed && (
                    <TestRunStatusItem
                      size='small'
                      typeName={DataEntityRunStatus.FAILED}
                      count={c.checks_failed}
                    />
                  )}
                  {!!c.checks_errored && (
                    <TestRunStatusItem
                      size='small'
                      typeName={DataEntityRunStatus.BROKEN}
                      count={c.checks_errored}
                    />
                  )}
                </S.Actions>
              )}
            </Table.Cell>
          </Table.RowContainer>
        ))}
      </div>

      {selected && detail && (
        <div ref={panelRef}>
          {/* key remounts the panel on contract change -- RuleBuilder,
              SyncRuleForm and friends seed a control from `detail` in
              useState's initialiser, which only runs once per mount and
              would otherwise carry the previous contract's picks over. */}
          <ContractPanel
            key={detail.contract.id}
            detail={detail}
            dimensions={overview.dimensions}
            ruleTypes={ruleTypes}
            onSaved={reload}
          />
        </div>
      )}

      {overview.open_failures.length > 0 && (
        <S.Panel>
          <Typography variant='h4'>Open failures</Typography>
          {overview.open_failures.map(f => {
            const sample = samples[f.check_id];
            return (
              <div key={f.check_id}>
                <S.Actions>
                  <TestRunStatusItem size='small' typeName={DataEntityRunStatus.FAILED} count={1} />
                  <Typography variant='body1'>
                    {f.name ?? f.check_id}
                  </Typography>
                  <Typography variant='caption' color='texts.secondary'>
                    {f.dimension} · {f.failed_rows}
                    {f.total_rows ? `/${f.total_rows}` : ''} rows
                  </Typography>
                  <Button
                    buttonType='tertiary-sm'
                    text={sample ? 'Hide rows' : 'Show rows'}
                    onClick={() => showRows(f.check_id)}
                  />
                </S.Actions>
                {f.reason && (
                  <Typography variant='caption' color='texts.secondary'>
                    {f.reason}
                  </Typography>
                )}
                {typeof sample === 'string' && (
                  <Typography variant='caption' color='error.main'>
                    {sample}
                  </Typography>
                )}
                {sample && typeof sample !== 'string' && <Rows sample={sample} />}
              </div>
            );
          })}
        </S.Panel>
      )}

      <SyncRules />
    </>
  );
};

const Rows: React.FC<{ sample: Sample }> = ({ sample }) => (
  <S.Panel>
    <Typography variant='caption' color='texts.secondary'>
      {sample.run_at} · scope {sample.scope} · {sample.failed_rows} failing rows
      {sample.masked.length > 0 && ` · masked: ${sample.masked.join(', ')}`}
    </Typography>
    {sample.note ? (
      <Typography variant='body2'>{sample.note}</Typography>
    ) : sample.rows.length === 0 ? (
      <Typography variant='body2'>No rows in this window.</Typography>
    ) : (
      <S.Scroll>
        <S.Cells>
          <thead>
            <tr>
              {sample.columns.map((c, i) => (
                // eslint-disable-next-line react/no-array-index-key
                <th key={`${c}-${i}`}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sample.rows.map((row, ri) => (
              // eslint-disable-next-line react/no-array-index-key
              <tr key={ri}>
                {row.map((v, ci) => (
                  // eslint-disable-next-line react/no-array-index-key
                  <td key={ci}>{v === null ? 'null' : String(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </S.Cells>
      </S.Scroll>
    )}
    {sample.sql && <S.Sql>{sample.sql}</S.Sql>}
  </S.Panel>
);

/**
 * The daily score, as a line.
 *
 * ODD's own Data Quality page counts tests; it has nowhere to put a weighted
 * score over time, because its run model has no numeric field. This is the
 * one chart that was only on the standalone page, and the reason that page
 * could not simply be deleted until now.
 */
const Trend: React.FC<{ points: { run_at: string; score: string | number }[] }> = ({
  points,
}) => {
  if (points.length < 2) return null;
  const w = 420;
  const h = 72;
  const values = points.map(p => Number(p.score));
  const lo = Math.min(...values) - 0.01;
  const hi = 1;
  const x = (i: number) => 2 + (i * (w - 4)) / (points.length - 1);
  const y = (v: number) => h - 4 - ((v - lo) / Math.max(0.0001, hi - lo)) * (h - 12);
  const path = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const last = values[values.length - 1] ?? 0;

  return (
    <S.Actions>
      <div>
        <Typography variant='h1'>{last.toFixed(3)}</Typography>
        <Typography variant='caption' color='texts.secondary'>
          {points.length} days · dimension-weighted
        </Typography>
      </div>
      {/* Decorative: the number and day count beside it already say what this
          shows. Per-point detail lives in the title tooltips, mouse-only --
          the concise summary text is the accessible fallback, not the SVG. */}
      <svg
        viewBox={`0 0 ${w} ${h}`}
        width='100%'
        height={h}
        style={{ maxWidth: w }}
        aria-hidden='true'
      >
        <path d={path} fill='none' stroke='currentColor' strokeWidth='1.6' opacity={0.7} />
        {values.map((v, i) =>
          v < 0.95 ? (
            // eslint-disable-next-line react/no-array-index-key
            <circle key={i} cx={x(i)} cy={y(v)} r='2.4' fill='currentColor'>
              <title>{`${points[i]?.run_at}: ${v.toFixed(3)}`}</title>
            </circle>
          ) : null
        )}
      </svg>
    </S.Actions>
  );
};

/**
 * The replication rules, and whether they are actually running.
 *
 * A dead apply worker and a quiet one look identical from the outside, which
 * is the whole reason this reports `slot_active` and `worker_running` for
 * Postgres, and `last_synced` (core/sync_mssql.py's own watermark) for CDC --
 * a rule sitting there with nothing behind it should not look the same as one
 * actually moving rows.
 */
const SyncRules: React.FC = () => {
  const [rows, setRows] = useState<SyncRule[] | null>(null);

  useEffect(() => {
    getSyncRules().then(setRows).catch(() => setRows([]));
  }, []);

  if (!rows || rows.length === 0) return null;

  return (
    <>
      <Typography variant='h4'>Replication</Typography>
      <Typography variant='subtitle2' color='texts.secondary'>
        The contract says where its table is replicated to; the engine is the
        database&apos;s own. Nothing of ours sits in the stream.
      </Typography>
      <div>
        <Table.HeaderContainer>
          <Table.Cell $flex={1.6}>
            <Typography variant='caption'>Contract</Typography>
          </Table.Cell>
          <Table.Cell $flex={1}>
            <Typography variant='caption'>Target</Typography>
          </Table.Cell>
          <Table.Cell $flex={2}>
            <Typography variant='caption'>Rule</Typography>
          </Table.Cell>
          <Table.Cell $flex={1}>
            <Typography variant='caption'>Identity</Typography>
          </Table.Cell>
          <Table.Cell $flex={1.6}>
            <Typography variant='caption'>State</Typography>
          </Table.Cell>
        </Table.HeaderContainer>
        {rows.map(r => {
          const status = r.status ?? {};
          const isCdc = !!status.engine && status.engine !== 'logical replication';
          const streaming = status.worker_running === true && status.slot_active === true;
          return (
            <Table.RowContainer key={r.contract_id}>
              <Table.Cell $flex={1.6}>
                <div>
                  <Typography variant='body1'>{r.title}</Typography>
                  <Typography variant='caption' color='texts.secondary'>
                    {r.contract_id}
                  </Typography>
                </div>
              </Table.Cell>
              <Table.Cell $flex={1}>
                <Typography variant='body2'>{r.rule.server}</Typography>
              </Table.Cell>
              <Table.Cell $flex={2}>
                <div>
                  <Typography variant='body2'>{r.rule.filter ?? 'everything'}</Typography>
                  <Typography variant='caption' color='texts.secondary'>
                    {(r.rule.columns ?? ['all columns']).join(', ')}
                  </Typography>
                </div>
              </Table.Cell>
              <Table.Cell $flex={1}>
                <Typography variant='body2'>{(r.identity ?? []).join(', ')}</Typography>
              </Table.Cell>
              <Table.Cell $flex={1.6}>
                <div>
                  {isCdc ? (
                    <Typography
                      variant='body2'
                      color={status.last_synced ? 'success.main' : 'texts.secondary'}
                    >
                      {status.engine} CDC ·{' '}
                      {status.last_synced
                        ? `synced ${new Date(status.last_synced).toLocaleString()}`
                        : 'never synced'}
                    </Typography>
                  ) : (
                    <Typography
                      variant='body2'
                      color={streaming ? 'success.main' : 'texts.secondary'}
                    >
                      {streaming ? `streaming · ${status.behind ?? ''} behind` : 'not applied'}
                    </Typography>
                  )}
                  {(r.problems ?? []).map(p => (
                    <Typography key={p} variant='caption' color='error.main' component='div'>
                      {p}
                    </Typography>
                  ))}
                </div>
              </Table.Cell>
            </Table.RowContainer>
          );
        })}
      </div>
    </>
  );
};

interface PanelProps {
  detail: ContractDetail;
  dimensions: string[];
  ruleTypes: RuleType[];
  onSaved: () => void;
}

/** name, type, the identity/uniqueness markers a version-carrying dimension
 * like Type 2 SCD depends on, its classification, and the contract's own
 * description of it -- schema.yaml already writes these in full for the
 * columns that matter (dwh_dim_customer.odcs.yaml explains valid_from,
 * valid_to and is_current at length); this is the first place any of it
 * was shown rather than only read from the file. */
const Definitions: React.FC<{ properties: ContractProperty[] }> = ({ properties }) => {
  if (properties.length === 0) return null;
  const flags = (p: ContractProperty) =>
    [p.primaryKey && 'primary key', p.unique && 'unique', p.required && 'required']
      .filter(Boolean)
      .join(' · ');

  return (
    <div>
      <Typography variant='h4'>Definitions</Typography>
      {properties.map(p => (
        <S.PropertyRow key={p.name}>
          <LabeledInfoItem label={p.name} labelWidth={3}>
            {p.logicalType ?? p.physicalType ?? '—'}
            {flags(p) && ` · ${flags(p)}`}
            {p.classification && ` · classified: ${p.classification}`}
          </LabeledInfoItem>
          {p.description && (
            <Typography variant='caption' color='texts.secondary'>
              {p.description}
            </Typography>
          )}
        </S.PropertyRow>
      ))}
    </div>
  );
};

/** history for one check, against the runs that are actually its own --
 * fetched by /api/contracts/{id} and, before this, never rendered anywhere. */
const CheckHistory: React.FC<{ checkId: string; history: ContractDetail['history'] }> = ({
  checkId,
  history,
}) => {
  const runs = history.filter(h => h.check_id === checkId);
  if (runs.length < 2) return null;
  const passed = runs.filter(r => r.status === 'pass').length;
  return (
    <Typography variant='caption' color='texts.secondary'>
      {passed}/{runs.length} runs passed
    </Typography>
  );
};

/** Every check this contract has -- schema-derived and custom rules alike --
 * with its current status and how often it has passed. `detail.rules` above
 * shows only the SQL of the custom ones; this is the full picture, which had
 * nowhere to be seen before even though the API already returned it. */
const Checks: React.FC<{ checks: ContractDetail['checks']; history: ContractDetail['history'] }> = ({
  checks,
  history,
}) => (
  <div>
    <Typography variant='h4'>Checks</Typography>
    <EmptyContentPlaceholder
      isContentEmpty={checks.length === 0}
      fullPage={false}
      text='No checks recorded for this contract yet.'
    />
    {checks.map(c => (
      <S.PropertyRow key={c.check_id}>
        <S.Actions>
          <TestRunStatusItem size='small' typeName={runStatus(c.status)} count={1} />
          <Typography variant='body2'>{c.name ?? c.check_id}</Typography>
          {c.field && (
            <Typography variant='caption' color='texts.secondary'>
              {c.field}
            </Typography>
          )}
        </S.Actions>
        {c.reason && (
          <Typography variant='caption' color='texts.secondary'>
            {c.reason}
          </Typography>
        )}
        <CheckHistory checkId={c.check_id} history={history} />
      </S.PropertyRow>
    ))}
  </div>
);

const ContractPanel: React.FC<PanelProps> = ({
  detail,
  dimensions,
  ruleTypes,
  onSaved,
}) => {
  const [tab, setTab] = useState(0);
  // AuditTrail only refetches on its own when `detail.contract.id` changes;
  // a save from any form here changes what it should show without changing
  // that id, so `key` is how it is told to ask again.
  const [auditKey, setAuditKey] = useState(0);
  const saved = useCallback(() => {
    onSaved();
    setAuditKey(k => k + 1);
  }, [onSaved]);

  return (
    <S.Panel>
      <Typography variant='h4'>
        {detail.contract.title} — {detail.file}
      </Typography>

      <Definitions properties={detail.properties} />
      <Checks checks={detail.checks} history={detail.history} />

      {detail.rules.length > 0 && (
        <div>
          <Typography variant='subtitle2' color='texts.secondary'>
            Rules written for this contract. The rest of the tests are derived
            from its schema.
          </Typography>
          {detail.rules.map(rule => (
            <div key={rule.description}>
              <Typography variant='body1'>
                {rule.description}{' '}
                <Typography variant='caption' color='texts.secondary'>
                  {rule.dimension}
                </Typography>
              </Typography>
              <S.Sql>{rule.query}</S.Sql>
            </div>
          ))}
        </div>
      )}

      <Typography variant='h4'>Add a rule</Typography>
      <Typography variant='subtitle2' color='texts.secondary'>
        Saved as an ODCS quality entry in {detail.file}, then re-run. The
        contract stays the source of truth; this is an editor for it.
      </Typography>
      {/* A tab, not a small link -- the SQL escape hatch existed before and
          was easy to miss because "Write SQL instead" was the only clue it
          was there. */}
      <AppTabs
        type='secondary'
        selectedTab={tab}
        handleTabChange={setTab}
        items={[{ name: 'Form' }, { name: 'Write SQL' }]}
      />

      {tab === 1 ? (
        <RawSqlRule detail={detail} dimensions={dimensions} onSaved={saved} />
      ) : (
        <RuleBuilder
          detail={detail}
          dimensions={dimensions}
          ruleTypes={ruleTypes}
          onSaved={saved}
        />
      )}

      <SyncRuleForm detail={detail} onSaved={saved} />
      <AuditTrail key={auditKey} contractId={detail.contract.id} />
    </S.Panel>
  );
};

/**
 * Author this contract's own replication rule. The "Replication" table
 * further down the page only ever lists a contract once it already has one
 * -- this is the other half, see issue #10: there was previously no way to
 * add or change a syncTo rule without hand-editing the contract's YAML.
 */
const SyncRuleForm: React.FC<{ detail: ContractDetail; onSaved: () => void }> = ({
  detail,
  onSaved,
}) => {
  // The source and the daily-window schema are already excluded server-side
  // -- see GET /api/contracts/{id} -- neither is a sensible replication
  // target even though nothing here would catch it as unsound.
  const targets = detail.servers;
  const [server, setServer] = useState(targets[0]?.server ?? '');
  const [filter, setFilter] = useState('');
  const [columns, setColumns] = useState('');
  const [identity, setIdentity] = useState('');
  const [token, setToken] = useState(() => window.localStorage.getItem('dq_token') ?? '');
  const [result, setResult] = useState<string | { rule: SyncRule['rule'] } | null>(null);
  const [busy, setBusy] = useState(false);

  if (targets.length === 0) return null;

  const save = async () => {
    setBusy(true);
    window.localStorage.setItem('dq_token', token);
    const draft: SyncRuleDraft = {
      contract_id: detail.contract.id,
      server,
      filter: filter || undefined,
      columns: columns ? columns.split(',').map(c => c.trim()).filter(Boolean) : undefined,
      identity: identity ? identity.split(',').map(c => c.trim()).filter(Boolean) : undefined,
    };
    try {
      const saved = await saveSyncRule(draft, token);
      setResult({ rule: saved.rule });
      onSaved();
    } catch (e) {
      setResult((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Typography variant='h4'>Replication rule for this contract</Typography>
      <Typography variant='subtitle2' color='texts.secondary'>
        Written as this contract&apos;s own <code>syncTo</code> custom property, then
        checked against ADR 0008&apos;s four preconditions before it is saved --
        rejected with the specific reason if it would not actually replicate.
      </Typography>
      <S.Actions>
        <AppSelect
          id='sync-target'
          label='Target server'
          value={server}
          onChange={e => setServer(e.target.value as string)}
        >
          {targets.map(t => (
            <MenuItem key={t.server} value={t.server}>
              {t.server} ({t.type})
            </MenuItem>
          ))}
        </AppSelect>
        <Input
          variant='main-m'
          label="Row filter — optional, e.g. country = 'TR'"
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
      </S.Actions>
      <S.Actions>
        <Input
          variant='main-m'
          label='Columns — optional, comma separated; empty replicates all'
          value={columns}
          onChange={e => setColumns(e.target.value)}
        />
        <Input
          variant='main-m'
          label='Widen identity — optional, only if the filter needs it'
          value={identity}
          onChange={e => setIdentity(e.target.value)}
        />
      </S.Actions>
      <Input
        variant='main-m'
        type='password'
        label='API token — the service prints it at startup'
        value={token}
        onChange={e => setToken(e.target.value)}
      />
      <Button buttonType='main-m' text='Save' isLoading={busy} onClick={save} />
      {result &&
        (typeof result === 'string' ? (
          <Typography variant='body2' color='error.main'>
            {result}
          </Typography>
        ) : (
          <Typography variant='body2' color='success.main'>
            Saved. {result.rule.filter ?? 'Replicates everything'} to {result.rule.server}.
          </Typography>
        ))}
    </>
  );
};

/**
 * What changed about this contract's rules, and when -- never who. See
 * issue #12: there is no identity provider (ADR 0010), so this reads
 * contract_audit rather than pretending to know a person made the change.
 */
const AuditTrail: React.FC<{ contractId: string }> = ({ contractId }) => {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);

  useEffect(() => {
    setEntries(null);
    getContractAudit(contractId)
      .then(setEntries)
      .catch(() => setEntries([]));
  }, [contractId]);

  if (!entries || entries.length === 0) return null;

  return (
    <>
      <Typography variant='h4'>Recent changes</Typography>
      {entries.slice(0, 10).map((e, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <Typography key={i} variant='caption' color='texts.secondary' component='div'>
          {e.run_at} · {e.action} {e.change_type.replace('_', ' ')} &quot;{e.description}
          &quot;{e.caller_label ? ` — ${e.caller_label}` : ''}
        </Typography>
      ))}
    </>
  );
};

/**
 * A rule chosen rather than written.
 *
 * The vocabulary comes from the service, not from here, so adding a rule kind
 * is a change in one place. The SQL is composed there too, which is why this
 * form needs no token: there is no statement for a caller to smuggle in.
 */
const RuleBuilder: React.FC<PanelProps> = ({
  detail,
  dimensions,
  ruleTypes,
  onSaved,
}) => {
  const columns = detail.properties.map(p => p.name);
  const [column, setColumn] = useState(columns[0] ?? '');
  const [kind, setKind] = useState(ruleTypes[0]?.kind ?? '');
  const [params, setParams] = useState<Record<string, string>>({});
  const [dimension, setDimension] = useState('');
  const [preview, setPreview] = useState<PreviewShape | string | null>(null);
  const [busy, setBusy] = useState(false);

  const selected = ruleTypes.find(r => r.kind === kind);

  const rule = useCallback((): StructuredRule => {
    const values: Record<string, unknown> = {};
    (selected?.parameters ?? []).forEach(p => {
      const value = params[p.name] ?? '';
      if (p.type === 'list') {
        values[p.name] = value
          .split(',')
          .map(v => v.trim())
          .filter(Boolean);
      } else if (p.type === 'number') {
        values[p.name] = value === '' ? null : Number(value);
      } else {
        values[p.name] = value;
      }
    });
    return {
      contract_id: detail.contract.id,
      kind,
      column,
      params: values,
      ...(dimension ? { dimension } : {}),
    };
  }, [detail.contract.id, kind, column, params, dimension, selected]);

  const run = useCallback(
    async (save: boolean) => {
      setBusy(true);
      try {
        if (save) {
          await saveStructured(rule());
          setPreview(null);
          onSaved();
        } else {
          setPreview(await previewStructured(rule()));
        }
      } catch (e) {
        setPreview((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [rule, onSaved]
  );

  return (
    <>
      <S.Actions>
        <AppSelect id='rule-column' label='Column' value={column} onChange={e => setColumn(e.target.value as string)}>
          {columns.map(c => (
            <MenuItem key={c} value={c}>
              {c}
            </MenuItem>
          ))}
        </AppSelect>
        <AppSelect
          id='rule-kind'
          label='Rule'
          value={kind}
          onChange={e => {
            setKind(e.target.value as string);
            setParams({});
            setPreview(null);
          }}
        >
          {ruleTypes.map(r => (
            <MenuItem key={r.kind} value={r.kind}>
              {r.label}
            </MenuItem>
          ))}
        </AppSelect>
        <AppSelect
          id='rule-dimension'
          label='Dimension — weights the score'
          value={dimension}
          onChange={e => setDimension(e.target.value as string)}
        >
          <MenuItem value=''>default ({selected?.dimension ?? '—'})</MenuItem>
          {dimensions.map(d => (
            <MenuItem key={d} value={d}>
              {d}
            </MenuItem>
          ))}
        </AppSelect>
      </S.Actions>

      {(selected?.parameters ?? []).map(p => (
        <Input
          key={p.name}
          variant='main-m'
          type={p.type === 'number' ? 'number' : 'text'}
          label={p.label}
          value={params[p.name] ?? ''}
          onChange={e => setParams({ ...params, [p.name]: e.target.value })}
        />
      ))}

      <S.Actions>
        <Button
          buttonType='secondary-m'
          text='Preview'
          isLoading={busy}
          onClick={() => run(false)}
        />
        <Button
          buttonType='main-m'
          text='Save and run'
          isLoading={busy}
          onClick={() => run(true)}
        />
      </S.Actions>
      <PreviewResultView preview={preview} />
    </>
  );
};

/** The escape hatch: SQL someone wrote, so it carries the token. */
const RawSqlRule: React.FC<Omit<PanelProps, 'ruleTypes'>> = ({
  detail,
  dimensions,
  onSaved,
}) => {
  const [draft, setDraft] = useState<RuleDraft>({
    contract_id: detail.contract.id,
    description: '',
    query: `select count(*) from ${detail.contract.source_table}\nwhere `,
    dimension: dimensions.includes('conformity')
      ? 'conformity'
      : (dimensions[0] ?? 'unknown'),
    must_be: 0,
  });
  const [token, setToken] = useState(
    () => window.localStorage.getItem('dq_token') ?? ''
  );
  const [preview, setPreview] = useState<PreviewShape | string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (save: boolean) => {
      setBusy(true);
      window.localStorage.setItem('dq_token', token);
      try {
        if (save) {
          await saveRule(draft, token);
          setPreview(null);
          onSaved();
        } else {
          setPreview(await previewRule(draft, token));
        }
      } catch (e) {
        setPreview((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [draft, token, onSaved]
  );

  return (
    <>
      <Input
        variant='main-m'
        label='Description — becomes the test name'
        value={draft.description}
        onChange={e => setDraft({ ...draft, description: e.target.value })}
      />
      <S.Actions>
        <AppSelect
          id='raw-dimension'
          label='Dimension — weights the score'
          value={draft.dimension}
          onChange={e => setDraft({ ...draft, dimension: e.target.value as string })}
        >
          {dimensions.map(d => (
            <MenuItem key={d} value={d}>
              {d}
            </MenuItem>
          ))}
        </AppSelect>
      </S.Actions>
      <div>
        <Typography variant='caption' color='texts.secondary'>
          SQL — must return one number, the count of bad rows
        </Typography>
        <S.Textarea
          value={draft.query}
          onChange={e => setDraft({ ...draft, query: e.target.value })}
        />
      </div>
      <Input
        variant='main-m'
        type='password'
        label='API token — only this route needs one, and the service prints it at startup'
        value={token}
        onChange={e => setToken(e.target.value)}
      />
      <S.Actions>
        <Button
          buttonType='secondary-m'
          text='Preview'
          isLoading={busy}
          onClick={() => run(false)}
        />
        <Button
          buttonType='main-m'
          text='Save and run'
          isLoading={busy}
          onClick={() => run(true)}
        />
      </S.Actions>
      <PreviewResultView preview={preview} />
    </>
  );
};

const PreviewResultView: React.FC<{
  preview: PreviewShape | string | null;
}> = ({ preview }) => {
  if (!preview) return null;
  if (typeof preview === 'string') {
    return (
      <Typography variant='body2' color='error.main'>
        {preview}
      </Typography>
    );
  }
  return (
    <div>
      {preview.description && (
        <Typography variant='body1'>{preview.description}</Typography>
      )}
      <Typography
        variant='body2'
        color={preview.ok ? 'success.main' : 'error.main'}
      >
        {preview.ok
          ? `compiled · result ${preview.result} · failing rows ${preview.failed_rows ?? '—'}`
          : (preview.error ?? preview.reason ?? 'the rule did not compile')}
      </Typography>
      {(preview.compiled_sql ?? preview.query) && (
        <S.Sql>{preview.compiled_sql ?? preview.query}</S.Sql>
      )}
    </div>
  );
};

export default Contracts;
