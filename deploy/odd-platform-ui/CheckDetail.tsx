import React, { useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import { Button, LabeledInfoItem, TestRunStatusItem } from 'components/shared/elements';
import type { CheckRow, CheckRun, ContractSummary, Sample } from './api';
import { getCheckHistory, getSample } from './api';
import { CheckStatusForm } from './CheckStatus';
import { Rows, runStatus, useT, when } from './shared';
import * as S from './Contracts.styles';

/**
 * One check, in full: what it looks at, when it last ran, what it found, the
 * SQL behind it and every run it has had.
 *
 * `check_type` is the honest source for "what kind of check is this" -- it is
 * what `datacontract test` derived the check from. It is *not* a key into
 * /api/rules/catalogue: that catalogue is the menu of rules someone can author
 * from the form ('not_null', 'between', ...), while these are the engine's own
 * kinds ('field_required', 'field_physical_type', ...), and a custom SQL rule
 * has no check_type at all. So the row's `name` is the description -- the
 * runner already writes a sentence -- and this only adds the structured
 * where.
 */

// English keys into the panel's catalogue; translated where shown.
const KINDS: Record<string, string> = {
  field_is_present: 'the column exists in the source',
  field_physical_type: 'the column has the type the contract declares',
  field_required: 'the column has no missing values',
  field_unique: 'the column has no duplicate values',
  primary_key_unique: 'the primary key has no duplicates',
  model_quality_sql: 'SQL written in the contract, counting bad rows',
};

interface Props {
  check: CheckRow;
  contract?: ContractSummary;
  onStatusSaved: () => void;
}

export const CheckDetail: React.FC<Props> = ({ check, contract, onStatusSaved }) => {
  const t = useT();
  const [history, setHistory] = useState<CheckRun[] | null>(null);
  const [sample, setSample] = useState<Sample | string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setHistory(null);
    getCheckHistory(check.check_id)
      .then(setHistory)
      .catch(() => setHistory([]));
  }, [check.check_id]);

  const showRows = async () => {
    if (sample) {
      setSample(null);
      return;
    }
    setBusy(true);
    try {
      setSample(await getSample(check.check_id));
    } catch (e) {
      setSample((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const kind = check.check_type ? KINDS[check.check_type] : undefined;

  return (
    <S.Panel>
      <S.Actions>
        <TestRunStatusItem size='large' typeName={runStatus(check.status)} count={1} />
        <Typography variant='h4'>{check.name ?? check.check_id}</Typography>
      </S.Actions>

      {check.stale && (
        <Typography variant='body2' color='texts.secondary'>
          {t(
            'This check is no longer in the contract. Its results are kept — a deleted rule does not delete the history it produced — but it will not run again.'
          )}
        </Typography>
      )}

      <S.Facts>
        <LabeledInfoItem label={t('What it checks')} labelWidth={4}>
          {kind ? t(kind) : check.check_type ?? t('custom SQL from the contract')}
        </LabeledInfoItem>
        <LabeledInfoItem label={t('Where')} labelWidth={4}>
          {contract?.source_table ?? check.contract_id}
          {check.field ? ` · ${check.field}` : ''}
          {contract?.server_type ? ` (${contract.server_type})` : ''}
        </LabeledInfoItem>
        <LabeledInfoItem label={t('Dimension')} labelWidth={4}>
          {t('{{dimension}} — this is the weight it carries in the score', {
            dimension: t(check.dimension),
          })}
        </LabeledInfoItem>
        <LabeledInfoItem label={t('Last run')} labelWidth={4}>
          {when(check.run_at)} · {check.run_at}
        </LabeledInfoItem>
        <LabeledInfoItem label={t('Result')} labelWidth={4}>
          {t('{{status}} · {{n}} failing', { status: t(check.status), n: check.failed_rows })}
          {check.total_rows
            ? ` ${t("of {{total}} rows in the day's window", { total: check.total_rows })}`
            : ''}
        </LabeledInfoItem>
        {check.reason && (
          <LabeledInfoItem label={t('Reason')} labelWidth={4}>
            {check.reason}
          </LabeledInfoItem>
        )}
        <LabeledInfoItem label={t('Check id')} labelWidth={4}>
          {check.check_id}
        </LabeledInfoItem>
      </S.Facts>

      {check.sql && (
        <div>
          <Typography variant='caption' color='texts.secondary'>
            {/* Only the SQL checks carry SQL here. For a derived one
                `datacontract test` records the assertion in words instead
                ("column 'country' exists in customer"), and calling that SQL
                would be a label that lies. */}
            {/^\s*(select|with)\b/i.test(check.sql)
              ? t('SQL, as the engine ran it')
              : t('What the engine asserted')}
          </Typography>
          <S.Sql>{check.sql}</S.Sql>
        </div>
      )}

      <CheckStatusForm check={check} onSaved={onStatusSaved} />

      <History runs={history} />

      <S.Actions>
        <Button
          buttonType='secondary-m'
          text={sample ? t('Hide failing rows') : t('Show failing rows')}
          isLoading={busy}
          onClick={showRows}
        />
      </S.Actions>
      {typeof sample === 'string' && (
        <Typography variant='body2' color='error.main'>
          {sample}
        </Typography>
      )}
      {sample && typeof sample !== 'string' && <Rows sample={sample} />}
    </S.Panel>
  );
};

/** Runs oldest to newest, as squares. Daily runs are incremental (invariant
 * 4), so each square is one day's verdict and a streak is readable at a
 * glance -- which a cumulative number would hide. */
const History: React.FC<{ runs: CheckRun[] | null }> = ({ runs }) => {
  const t = useT();
  if (!runs || runs.length === 0) return null;
  const passed = runs.filter(r => r.status === 'pass').length;
  return (
    <div>
      <Typography variant='caption' color='texts.secondary'>
        {t('{{passed}} of {{total}} runs passed', { passed, total: runs.length })}
      </Typography>
      {/* Decorative: the line above is the accessible summary, and the
          per-day detail is in the title tooltips, mouse-only. Same choice as
          the score sparkline on the Contracts tab. */}
      <S.Runs aria-hidden='true'>
        {runs.map(r => (
          <S.Run key={r.run_at} $status={r.status} title={t('{{at}}: {{status}}, {{n}} failing', {
              at: r.run_at,
              status: t(r.status),
              n: r.failed_rows,
            })} />
        ))}
      </S.Runs>
    </div>
  );
};
