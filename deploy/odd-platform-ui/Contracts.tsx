import React, { useCallback, useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import { AppTabs } from 'components/shared/elements';
import type { Overview, RuleType } from './api';
import { getOverview, getRuleTypes } from './api';
import { ChecksTab } from './ChecksTab';
import { ContractsTab } from './ContractsTab';
import { Replication } from './Replication';
import { readParam, writeParams } from './shared';

/**
 * Contract quality, inside this platform's own Data Quality page.
 *
 * Three questions, three tabs, because they were one scroll and answering any
 * of them meant reading past the other two:
 *
 *   Checks       what is being tested, against which table, and what it found
 *   Contracts    the score each contract carries, and the schema behind it
 *   Replication  which of those tables is copied somewhere, and whether it is
 *                actually moving
 *
 * This platform reports quality and does not let anyone change it: there is no
 * "create test" anywhere in its UI, because a test arrives through ingestion
 * and belongs to whatever produced it. The contract behind these tests is
 * editable, though, and this is where that belongs -- next to the dashboard
 * that says a check failed, rather than on another port.
 *
 * Everything here talks to the contract service; see ./api.ts. Built from
 * `components/shared/elements` -- this platform's own design system -- rather
 * than bare HTML controls, so the panel looks like it belongs on the page it
 * lives on. See docs/adr/0009-fork-odd-platform-ui.md for why it is a fork.
 */

const TABS = ['Checks', 'Contracts', 'Replication'];

export const Contracts: React.FC = () => {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ruleTypes, setRuleTypes] = useState<RuleType[]>([]);
  // A check or contract in the URL is a link someone was sent; open the tab
  // that shows it rather than the default one.
  const [tab, setTab] = useState(() => {
    if (readParam('check')) return 0;
    if (readParam('contract')) return 1;
    return Math.max(0, TABS.indexOf(readParam('tab') ?? 'Checks'));
  });

  const load = useCallback(() => {
    getOverview()
      .then(setOverview)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    // The rule vocabulary belongs to the service; fetching it means adding a
    // rule kind is a change in one place rather than two.
    getRuleTypes()
      .then(r => setRuleTypes(r.rules))
      .catch(() => setRuleTypes([]));
  }, [load]);

  const changeTab = useCallback((next: number) => {
    setTab(next);
    writeParams({ tab: TABS[next] ?? null });
  }, []);

  if (error) {
    return (
      <Typography variant='body1' color='texts.secondary'>
        Contract service unreachable: {error}
      </Typography>
    );
  }
  if (!overview) {
    return (
      <Typography variant='body2' color='texts.secondary'>
        Loading contract quality…
      </Typography>
    );
  }

  return (
    <>
      <Typography variant='h4'>Contract quality</Typography>
      <AppTabs
        type='primary'
        selectedTab={tab}
        handleTabChange={changeTab}
        items={TABS.map(name => ({ name }))}
      />

      {tab === 0 && <ChecksTab contracts={overview.contracts} />}
      {tab === 1 && (
        <ContractsTab overview={overview} ruleTypes={ruleTypes} onSaved={load} />
      )}
      {tab === 2 && <Replication />}
    </>
  );
};

export default Contracts;
