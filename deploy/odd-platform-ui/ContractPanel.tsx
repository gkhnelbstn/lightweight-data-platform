import React, { useCallback, useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import {
  AppTabs,
  EmptyContentPlaceholder,
  LabeledInfoItem,
  Table,
  TestRunStatusItem,
} from 'components/shared/elements';
import type { AuditEntry, ContractDetail, ContractProperty, RuleType } from './api';
import { getContractAudit } from './api';
import { RawSqlRule, RuleBuilder, SyncRuleForm } from './RuleForms';
import { runStatus, when } from './shared';
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

const TABS = ['Schema', 'Checks', 'Add a rule', 'Replication', 'Changes'];

export const ContractPanel: React.FC<Props> = ({
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
      <div>
        <Typography variant='h4'>{detail.contract.title}</Typography>
        <Typography variant='caption' color='texts.secondary'>
          {detail.contract.source_table} ({detail.contract.server_type}) ·{' '}
          {detail.file} · {detail.properties.length} columns ·{' '}
          {detail.checks.length} checks
        </Typography>
      </div>

      <AppTabs
        type='secondary'
        selectedTab={tab}
        handleTabChange={setTab}
        items={TABS.map(name => ({ name }))}
      />

      {tab === 0 && <Definitions properties={detail.properties} />}
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

/** name, type, the identity/uniqueness markers a version-carrying dimension
 * like Type 2 SCD depends on, its classification, and the contract's own
 * description of it -- schema.yaml already writes these in full for the
 * columns that matter (dwh_dim_customer.odcs.yaml explains valid_from,
 * valid_to and is_current at length); this is the first place any of it
 * was shown rather than only read from the file. */
const Definitions: React.FC<{ properties: ContractProperty[] }> = ({ properties }) => {
  const flags = (p: ContractProperty) =>
    [p.primaryKey && 'primary key', p.unique && 'unique', p.required && 'required']
      .filter(Boolean)
      .join(' · ');

  return (
    <div>
      <EmptyContentPlaceholder
        isContentEmpty={properties.length === 0}
        fullPage={false}
        text='This contract declares no columns.'
      />
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

/** This contract's checks -- schema-derived and custom alike -- with what
 * each one last found. The full detail (SQL, every run, the failing rows)
 * is on the Checks tab, which is the same check under a stable id. */
const ContractChecks: React.FC<{ detail: ContractDetail }> = ({ detail }) => {
  const runsOf = (checkId: string) => detail.history.filter(h => h.check_id === checkId);

  return (
    <div>
      <Typography variant='subtitle2' color='texts.secondary'>
        Derived from the schema above, plus {detail.rules.length} rule
        {detail.rules.length === 1 ? '' : 's'} written into the contract.
      </Typography>
      <EmptyContentPlaceholder
        isContentEmpty={detail.checks.length === 0}
        fullPage={false}
        text='No checks recorded for this contract yet.'
      />
      {detail.checks.length > 0 && (
        <Table.HeaderContainer>
          <Table.Cell $flex={0.5}>
            <Typography variant='caption'>Result</Typography>
          </Table.Cell>
          <Table.Cell $flex={3}>
            <Typography variant='caption'>Check</Typography>
          </Table.Cell>
          <Table.Cell $flex={1}>
            <Typography variant='caption'>Column</Typography>
          </Table.Cell>
          <Table.Cell $flex={1}>
            <Typography variant='caption'>Last run</Typography>
          </Table.Cell>
          <Table.Cell $flex={1} $justifyContent='flex-end'>
            <Typography variant='caption'>Runs passed</Typography>
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
  const [mode, setMode] = useState(0);

  return (
    <>
      <Typography variant='subtitle2' color='texts.secondary'>
        Saved as an ODCS quality entry in {detail.file}, then re-run. The
        contract stays the source of truth; this is an editor for it.
      </Typography>
      {/* A tab, not a small link -- the SQL escape hatch existed before and
          was easy to miss because "Write SQL instead" was the only clue it
          was there. */}
      <AppTabs
        type='secondary'
        selectedTab={mode}
        handleTabChange={setMode}
        items={[{ name: 'Form' }, { name: 'Write SQL' }]}
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
          <Typography variant='h4'>Rules already written for this contract</Typography>
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

  return (
    <div>
      <EmptyContentPlaceholder
        isContentEmpty={!!entries && entries.length === 0}
        fullPage={false}
        text='Nothing has changed about this contract since the audit trail existed.'
      />
      {(entries ?? []).slice(0, 20).map((e, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <Typography key={i} variant='caption' color='texts.secondary' component='div'>
          {e.run_at} · {e.action} {e.change_type.replace('_', ' ')} &quot;{e.description}
          &quot;{e.caller_label ? ` — ${e.caller_label}` : ''}
        </Typography>
      ))}
    </div>
  );
};
