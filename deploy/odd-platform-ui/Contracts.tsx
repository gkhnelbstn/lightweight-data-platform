import React, { useCallback, useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import { Button, Input } from 'components/shared/elements';
import type {
  ContractDetail,
  Overview,
  PreviewResult,
  RuleDraft,
  RuleType,
  Sample,
  StructuredRule,
  SyncRule,
} from './api';
import {
  getContract,
  getOverview,
  getRuleTypes,
  getSample,
  getSyncRules,
  previewRule,
  previewStructured,
  saveRule,
  saveStructured,
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
 * change on the next refresh.
 */

const fmt = (v: unknown) =>
  v === null || v === undefined ? '—' : Number(v).toFixed(3);

export const Contracts: React.FC = () => {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ContractDetail | null>(null);
  const [samples, setSamples] = useState<Record<string, Sample | string>>({});
  const [ruleTypes, setRuleTypes] = useState<RuleType[]>([]);

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
  if (!overview) return null;

  return (
    <>
      <Typography variant='h4'>Contract quality over time</Typography>
      <Trend points={overview.trend} />

      <Typography variant='h4'>Contracts</Typography>
      <Typography variant='subtitle2' color='texts.secondary'>
        The tests above are derived from these. Select one to see its rules, add
        another, or open the rows a check failed on.
      </Typography>

      <div>
        <S.HeaderRow>
          <Typography variant='caption'>Contract</Typography>
          <Typography variant='caption'>Source</Typography>
          <Typography variant='caption'>Score</Typography>
          <Typography variant='caption'>SLA</Typography>
          <Typography variant='caption'>Tests</Typography>
        </S.HeaderRow>
        {overview.contracts.map(c => (
          <S.Row
            key={c.id}
            $selected={selected === c.id}
            onClick={() => select(c.id)}
          >
            <div>
              <Typography variant='body1'>{c.title}</Typography>
              <Typography variant='caption' color='texts.secondary'>
                {c.id}
              </Typography>
            </div>
            <Typography variant='body2'>
              {c.source_table} ({c.server_type})
            </Typography>
            <Typography
              variant='h4'
              color={c.sla_met === false ? 'error.main' : 'success.main'}
            >
              {fmt(c.score)}
            </Typography>
            <Typography variant='body2' color='texts.secondary'>
              ≥ {fmt(c.sla_min)}
            </Typography>
            <Typography variant='body2'>
              {c.checks_total == null
                ? '—'
                : `${c.checks_total - (c.checks_failed ?? 0) - (c.checks_errored ?? 0)}/${c.checks_total} passed`}
              {c.checks_errored ? ` · ${c.checks_errored} could not run` : ''}
            </Typography>
          </S.Row>
        ))}
      </div>

      {selected && detail && (
        <ContractPanel
          detail={detail}
          dimensions={overview.dimensions}
          ruleTypes={ruleTypes}
          onSaved={reload}
        />
      )}

      {overview.open_failures.length > 0 && (
        <S.Panel>
          <Typography variant='h4'>Open failures</Typography>
          {overview.open_failures.map(f => {
            const sample = samples[f.check_id];
            return (
              <div key={f.check_id}>
                <S.Actions>
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
      <svg viewBox={`0 0 ${w} ${h}`} width='100%' height={h} style={{ maxWidth: w }}>
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
 * is the whole reason this reports `slot_active` and `worker_running` rather
 * than just the rule.
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
        <S.HeaderRow>
          <Typography variant='caption'>Contract</Typography>
          <Typography variant='caption'>Target</Typography>
          <Typography variant='caption'>Rule</Typography>
          <Typography variant='caption'>Identity</Typography>
          <Typography variant='caption'>State</Typography>
        </S.HeaderRow>
        {rows.map(r => {
          const status = r.status ?? {};
          const streaming = status.worker_running === true && status.slot_active === true;
          return (
            <S.Grid key={r.contract_id}>
              <div>
                <Typography variant='body1'>{r.title}</Typography>
                <Typography variant='caption' color='texts.secondary'>
                  {r.contract_id}
                </Typography>
              </div>
              <Typography variant='body2'>{r.rule.server}</Typography>
              <div>
                <Typography variant='body2'>{r.rule.filter ?? 'everything'}</Typography>
                <Typography variant='caption' color='texts.secondary'>
                  {(r.rule.columns ?? ['all columns']).join(', ')}
                </Typography>
              </div>
              <Typography variant='body2'>{(r.identity ?? []).join(', ')}</Typography>
              <div>
                <Typography
                  variant='body2'
                  color={streaming ? 'success.main' : 'texts.secondary'}
                >
                  {status.engine && status.engine !== 'logical replication'
                    ? `${status.engine} CDC`
                    : streaming
                      ? `streaming · ${status.behind ?? ''} behind`
                      : 'not applied'}
                </Typography>
                {(r.problems ?? []).map(p => (
                  <Typography key={p} variant='caption' color='error.main'>
                    {p}
                  </Typography>
                ))}
              </div>
            </S.Grid>
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

const ContractPanel: React.FC<PanelProps> = ({
  detail,
  dimensions,
  ruleTypes,
  onSaved,
}) => {
  const [raw, setRaw] = useState(false);

  return (
    <S.Panel>
      <Typography variant='h4'>
        {detail.contract.title} — {detail.file}
      </Typography>

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

      <S.Actions>
        <Typography variant='h4'>Add a rule</Typography>
        <Button
          buttonType='tertiary-sm'
          text={raw ? 'Use the form' : 'Write SQL instead'}
          onClick={() => setRaw(v => !v)}
        />
      </S.Actions>
      <Typography variant='subtitle2' color='texts.secondary'>
        Saved as an ODCS quality entry in {detail.file}, then re-run. The
        contract stays the source of truth; this is an editor for it.
      </Typography>

      {raw ? (
        <RawSqlRule detail={detail} dimensions={dimensions} onSaved={onSaved} />
      ) : (
        <RuleBuilder
          detail={detail}
          dimensions={dimensions}
          ruleTypes={ruleTypes}
          onSaved={onSaved}
        />
      )}
    </S.Panel>
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
        <label>
          <Typography variant='caption' color='texts.secondary'>
            Column
          </Typography>
          <select value={column} onChange={e => setColumn(e.target.value)}>
            {columns.map(c => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          <Typography variant='caption' color='texts.secondary'>
            Rule
          </Typography>
          <select
            value={kind}
            onChange={e => {
              setKind(e.target.value);
              setParams({});
              setPreview(null);
            }}
          >
            {ruleTypes.map(r => (
              <option key={r.kind} value={r.kind}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <Typography variant='caption' color='texts.secondary'>
            Dimension — weights the score
          </Typography>
          <select value={dimension} onChange={e => setDimension(e.target.value)}>
            <option value=''>default ({selected?.dimension ?? '—'})</option>
            {dimensions.map(d => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
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
      <label>
        <Typography variant='caption' color='texts.secondary'>
          Dimension — weights the score
        </Typography>
        <select
          value={draft.dimension}
          onChange={e => setDraft({ ...draft, dimension: e.target.value })}
        >
          {dimensions.map(d => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
      </label>
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
