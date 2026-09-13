import React, { useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import { EmptyContentPlaceholder, Table } from 'components/shared/elements';
import type { SyncRule } from './api';
import { getSyncRules } from './api';
import { when } from './shared';
import * as S from './Contracts.styles';

/**
 * The replication rules, and whether data is actually arriving.
 *
 * A dead apply worker and a quiet one look identical from the outside, which
 * is why this reports `slot_active` and `worker_running` for Postgres and
 * `last_synced` for CDC. But those answer "is it configured", and the question
 * anyone actually has is "is my data arriving" — so the row counts on both
 * sides are here too, and what the last passes moved. Issue #28.
 */
export const Replication: React.FC = () => {
  const [rows, setRows] = useState<SyncRule[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    getSyncRules().then(setRows).catch(() => setRows([]));
  }, []);

  if (!rows) {
    return (
      <Typography variant='body2' color='texts.secondary'>
        Loading replication rules…
      </Typography>
    );
  }

  return (
    <>
      <Typography variant='subtitle2' color='texts.secondary'>
        The contract says where its table is replicated to; the engine is the
        database&apos;s own — logical replication on Postgres, CDC on SQL Server.
        Nothing of ours sits in the stream. A rule is added on the contract&apos;s
        own tab.
      </Typography>
      <EmptyContentPlaceholder
        isContentEmpty={rows.length === 0}
        fullPage={false}
        text='No contract declares a syncTo rule yet.'
      />
      {rows.length > 0 && (
        <div>
          <Table.HeaderContainer>
            <Table.Cell $flex={1.8}>
              <Typography variant='caption'>Contract</Typography>
            </Table.Cell>
            <Table.Cell $flex={1.6}>
              <Typography variant='caption'>Rows arriving</Typography>
            </Table.Cell>
            <Table.Cell $flex={1.6}>
              <Typography variant='caption'>Rule</Typography>
            </Table.Cell>
            <Table.Cell $flex={1.8}>
              <Typography variant='caption'>State</Typography>
            </Table.Cell>
          </Table.HeaderContainer>
          {rows.map(r => (
            <React.Fragment key={r.contract_id}>
              <Table.RowContainer
                role='button'
                tabIndex={0}
                aria-expanded={open === r.contract_id}
                onClick={() =>
                  setOpen(prev => (prev === r.contract_id ? null : r.contract_id))
                }
                onKeyDown={e => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setOpen(prev => (prev === r.contract_id ? null : r.contract_id));
                  }
                }}
                sx={{
                  cursor: 'pointer',
                  '&:focus-visible': { outline: '2px solid currentColor', outlineOffset: '-2px' },
                }}
              >
                <Table.Cell $flex={1.8}>
                  <div>
                    <Typography variant='body1'>{r.title}</Typography>
                    <Typography variant='caption' color='texts.secondary'>
                      {r.engine} → {r.rule.server}
                    </Typography>
                  </div>
                </Table.Cell>
                <Table.Cell $flex={1.6}>
                  <Arriving rule={r} />
                </Table.Cell>
                <Table.Cell $flex={1.6}>
                  <div>
                    <Typography variant='body2'>{r.rule.filter ?? 'everything'}</Typography>
                    <Typography variant='caption' color='texts.secondary'>
                      {columnList(r)} · identity {(r.identity ?? []).join(', ')}
                    </Typography>
                  </div>
                </Table.Cell>
                <Table.Cell $flex={1.8}>
                  <State rule={r} />
                </Table.Cell>
              </Table.RowContainer>
              {open === r.contract_id && <Runs rule={r} />}
            </React.Fragment>
          ))}
        </div>
      )}
    </>
  );
};

/** A publication's column list is a privacy boundary, not an optimisation, so
 * it matters that it is short and it matters which columns are in it -- but
 * eight of them turn one table row into three. The count carries the shape;
 * the full list is in the contract. */
const columnList = (rule: SyncRule) => {
  const columns = rule.rule.columns;
  if (!columns) return 'all columns';
  if (columns.length <= 3) return columns.join(', ');
  return `${columns.slice(0, 3).join(', ')} +${columns.length - 3} more`;
};

/**
 * How much of the source is on the other side.
 *
 * A filtered rule is *supposed* to leave rows behind — a publication's column
 * and row list is a privacy boundary, not an optimisation — so fewer rows on
 * the target is only news when there is no filter.
 */
const Arriving: React.FC<{ rule: SyncRule }> = ({ rule }) => {
  const a = rule.arriving;
  if (!a) return null;
  if (a.target_error) {
    return (
      <Typography variant='body2' color='error.main'>
        target unreachable
      </Typography>
    );
  }
  const short = a.source !== undefined && a.target !== undefined &&
    a.target < a.source && !a.filter;
  return (
    <div>
      <Typography variant='body1' color={short ? 'error.main' : 'texts.primary'}>
        {a.target ?? '—'} of {a.source ?? '—'}
      </Typography>
      <Typography variant='caption' color='texts.secondary'>
        {a.filter ? 'rows, filtered' : 'rows, unfiltered'}
      </Typography>
    </div>
  );
};

const State: React.FC<{ rule: SyncRule }> = ({ rule }) => {
  const status = rule.status ?? {};
  const isCdc = !!status.engine && status.engine !== 'logical replication';
  // The service decides this: an apply worker can be up, the slot active and
  // the lag zero while a table sits in the initial copy and replicates
  // nothing (issue #35). Older payloads have no `streaming`, so fall back.
  const copying = status.copying ?? [];
  const streaming = status.streaming ??
    (status.worker_running === true && status.slot_active === true);
  const last = rule.runs?.[0];
  return (
    <div>
      {isCdc ? (
        <Typography
          variant='body2'
          color={status.last_synced ? 'success.main' : 'texts.secondary'}
        >
          {status.last_synced
            ? `read ${when(status.last_synced)}`
            : 'never read — is core/sync_mssql.py running?'}
        </Typography>
      ) : (
        <Typography
          variant='body2'
          color={
            streaming ? 'success.main'
              : copying.length ? 'error.main'
              : 'texts.secondary'
          }
        >
          {streaming
            ? `streaming · ${status.behind ?? ''} behind`
            : copying.length
            ? `${copying.join(', ')} stuck in the initial copy`
            : 'not applied'}
        </Typography>
      )}
      {last?.applied_through && (
        <Typography variant='caption' color='texts.secondary' component='div'>
          changes applied through {new Date(last.applied_through).toLocaleTimeString()}
        </Typography>
      )}
      {(rule.problems ?? []).map(p => (
        <Typography key={p} variant='caption' color='error.main' component='div'>
          {p}
        </Typography>
      ))}
    </div>
  );
};

/** The last passes, newest first. A run that moved nothing after a week of
 * moving hundreds is the shape worth seeing, which a total would hide. */
const Runs: React.FC<{ rule: SyncRule }> = ({ rule }) => {
  const runs = rule.runs ?? [];
  return (
    <S.Panel>
      <Typography variant='h4'>Recent passes</Typography>
      {runs.length === 0 ? (
        <Typography variant='body2' color='texts.secondary'>
          Nothing recorded. Logical replication is the database&apos;s own apply
          worker and keeps no per-pass count here — the row counts above are the
          measurement for it. For CDC this fills in once{' '}
          <code>core/sync_mssql.py</code> has run.
        </Typography>
      ) : (
        <S.Scroll>
          <S.Cells>
            <thead>
              <tr>
                <th>Ran</th>
                <th>Mode</th>
                <th>Changes read</th>
                <th>Upserted</th>
                <th>Deleted</th>
                <th>Applied through</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(run => (
                <tr key={run.run_at}>
                  <td>{new Date(run.run_at).toLocaleString()}</td>
                  <td>{run.mode}</td>
                  <td>{run.rows_read}</td>
                  <td>{run.upserted}</td>
                  <td>{run.deleted}</td>
                  <td>
                    {run.applied_through
                      ? new Date(run.applied_through).toLocaleString()
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </S.Cells>
        </S.Scroll>
      )}
      <Typography variant='caption' color='texts.secondary'>
        Changes read is CDC rows, which is more than rows moved: an update
        arrives as a before image and an after image, and both are needed —
        without the before image an update that changes an identity column
        silently duplicates the row.
      </Typography>
    </S.Panel>
  );
};
