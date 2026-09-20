import React, { useCallback, useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import {
  AppTabs,
  Button,
  EmptyContentPlaceholder,
  LabeledInfoItem,
  Table,
  TestRunStatusItem,
} from 'components/shared/elements';
import type {
  AuditEntry,
  ColumnProfile,
  ContractDetail,
  ContractProperty,
  RuleType,
} from './api';
import { getContractAudit, getRun, startRun } from './api';
import type { RunState } from './api';
import { RawSqlRule, RuleBuilder, SyncRuleForm } from './RuleForms';
import { runStatus, useT, when } from './shared';
import * as S from './Contracts.styles';

/**
 * One contract, in sections rather than in one column.
 *
 * Everything here was already on the page before; it was a single scroll of
 * definitions, checks, rules, two authoring forms and an audit trail, and the
 * thing anyone actually came for was somewhere in the middle of it. These are
 * the same components behind tabs, which is what makes "what does this
 * contract check" a click rather than a search.
 */

interface Props {
  detail: ContractDetail;
  dimensions: string[];
  ruleTypes: RuleType[];
  onSaved: () => void;
}

// English keys into the panel's catalogue; translated where shown.
const TABS = ['Schema', 'Checks', 'Add a rule', 'Replication', 'Changes'];

export const ContractPanel: React.FC<Props> = ({
  detail,
  dimensions,
  ruleTypes,
  onSaved,
}) => {
  const t = useT();
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
      <div>
        <Typography variant='h4'>{detail.contract.title}</Typography>
        <Typography variant='caption' color='texts.secondary'>
          {detail.contract.source_table} ({detail.contract.server_type}) · {detail.file} ·{' '}
          {t('{{columns}} columns · {{checks}} checks', {
            columns: detail.properties.length,
            checks: detail.checks.length,
          })}
        </Typography>
      </div>
      <RunNow contractId={detail.contract.id} onFinished={saved} />

      <AppTabs
        type='secondary'
        selectedTab={tab}
        handleTabChange={setTab}
        items={TABS.map(name => ({ name: t(name) }))}
      />

      {tab === 0 && (
        <Definitions properties={detail.properties} profile={detail.profile} />
      )}
      {tab === 1 && <ContractChecks detail={detail} />}
      {tab === 2 && (
        <AddRule
          detail={detail}
          dimensions={dimensions}
          ruleTypes={ruleTypes}
          onSaved={saved}
        />
      )}
      {tab === 3 && <SyncRuleForm detail={detail} onSaved={saved} />}
      {tab === 4 && <AuditTrail key={auditKey} contractId={detail.contract.id} />}
    </S.Panel>
  );
};

/**
 * The other occasion for a run: the data was just fixed, or a rule was just
 * added, and the schedule is tomorrow (#113). It starts the same run
 * `core/runner.py` starts -- today, this contract -- and then watches it,
 * because it takes as long as the checks take.
 */
const RunNow: React.FC<{ contractId: string; onFinished: () => void }> = ({
  contractId,
  onFinished,
}) => {
  const t = useT();
  const [run, setRun] = useState<RunState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRun(null);
    setError(null);
    getRun(contractId).then(setRun).catch(() => undefined);
  }, [contractId]);

  useEffect(() => {
    if (run?.state !== 'running') return undefined;
    const timer = window.setInterval(() => {
      getRun(contractId)
        .then(next => {
          setRun(next);
          // The results are in the database now, so the panel is stale.
          if (next.state !== 'running') onFinished();
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [run?.state, contractId, onFinished]);

  const start = async () => {
    setError(null);
    try {
      setRun(await startRun(contractId));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const said =
    run?.state === 'running'
      ? t('Running. It takes as long as the checks take.')
      : run?.state === 'done' && run.result
        ? t('{{failed}} of {{total}} failing, {{errored}} could not run · score {{score}}', {
            failed: run.result.failed,
            total: run.result.total,
            errored: run.result.errored,
            score: run.result.score.toFixed(3),
          })
        : null;

  return (
    <S.Actions>
      <Button
        buttonType='secondary-m'
        text={t('Run the checks now')}
        isLoading={run?.state === 'running'}
        onClick={start}
      />
      {said && (
        <Typography variant='caption' color='texts.secondary'>
          {said}
        </Typography>
      )}
      {(error || run?.state === 'failed') && (
        <Typography variant='caption' color='error.main'>
          {error ?? run?.error}
        </Typography>
      )}
    </S.Actions>
  );
};

/** name, type, the identity/uniqueness markers a version-carrying dimension
 * like Type 2 SCD depends on, its classification, and the contract's own
 * description of it -- schema.yaml already writes these in full for the
 * columns that matter (dwh_dim_customer.odcs.yaml explains valid_from,
 * valid_to and is_current at length); this is the first place any of it
 * was shown rather than only read from the file. */
const Definitions: React.FC<{
  properties: ContractProperty[];
  profile?: ColumnProfile[];
}> = ({ properties, profile }) => {
  const t = useT();
  const measured = new Map((profile ?? []).map(p => [p.column_name, p]));
  const flags = (p: ContractProperty) =>
    [p.primaryKey && t('primary key'), p.unique && t('unique'), p.required && t('required')]
      .filter(Boolean)
      .join(' · ');

  return (
    <div>
      <EmptyContentPlaceholder
        isContentEmpty={properties.length === 0}
        fullPage={false}
        text={t('This contract declares no columns.')}
      />
      {properties.map(p => (
        <S.PropertyRow key={p.name}>
          <LabeledInfoItem label={p.name} labelWidth={3}>
            {p.logicalType ?? p.physicalType ?? '—'}
            {flags(p) && ` · ${flags(p)}`}
            {p.classification &&
              ` · ${t('classified: {{as}}', { as: p.classification })}`}
          </LabeledInfoItem>
          {p.description && (
            <Typography variant='caption' color='texts.secondary'>
              {p.description}
            </Typography>
          )}
          <Profile row={measured.get(p.name)} />
        </S.PropertyRow>
      ))}
    </div>
  );
};

/**
 * What the last run actually found in this column. Issue #30.
 *
 * The same measurement `field_required` makes, continuous rather than
 * pass/fail: a column at 9% null passes every check it has and is three days
 * from breaking one, and that is the only thing on this page that would say
 * so. Of the day's window, not the whole table -- see core/profile.py.
 */
const Profile: React.FC<{ row?: ColumnProfile }> = ({ row }) => {
  const t = useT();
  if (!row || row.rows === 0) return null;
  const pct = (n: number, of: number) => `${((n / of) * 100).toFixed(1)}%`;
  // A fraction, not a count: yesterday's window is a different size, so "4
  // nulls, was 2" says nothing and "9.1%, was 4.5%" says the thing.
  const before =
    row.prev_nulls !== null && row.prev_rows ? row.prev_nulls / row.prev_rows : null;
  const now = row.nulls / row.rows;
  const rising = before !== null && now > before;

  return (
    <Typography variant='caption' color='texts.secondary' component='div'>
      {t('{{n}} rows', { n: row.rows })} ·{' '}
      <Typography
        variant='caption'
        component='span'
        color={row.nulls > 0 ? 'warning.main' : 'texts.secondary'}
      >
        {t('{{n}} null ({{pct}})', { n: row.nulls, pct: pct(row.nulls, row.rows) })}
      </Typography>
      {rising &&
        before !== null &&
        ` ${t('↑ from {{pct}}', { pct: `${(before * 100).toFixed(1)}%` })}`}
      {' · '}
      {t('{{n}} distinct', { n: row.distinct_count })}
      {row.distinct_count === row.rows && ` — ${t('every row')}`}
    </Typography>
  );
};

/** This contract's checks -- schema-derived and custom alike -- with what
 * each one last found. The full detail (SQL, every run, the failing rows)
 * is on the Checks tab, which is the same check under a stable id. */
const ContractChecks: React.FC<{ detail: ContractDetail }> = ({ detail }) => {
  const t = useT();
  const runsOf = (checkId: string) => detail.history.filter(h => h.check_id === checkId);

  return (
    <div>
      <Typography variant='subtitle2' color='texts.secondary'>
        {t(
          detail.rules.length === 1
            ? 'Derived from the schema above, plus {{n}} rule written into the contract.'
            : 'Derived from the schema above, plus {{n}} rules written into the contract.',
          { n: detail.rules.length }
        )}
      </Typography>
      <EmptyContentPlaceholder
        isContentEmpty={detail.checks.length === 0}
        fullPage={false}
        text={t('No checks recorded for this contract yet.')}
      />
      {detail.checks.length > 0 && (
        <Table.HeaderContainer>
          <Table.Cell $flex={0.5}>
            <Typography variant='caption'>{t('Result')}</Typography>
          </Table.Cell>
          <Table.Cell $flex={3}>
            <Typography variant='caption'>{t('Check')}</Typography>
          </Table.Cell>
          <Table.Cell $flex={1}>
            <Typography variant='caption'>{t('Column')}</Typography>
          </Table.Cell>
          <Table.Cell $flex={1}>
            <Typography variant='caption'>{t('Last run')}</Typography>
          </Table.Cell>
          <Table.Cell $flex={1} $justifyContent='flex-end'>
            <Typography variant='caption'>{t('Runs passed')}</Typography>
          </Table.Cell>
        </Table.HeaderContainer>
      )}
      {detail.checks.map(c => {
        const runs = runsOf(c.check_id);
        return (
          <Table.RowContainer key={c.check_id}>
            <Table.Cell $flex={0.5}>
              <TestRunStatusItem size='small' typeName={runStatus(c.status)} count={1} />
            </Table.Cell>
            <Table.Cell $flex={3}>
              <div>
                <Typography variant='body2'>{c.name ?? c.check_id}</Typography>
                {c.reason && (
                  <Typography variant='caption' color='texts.secondary'>
                    {c.reason}
                  </Typography>
                )}
              </div>
            </Table.Cell>
            <Table.Cell $flex={1}>
              <Typography variant='body2' color='texts.secondary'>
                {c.field ?? '—'}
              </Typography>
            </Table.Cell>
            <Table.Cell $flex={1}>
              <Typography variant='body2' color='texts.secondary'>
                {when(c.run_at)}
              </Typography>
            </Table.Cell>
            <Table.Cell $flex={1} $justifyContent='flex-end'>
              <Typography variant='body2' color='texts.secondary'>
                {runs.length === 0
                  ? '—'
                  : `${runs.filter(r => r.status === 'pass').length}/${runs.length}`}
              </Typography>
            </Table.Cell>
          </Table.RowContainer>
        );
      })}
    </div>
  );
};

const AddRule: React.FC<Props> = ({ detail, dimensions, ruleTypes, onSaved }) => {
  const t = useT();
  const [mode, setMode] = useState(0);

  return (
    <>
      <Typography variant='subtitle2' color='texts.secondary'>
        {t(
          'Saved as an ODCS quality entry in {{file}}, then re-run. The contract stays the source of truth; this is an editor for it.',
          { file: detail.file }
        )}
      </Typography>
      {/* A tab, not a small link -- the SQL escape hatch existed before and
          was easy to miss because "Write SQL instead" was the only clue it
          was there. */}
      <AppTabs
        type='secondary'
        selectedTab={mode}
        handleTabChange={setMode}
        items={[{ name: t('Form') }, { name: t('Write SQL') }]}
      />
      {mode === 1 ? (
        <RawSqlRule detail={detail} dimensions={dimensions} onSaved={onSaved} />
      ) : (
        <RuleBuilder
          detail={detail}
          dimensions={dimensions}
          ruleTypes={ruleTypes}
          onSaved={onSaved}
        />
      )}

      {detail.rules.length > 0 && (
        <div>
          <Typography variant='h4'>{t('Rules already written for this contract')}</Typography>
          {detail.rules.map(rule => (
            <div key={rule.description}>
              <Typography variant='body1'>
                {rule.description}{' '}
                <Typography variant='caption' color='texts.secondary'>
                  {rule.dimension && t(rule.dimension)}
                </Typography>
              </Typography>
              <S.Sql>{rule.query}</S.Sql>
            </div>
          ))}
        </div>
      )}
    </>
  );
};

/**
 * What changed about this contract's rules, and when -- never who. See
 * issue #12: there is no identity provider (ADR 0010), so this reads
 * contract_audit rather than pretending to know a person made the change.
 */
const AuditTrail: React.FC<{ contractId: string }> = ({ contractId }) => {
  const t = useT();
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);

  useEffect(() => {
    setEntries(null);
    getContractAudit(contractId)
      .then(setEntries)
      .catch(() => setEntries([]));
  }, [contractId]);

  return (
    <div>
      <EmptyContentPlaceholder
        isContentEmpty={!!entries && entries.length === 0}
        fullPage={false}
        text={t('Nothing has changed about this contract since the audit trail existed.')}
      />
      {(entries ?? []).slice(0, 20).map((e, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <Typography key={i} variant='caption' color='texts.secondary' component='div'>
          {e.run_at} · {t(e.action)} {t(e.change_type.replace('_', ' '))} &quot;{e.description}
          &quot;{e.caller_label ? ` — ${e.caller_label}` : ''}
        </Typography>
      ))}
    </div>
  );
};
