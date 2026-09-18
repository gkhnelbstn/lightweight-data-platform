import React, { useEffect, useMemo, useState } from 'react';
import { MenuItem, Typography } from '@mui/material';
import {
  AppSelect,
  EmptyContentPlaceholder,
  Input,
  Table,
  TestRunStatusItem,
} from 'components/shared/elements';
import type { CheckRow, ContractSummary } from './api';
import { getChecks } from './api';
import { CheckDetail } from './CheckDetail';
import { readParam, runStatus, useT, when, writeParams } from './shared';
import * as S from './Contracts.styles';

/**
 * Every check on the platform, in one list.
 *
 * The panel used to show checks only inside a contract, three scroll-lengths
 * down: to answer "what is failing, and against which table" you had to open
 * each contract in turn. This is the list that question actually wants, and
 * selecting a row opens what the check does, the SQL behind it and the runs it
 * has had -- all of which /api/checks and /api/checks/{id}/history already
 * returned and nothing rendered.
 */

interface Props {
  contracts: ContractSummary[];
}

export const ChecksTab: React.FC<Props> = ({ contracts }) => {
  const t = useT();
  const [rows, setRows] = useState<CheckRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(() => readParam('check'));
  const [needle, setNeedle] = useState('');
  const [status, setStatus] = useState('failing');
  // `dq_checks_contract` is what a catalogue entity links to: the checks of
  // one table, in this tab, rather than the contract's own page. A separate
  // key from `dq_contract`, which selects the Contracts tab (issue #23).
  const [contract, setContract] = useState(() => readParam('checks_contract') ?? '');
  const [reloads, setReloads] = useState(0);

  useEffect(() => {
    getChecks()
      .then(setRows)
      .catch((e: Error) => setError(e.message));
  }, [reloads]);

  // The contract's title and source table are not in check_results; the
  // overview already carries both, so they are joined here rather than in a
  // second query on the service.
  const source = useMemo(
    () => new Map(contracts.map(c => [c.id, c])),
    [contracts]
  );

  const visible = useMemo(() => {
    const text = needle.trim().toLowerCase();
    return (rows ?? []).filter(r => {
      // Accepted is a failure the team decided to live with (issue #29), so
      // it is out of the default list for the same reason a passing check is:
      // neither is news. It stays in the score either way.
      if (status === 'failing' && (r.status === 'pass' || r.state === 'accepted'))
        return false;
      if (status === 'passing' && r.status !== 'pass') return false;
      if (status === 'accepted' && r.state !== 'accepted') return false;
      if (contract && r.contract_id !== contract) return false;
      if (!text) return true;
      const table = source.get(r.contract_id)?.source_table ?? '';
      return (
        (r.name ?? r.check_id).toLowerCase().includes(text) ||
        r.check_id.toLowerCase().includes(text) ||
        (r.field ?? '').toLowerCase().includes(text) ||
        table.toLowerCase().includes(text)
      );
    });
  }, [rows, needle, status, contract, source]);

  const pick = (id: string) => {
    setContract(id);
    writeParams({ checks_contract: id || null });
  };

  const select = (id: string) => {
    const next = selected === id ? null : id;
    setSelected(next);
    writeParams({ check: next });
  };

  if (error) {
    return (
      <Typography variant='body1' color='texts.secondary'>
        {t('Contract service unreachable: {{error}}', { error })}
      </Typography>
    );
  }
  if (!rows) {
    return (
      <Typography variant='body2' color='texts.secondary'>
        {t('Loading checks…')}
      </Typography>
    );
  }

  return (
    <>
      <S.Actions>
        <Input
          variant='search-lg'
          placeholder={t('Filter by check, column or table')}
          value={needle}
          onChange={e => setNeedle(e.target.value)}
          handleCleanUp={() => setNeedle('')}
        />
        <AppSelect
          id='check-status'
          label={t('Result')}
          value={status}
          onChange={e => setStatus(e.target.value as string)}
        >
          <MenuItem value='failing'>{t('Needs attention')}</MenuItem>
          <MenuItem value='passing'>{t('Passing')}</MenuItem>
          <MenuItem value='accepted'>{t('Accepted failures')}</MenuItem>
          <MenuItem value=''>{t('All')}</MenuItem>
        </AppSelect>
        <AppSelect
          id='check-contract'
          label={t('Contract')}
          value={contract}
          onChange={e => pick(e.target.value as string)}
        >
          <MenuItem value=''>{t('All contracts')}</MenuItem>
          {contracts.map(c => (
            <MenuItem key={c.id} value={c.id}>
              {c.title}
            </MenuItem>
          ))}
        </AppSelect>
      </S.Actions>
      <Typography variant='subtitle2' color='texts.secondary'>
        {t(
          '{{visible}} of {{total}} checks. Select one for what it does, its SQL and every run it has had.',
          { visible: visible.length, total: rows.length }
        )}
      </Typography>

      <div>
        <Table.HeaderContainer>
          <Table.Cell $flex={0.5}>
            <Typography variant='caption'>{t('Result')}</Typography>
          </Table.Cell>
          <Table.Cell $flex={3}>
            <Typography variant='caption'>{t('Check')}</Typography>
          </Table.Cell>
          <Table.Cell $flex={1.6}>
            <Typography variant='caption'>{t('Table · column')}</Typography>
          </Table.Cell>
          <Table.Cell $flex={1}>
            <Typography variant='caption'>{t('Dimension')}</Typography>
          </Table.Cell>
          <Table.Cell $flex={0.9}>
            <Typography variant='caption'>{t('Last run')}</Typography>
          </Table.Cell>
          <Table.Cell $flex={1} $justifyContent='flex-end'>
            <Typography variant='caption'>{t('Failing rows')}</Typography>
          </Table.Cell>
        </Table.HeaderContainer>

        <EmptyContentPlaceholder
          isContentEmpty={visible.length === 0}
          fullPage={false}
          text={t('No check matches these filters.')}
        />

        {visible.map(c => (
          <React.Fragment key={c.check_id}>
            <Table.RowContainer
              role='button'
              tabIndex={0}
              aria-expanded={selected === c.check_id}
              onClick={() => select(c.check_id)}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  select(c.check_id);
                }
              }}
              sx={{
                cursor: 'pointer',
                backgroundColor: theme =>
                  selected === c.check_id
                    ? theme.palette.backgrounds.secondary
                    : 'transparent',
                '&:focus-visible': { outline: '2px solid currentColor', outlineOffset: '-2px' },
              }}
            >
              <Table.Cell $flex={0.5}>
                <TestRunStatusItem size='small' typeName={runStatus(c.status)} count={1} />
              </Table.Cell>
              <Table.Cell $flex={3}>
                <div>
                  <Typography variant='body1'>{c.name ?? c.check_id}</Typography>
                  <Typography variant='caption' color='texts.secondary'>
                    {source.get(c.contract_id)?.title ?? c.contract_id}
                    {c.stale && ` · ${t('removed from the contract')}`}
                    {c.state !== 'open' && ` · ${t(c.state)}`}
                    {c.note ? `: ${c.note}` : ''}
                  </Typography>
                </div>
              </Table.Cell>
              <Table.Cell $flex={1.6}>
                <Typography variant='body2'>
                  {source.get(c.contract_id)?.source_table ?? '—'}
                  {c.field ? ` · ${c.field}` : ''}
                </Typography>
              </Table.Cell>
              <Table.Cell $flex={1}>
                <Typography variant='body2' color='texts.secondary'>
                  {t(c.dimension)}
                </Typography>
              </Table.Cell>
              <Table.Cell $flex={0.9}>
                <Typography variant='body2' color='texts.secondary'>
                  {when(c.run_at)}
                </Typography>
              </Table.Cell>
              <Table.Cell $flex={1} $justifyContent='flex-end'>
                <Typography
                  variant='body2'
                  color={c.failed_rows ? 'error.main' : 'texts.secondary'}
                >
                  {c.failed_rows}
                  {c.total_rows ? ` / ${c.total_rows}` : ''}
                </Typography>
              </Table.Cell>
            </Table.RowContainer>
            {selected === c.check_id && (
              <CheckDetail
                check={c}
                contract={source.get(c.contract_id)}
                onStatusSaved={() => setReloads(n => n + 1)}
              />
            )}
          </React.Fragment>
        ))}
      </div>
    </>
  );
};
