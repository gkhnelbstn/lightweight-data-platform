import React, { useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import { EmptyContentPlaceholder, Table } from 'components/shared/elements';
import type { SyncRule } from './api';
import { getSyncRules } from './api';

/**
 * The replication rules, and whether they are actually running.
 *
 * A dead apply worker and a quiet one look identical from the outside, which
 * is the whole reason this reports `slot_active` and `worker_running` for
 * Postgres, and `last_synced` (core/sync_mssql.py's own watermark) for CDC --
 * a rule sitting there with nothing behind it should not look the same as one
 * actually moving rows.
 */
export const Replication: React.FC = () => {
  const [rows, setRows] = useState<SyncRule[] | null>(null);

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
      )}
    </>
  );
};
