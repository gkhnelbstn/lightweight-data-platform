import React, { useCallback, useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import { Button, Input } from 'components/shared/elements';
import type {
  ContractDetail,
  Overview,
  PreviewResult,
  RuleDraft,
  Sample,
} from './api';
import {
  getContract,
  getOverview,
  getSample,
  previewRule,
  saveRule,
} from './api';
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

  useEffect(() => {
    getOverview()
      .then(setOverview)
      .catch((e: Error) => setError(e.message));
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
        <ContractPanel detail={detail} dimensions={overview.dimensions} onSaved={reload} />
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

interface PanelProps {
  detail: ContractDetail;
  dimensions: string[];
  onSaved: () => void;
}

const ContractPanel: React.FC<PanelProps> = ({ detail, dimensions, onSaved }) => {
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
  const [preview, setPreview] = useState<PreviewResult | string | null>(null);
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
    <S.Panel>
      <Typography variant='h4'>
        {detail.contract.title} — {detail.file}
      </Typography>

      {detail.rules.length > 0 && (
        <div>
          <Typography variant='subtitle2' color='texts.secondary'>
            Rules written by hand. The rest of the tests are derived from the
            schema.
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
      <Input
        variant='main-m'
        label='Description — becomes the test name'
        value={draft.description}
        onChange={e => setDraft({ ...draft, description: e.target.value })}
      />
      <div>
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
      </div>
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
        label='API token — authoring runs SQL against the source'
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
      {typeof preview === 'string' && (
        <Typography variant='body2' color='error.main'>
          {preview}
        </Typography>
      )}
      {preview && typeof preview !== 'string' && (
        <div>
          <Typography variant='body2' color={preview.ok ? 'success.main' : 'error.main'}>
            {preview.ok
              ? `compiled · result ${preview.result} · failing rows ${preview.failed_rows ?? '—'}`
              : (preview.error ?? preview.reason ?? 'the rule did not compile')}
          </Typography>
          {preview.compiled_sql && <S.Sql>{preview.compiled_sql}</S.Sql>}
        </div>
      )}
    </S.Panel>
  );
};

export default Contracts;
