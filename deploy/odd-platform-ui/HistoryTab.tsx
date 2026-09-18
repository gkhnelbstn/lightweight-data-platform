import React, { useEffect, useState } from 'react';
import { MenuItem, Typography } from '@mui/material';
import {
  AppSelect,
  EmptyContentPlaceholder,
  Input,
  LabeledInfoItem,
  Table,
} from 'components/shared/elements';
import type { ChangedKey, Version, VersionedContract } from './api';
import { getChangedKeys, getVersionedContracts, getVersions } from './api';
import { Code, readParam, useT, writeParams } from './shared';
import * as S from './Contracts.styles';

/**
 * What the warehouse kept that the source overwrote. Issue #27.
 *
 * `dim.customer` is Type 2: the ERP holds one row per customer and overwrites
 * it, so the day sales re-grades someone the old segment stops existing at the
 * source. Here it survives as a closed version, and `fct.orders` joins as of
 * the order date — which is what keeps a closed month's revenue from moving
 * between segments after the fact.
 *
 * That is the whole reason the table is shaped the way it is, and until now
 * the only evidence of it on any screen was four consistency rules passing.
 * Nothing here is a quality check — none of it is failing — so it does not
 * belong on the Checks tab. It is the answer to "why does this order say SMB
 * when the customer is ENT".
 */

export const HistoryTab: React.FC = () => {
  const t = useT();
  const [contracts, setContracts] = useState<VersionedContract[] | null>(null);
  const [contractId, setContractId] = useState('');
  const [changed, setChanged] = useState<ChangedKey[] | null>(null);
  const [summary, setSummary] = useState<VersionedContract | null>(null);
  const [key, setKey] = useState(() => readParam('key') ?? '');
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getVersionedContracts()
      .then(rows => {
        setContracts(rows);
        setContractId(prev => prev || rows[0]?.contract_id || '');
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!contractId) return;
    setChanged(null);
    getChangedKeys(contractId)
      .then(r => {
        setChanged(r.changed);
        setSummary(r.summary);
      })
      .catch((e: Error) => setError(e.message));
  }, [contractId]);

  useEffect(() => {
    if (!contractId || !key) {
      setVersions(null);
      return;
    }
    getVersions(contractId, key)
      .then(r => setVersions(r.versions))
      .catch((e: Error) => setError(e.message));
  }, [contractId, key]);

  const open = (next: string) => {
    setKey(next);
    writeParams({ key: next || null });
  };

  if (error) {
    return (
      <Typography variant='body1' color='texts.secondary'>
        {error}
      </Typography>
    );
  }
  if (!contracts) {
    return (
      <Typography variant='body2' color='texts.secondary'>
        {t('Loading…')}
      </Typography>
    );
  }
  if (contracts.length === 0) {
    return (
      <Typography variant='body2' color='texts.secondary'>
        <Code k='No contract declares versions. A table keeps history here when its contract has <c>valid_from</c>, <c>valid_to</c>, <c>is_current</c> and a <c>versionedBy</c> property naming the business key.' />
      </Typography>
    );
  }

  const contract = contracts.find(c => c.contract_id === contractId);

  return (
    <>
      <S.Actions>
        {contracts.length > 1 && (
          <AppSelect
            id='history-contract'
            label={t('Table')}
            value={contractId}
            onChange={e => {
              setContractId(e.target.value as string);
              open('');
            }}
          >
            {contracts.map(c => (
              <MenuItem key={c.contract_id} value={c.contract_id}>
                {c.title}
              </MenuItem>
            ))}
          </AppSelect>
        )}
        <Input
          variant='search-lg'
          placeholder={t('Open one by {{key}}', { key: contract?.key ?? t('key') })}
          value={key}
          onChange={e => open(e.target.value)}
          handleCleanUp={() => open('')}
        />
      </S.Actions>

      {contract?.unreachable ? (
        <Typography variant='body2' color='error.main'>
          {t('{{title}} is not reachable: {{error}}', {
            title: contract.title,
            error: contract.unreachable,
          })}
        </Typography>
      ) : (
        <Summary contract={contract} summary={summary} />
      )}

      {key && versions ? (
        <Versions versions={versions} contract={contract} keyValue={key} />
      ) : (
        <Changed rows={changed} label={contract?.key ?? t('key')} onOpen={open} />
      )}
    </>
  );
};

const Summary: React.FC<{
  contract?: VersionedContract;
  summary: VersionedContract | null;
}> = ({ contract, summary }) => {
  const t = useT();
  if (!contract || !summary) return null;
  return (
    <S.Facts>
      <LabeledInfoItem label={t('Versions')} labelWidth={4}>
        {t('{{versions}} rows for {{keys}} {{key}} values', {
          versions: summary.versions,
          keys: summary.keys,
          key: contract.key,
        })}
      </LabeledInfoItem>
      <LabeledInfoItem label={t('Closed')} labelWidth={4}>
        {summary.closed === 0
          ? t('none yet — the source has not changed since this table was built')
          : t('{{n}} versions the source has since overwritten', { n: summary.closed })}
      </LabeledInfoItem>
      <LabeledInfoItem label={t('Oldest opens')} labelWidth={4}>
        {summary.earliest}
        {summary.earliest?.startsWith('0001') &&
          ` — ${t('the sentinel. We know this is the oldest version we have, not when it began.')}`}
      </LabeledInfoItem>
      <LabeledInfoItem label={t('Tracked columns')} labelWidth={4}>
        {contract.attributes.join(', ')}
      </LabeledInfoItem>
    </S.Facts>
  );
};

const Changed: React.FC<{
  rows: ChangedKey[] | null;
  label: string;
  onOpen: (key: string) => void;
}> = ({ rows, label, onOpen }) => {
  const t = useT();
  if (!rows) {
    return (
      <Typography variant='body2' color='texts.secondary'>
        {t('Loading…')}
      </Typography>
    );
  }
  return (
    <div>
      <Typography variant='subtitle2' color='texts.secondary'>
        {t('The ones that changed. A row still on its first version has no history to show, so it is not listed.')}
      </Typography>
      <EmptyContentPlaceholder
        isContentEmpty={rows.length === 0}
        fullPage={false}
        text={t(
          'Nothing has changed yet. demo/medallion.py --with-history re-grades a few customers and rebuilds, which is what gives this table something to keep.'
        )}
      />
      {rows.length > 0 && (
        <Table.HeaderContainer>
          <Table.Cell $flex={1}>
            <Typography variant='caption'>{label}</Typography>
          </Table.Cell>
          <Table.Cell $flex={1}>
            <Typography variant='caption'>{t('Versions')}</Typography>
          </Table.Cell>
          <Table.Cell $flex={2}>
            <Typography variant='caption'>{t('Last changed')}</Typography>
          </Table.Cell>
        </Table.HeaderContainer>
      )}
      {rows.map(r => (
        <Table.RowContainer
          key={String(r.key)}
          role='button'
          tabIndex={0}
          onClick={() => onOpen(String(r.key))}
          onKeyDown={e => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onOpen(String(r.key));
            }
          }}
          sx={{
            cursor: 'pointer',
            '&:focus-visible': { outline: '2px solid currentColor', outlineOffset: '-2px' },
          }}
        >
          <Table.Cell $flex={1}>
            <Typography variant='body1'>{String(r.key)}</Typography>
          </Table.Cell>
          <Table.Cell $flex={1}>
            <Typography variant='body2'>{r.versions}</Typography>
          </Table.Cell>
          <Table.Cell $flex={2}>
            <Typography variant='body2' color='texts.secondary'>
              {r.last_changed ?? '—'}
            </Typography>
          </Table.Cell>
        </Table.RowContainer>
      ))}
    </div>
  );
};

/** One key's versions, oldest first. The interval is half-open --
 * [valid_from, valid_to) -- so a version's closing date and the next one's
 * opening date are the same day, which is what stops an as-of join matching
 * two rows. */
const Versions: React.FC<{
  versions: Version[];
  contract?: VersionedContract;
  keyValue: string;
}> = ({ versions, contract, keyValue }) => {
  const t = useT();
  if (versions.length === 0) {
    return (
      <Typography variant='body2' color='texts.secondary'>
        {t('No {{key}} {{value}} in {{table}}.', {
          key: contract?.key ?? t('row'),
          value: keyValue,
          table: contract?.table,
        })}
      </Typography>
    );
  }
  const columns = contract?.attributes ?? [];
  return (
    <div>
      <Typography variant='h4'>
        {contract?.key} {keyValue} ·{' '}
        {t(versions.length === 1 ? '{{n}} version' : '{{n}} versions', { n: versions.length })}
      </Typography>
      {versions.map(v => (
        <S.PropertyRow key={v.valid_from}>
          <S.Actions>
            <Typography variant='body1'>
              {v.valid_from} → {v.valid_to ?? t('now')}
            </Typography>
            <Typography variant='caption' color='texts.secondary'>
              {v.is_current ? t('current') : t('closed')}
              {v.changed.length > 0 &&
                ` · ${t('changed: {{columns}}', { columns: v.changed.join(', ') })}`}
            </Typography>
          </S.Actions>
          <S.Facts>
            {columns.map(c => (
              <LabeledInfoItem key={c} label={c} labelWidth={4}>
                {String(v[c] ?? '—')}
                {v.changed.includes(c) && ' ←'}
              </LabeledInfoItem>
            ))}
          </S.Facts>
        </S.PropertyRow>
      ))}
    </div>
  );
};
