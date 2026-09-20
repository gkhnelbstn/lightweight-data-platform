import React, { useEffect, useState } from 'react';
import { MenuItem, Typography } from '@mui/material';
import { AppSelect, Button, Input } from 'components/shared/elements';
import type { FlowDetail, FlowDoc, FlowSide } from './api';
import { getFlow, runJobs, saveFlow } from './api';
import { tr, useT } from './shared';
import * as S from './Contracts.styles';

/**
 * A flow, configured from the screen (#109).
 *
 * What is edited is the **flow file** -- `contracts/flows/<id>.yaml`, the
 * contract of the integration (ADR 0019) -- because the contract is the
 * source of truth and this is an editor for it (invariant 1). Nothing here
 * writes to SeaTunnel: Apply calls the same code `--apply` does, so a flow
 * started from this panel and one started from the CLI are the same flow, and
 * refuse for the same reasons (a map that is not an inverse, a column the
 * contract does not declare, a table that changed underneath).
 *
 * Check before Save, always: the refusals are the point of the file.
 */

type Value = string | number | boolean;

/** Text as the file would hold it. A value map matches what the source
 * really holds -- `'Y'` against `true` -- so `true`, `false` and numbers are
 * typed rather than quoted; everything else is a string. */
const typed = (text: string): Value => {
  const raw = text.trim();
  if (raw === 'true') return true;
  if (raw === 'false') return false;
  if (raw !== '' && !Number.isNaN(Number(raw))) return Number(raw);
  return text;
};

const shown = (v: unknown) => (v === null || v === undefined ? '' : String(v));

const Columns: React.FC<{ side: FlowSide | null }> = ({ side }) => {
  const t = useT();
  if (!side) return null;
  return (
    <Typography variant='caption' color='texts.secondary' component='div'>
      {t('{{title}}: {{columns}}', {
        title: side.name ?? side.id,
        columns: side.columns.map(c => c.name + (c.key ? ' *' : '')).join(', '),
      })}
    </Typography>
  );
};

/** One map, edited as rows. The left is chosen from a list when there is one
 * (a column of the target), the right either chosen or typed. */
const Pairs: React.FC<{
  title: string;
  left: string;
  right: string;
  leftOptions?: string[];
  rightOptions?: string[];
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  hint?: string;
}> = ({ title, left, right, leftOptions, rightOptions, value, onChange, hint }) => {
  const t = useT();
  const rows = Object.entries(value);
  const set = (key: string, k: string, v: unknown) => {
    const next: Record<string, unknown> = {};
    rows.forEach(([a, b]) => {
      if (a === key) {
        if (k !== '') next[k] = v;
      } else {
        next[a] = b;
      }
    });
    onChange(next);
  };
  const add = () => onChange({ ...value, '': '' });
  return (
    <div>
      <Typography variant='h5'>{title}</Typography>
      {hint && (
        <Typography variant='caption' color='texts.secondary' component='div'>
          {hint}
        </Typography>
      )}
      {rows.map(([a, b], i) => (
        // The fields are labelled once, on the first row: a label on every
        // row of a four-row map is the same two words four times.
        // eslint-disable-next-line react/no-array-index-key
        <S.Actions key={`${a}-${i}`}>
          {leftOptions ? (
            <AppSelect
              id={`${left}-${i}-left`}
              label={i === 0 ? left : ' '}
              value={a}
              onChange={e => set(a, e.target.value as string, b)}
            >
              {[a, ...leftOptions.filter(o => o !== a)].filter(Boolean).map(o => (
                <MenuItem key={o} value={o}>
                  {o}
                </MenuItem>
              ))}
            </AppSelect>
          ) : (
            <Input
              variant='main-m'
              label={i === 0 ? left : ' '}
              value={a}
              onChange={e => set(a, e.target.value, b)}
            />
          )}
          {rightOptions ? (
            <AppSelect
              id={`${left}-${i}-right`}
              label={i === 0 ? right : ' '}
              value={shown(b)}
              onChange={e => set(a, a, e.target.value as string)}
            >
              {[shown(b), ...rightOptions.filter(o => o !== b)].filter(Boolean).map(o => (
                <MenuItem key={o} value={o}>
                  {o}
                </MenuItem>
              ))}
            </AppSelect>
          ) : (
            <Input
              variant='main-m'
              label={i === 0 ? right : ' '}
              value={shown(b)}
              onChange={e => set(a, a, typed(e.target.value))}
            />
          )}
          <Button
            buttonType='secondary-m'
            text={t('Remove')}
            onClick={() => {
              const next = { ...value };
              delete next[a];
              onChange(next);
            }}
          />
        </S.Actions>
      ))}
      <Button buttonType='tertiary-m' text={t('Add')} onClick={add} />
    </div>
  );
};

/** A list of column names, as checkboxes would be too wide: chosen one at a
 * time from what the contract declares. */
const Names: React.FC<{
  label: string;
  hint: string;
  options: string[];
  value: string[];
  onChange: (next: string[]) => void;
}> = ({ label, hint, options, value, onChange }) => {
  const t = useT();
  const left = options.filter(o => !value.includes(o));
  return (
    <div>
      <Typography variant='h5'>{label}</Typography>
      <Typography variant='caption' color='texts.secondary' component='div'>
        {hint}
      </Typography>
      <S.Actions>
        {value.map(name => (
          <Button
            key={name}
            buttonType='secondary-m'
            text={`${name} ×`}
            onClick={() => onChange(value.filter(v => v !== name))}
          />
        ))}
        {left.length > 0 && (
          <AppSelect
            id={`${label}-add`}
            label={t('Add')}
            value=''
            onChange={e => onChange([...value, e.target.value as string])}
          >
            {left.map(o => (
              <MenuItem key={o} value={o}>
                {o}
              </MenuItem>
            ))}
          </AppSelect>
        )}
      </S.Actions>
    </div>
  );
};

export const FlowPanel: React.FC<{ id: string; onRan?: () => void }> = ({ id, onRan }) => {
  const t = useT();
  const [detail, setDetail] = useState<FlowDetail | null>(null);
  const [doc, setDoc] = useState<FlowDoc>({});
  const [problems, setProblems] = useState<string[]>([]);
  const [said, setSaid] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showJob, setShowJob] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getFlow(id)
      .then(d => {
        setDetail(d);
        setDoc(d.doc);
        setProblems(d.problems);
      })
      .catch((e: Error) => setError(e.message));
  }, [id]);

  if (!detail) {
    return (
      <Typography variant='body2' color={error ? 'error.main' : 'texts.secondary'}>
        {error ?? t('Loading…')}
      </Typography>
    );
  }

  const from = detail.sides.from;
  const to = detail.sides.to;
  const targets = (to?.columns ?? []).map(c => c.name);
  const sources = (from?.columns ?? []).map(c => c.name);
  const mapped = doc.columns ?? {};
  const job = doc.job ?? {};

  const change = (part: Partial<FlowDoc>) => {
    setDoc({ ...doc, ...part });
    setSaved(false);
  };

  const send = async (check: boolean) => {
    setBusy(true);
    setSaid([]);
    try {
      const result = await saveFlow(id, doc, check);
      setProblems(result.problems);
      setSaved(result.saved);
      if (result.saved) setSaid([t('Saved to {{file}}. Restart the flow to run it.', { file: detail.file })]);
    } catch (e) {
      setProblems([(e as Error).message]);
    } finally {
      setBusy(false);
    }
  };

  const run = async (action: 'apply' | 'stop' | 'restart' | 'resnapshot') => {
    if (action === 'resnapshot' &&
        // eslint-disable-next-line no-alert
        !window.confirm(tr('Reading the table from the start again means the hub sees every row as a first sync. Continue?'))) {
      return;
    }
    setBusy(true);
    setSaid([]);
    try {
      const result = await runJobs(id, action);
      setSaid(result.said);
      setProblems(result.refused);
      onRan?.();
    } catch (e) {
      setProblems([(e as Error).message]);
    } finally {
      setBusy(false);
    }
  };

  const setValues = (column: string, pairs: Record<string, unknown>) => {
    const values = { ...(doc.values ?? {}) };
    if (Object.keys(pairs).length === 0) delete values[column];
    else values[column] = pairs as Record<string, unknown>;
    change({ values });
  };

  return (
    <S.Panel>
      <div>
        <Typography variant='h4'>{detail.id}</Typography>
        <Typography variant='caption' color='texts.secondary' component='div'>
          {t('{{from}} → {{to}} · {{file}}', {
            from: doc.from ?? '',
            to: doc.to ?? '',
            file: detail.file,
          })}
        </Typography>
        <Columns side={from} />
        <Columns side={to} />
      </div>

      {detail.drift.map(d => (
        <Typography key={d} variant='body2' color='error.main'>
          {d}
        </Typography>
      ))}

      <Pairs
        title={t('What it carries')}
        left={t('Column it fills')}
        right={t('Column it reads')}
        leftOptions={targets}
        rightOptions={sources}
        value={mapped}
        onChange={columns => change({ columns: columns as Record<string, string> })}
        hint={t('The map, and the flow back must be its inverse: what fills a column here is what that column fills there.')}
      />

      {Object.keys(doc.values ?? {}).map(column => (
        <Pairs
          key={`values-${column}`}
          title={t('{{column}}: value map', { column })}
          left={t('value')}
          right={t('becomes')}
          value={(doc.values ?? {})[column] ?? {}}
          onChange={pairs => setValues(column, pairs)}
          hint={t('A column coded differently on the two sides. One value to one value, and the flow back maps it the other way -- anything else corrupts a round trip.')}
        />
      ))}

      {Object.keys(mapped).some(c => !(doc.values ?? {})[c]) && (
        <S.Actions>
          <AppSelect
            id='add-value-map'
            label={t('Translate the values of')}
            value=''
            onChange={e => setValues(e.target.value as string, { '': '' })}
          >
            {Object.keys(mapped)
              .filter(c => !(doc.values ?? {})[c])
              .map(c => (
                <MenuItem key={c} value={c}>
                  {c}
                </MenuItem>
              ))}
          </AppSelect>
          <Typography variant='caption' color='texts.secondary'>
            {t('Only a column whose two sides code the same thing differently needs one.')}
          </Typography>
        </S.Actions>
      )}

      <Pairs
        title={t('Rows this flow takes')}
        left={t('Column')}
        right={t('is')}
        value={doc.match ?? {}}
        onChange={match => change({ match })}
        hint={t('A table with several rows per record (an address per type): a filter on the way in, a constant on the way out, and both directions must agree.')}
      />

      {Object.keys(doc.aggregates ?? {}).length > 0 && (
        <Pairs
          title={t('Totals')}
          left={t('Total column')}
          right={t('is')}
          leftOptions={targets}
          value={doc.aggregates ?? {}}
          onChange={aggregates => change({ aggregates: aggregates as Record<string, string> })}
          hint={t('Many rows into one, one way: sum, count, min, max or avg of one column. The columns above are then the group.')}
        />
      )}

      {to?.hub && (
        <Names
          label={t('Find the record by')}
          hint={t('A row under a code the hub has not seen: one match links it, none creates a record, anything else waits for a person.')}
          options={targets}
          value={doc.linkBy ?? []}
          onChange={linkBy => change({ linkBy })}
        />
      )}

      {!to?.hub && (
        <Names
          label={t('Filled by the target')}
          hint={t('Columns the receiving system fills itself -- its own key, a created-at default. The hub never sends them.')}
          options={targets}
          value={doc.filledByTarget ?? []}
          onChange={filledByTarget => change({ filledByTarget })}
        />
      )}

      <div>
        <Typography variant='h5'>{t('How SeaTunnel runs it')}</Typography>
        <Typography variant='caption' color='texts.secondary' component='div'>
          {t('Parallelism is not offered: a second reader reorders one record’s changes, and the hub decides by the order they were committed in.')}
        </Typography>
        <S.Actions>
          <Input
            variant='main-m'
            type='number'
            label={t('Checkpoint every (ms)')}
            value={shown(job.checkpointInterval)}
            onChange={e =>
              change({
                job: e.target.value
                  ? { ...job, checkpointInterval: Number(e.target.value) }
                  : (({ checkpointInterval, ...rest }) => rest)(job),
              })
            }
          />
          <Input
            variant='main-m'
            type='number'
            label={t('Read at most (rows/second)')}
            value={shown(job.rowsPerSecond)}
            onChange={e =>
              change({
                job: e.target.value
                  ? { ...job, rowsPerSecond: Number(e.target.value) }
                  : (({ rowsPerSecond, ...rest }) => rest)(job),
              })
            }
          />
        </S.Actions>
        <Typography variant='caption' color='texts.secondary' component='div'>
          {t('Empty means the platform’s default: a checkpoint every 3 s, and no read limit. A limit is what keeps a first read of a large table from taking the source’s disk.')}
        </Typography>
      </div>

      {problems.map(p => (
        <Typography key={p} variant='body2' color='error.main'>
          {p}
        </Typography>
      ))}
      {said.map(s => (
        <Typography key={s} variant='body2' color={saved ? 'success.main' : 'texts.secondary'}>
          {s}
        </Typography>
      ))}

      <S.Actions>
        <Button
          buttonType='secondary-m'
          text={t('Check')}
          isLoading={busy}
          onClick={() => send(true)}
        />
        <Button
          buttonType='main-m'
          text={t('Save')}
          isLoading={busy}
          onClick={() => send(false)}
        />
        <Button
          buttonType='secondary-m'
          text={t('Restart the flow')}
          isLoading={busy}
          onClick={() => run('restart')}
        />
        <Button
          buttonType='secondary-m'
          text={t('Stop')}
          isLoading={busy}
          onClick={() => run('stop')}
        />
        <Button
          buttonType='secondary-m'
          text={t('Start')}
          isLoading={busy}
          onClick={() => run('apply')}
        />
        <Button
          buttonType='tertiary-m'
          text={t('Read from the start again')}
          isLoading={busy}
          onClick={() => run('resnapshot')}
        />
      </S.Actions>
      <Typography variant='caption' color='texts.secondary'>
        {t('Stopping takes a savepoint and starting resumes from it, so nothing between is missed. Reading from the start again does not: the hub sees a first sync.')}
      </Typography>

      <div>
        <Button
          buttonType='tertiary-m'
          text={showJob ? t('Hide what SeaTunnel runs') : t('Show what SeaTunnel runs')}
          onClick={() => setShowJob(!showJob)}
        />
        {showJob && <S.Sql>{JSON.stringify(detail.jobs, null, 2)}</S.Sql>}
      </div>
    </S.Panel>
  );
};

export default FlowPanel;
