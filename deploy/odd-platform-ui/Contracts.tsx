import React, { useCallback, useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import { AppTabs } from 'components/shared/elements';
import type { Overview, RuleType } from './api';
import { getOverview, getRuleTypes } from './api';
import { ChecksTab } from './ChecksTab';
import { ContractsTab } from './ContractsTab';
import { HistoryTab } from './HistoryTab';
import { Replication } from './Replication';
import { readParam, showDashboard, writeParams } from './shared';
import * as S from './Contracts.styles';

/**
 * Contract quality, inside this platform's own Data Quality page.
 *
 * Three questions, three tabs, because they were one scroll and answering any
 * of them meant reading past the other two:
 *
 *   Checks       what is being tested, against which table, and what it found
 *   Contracts    the score each contract carries, and the schema behind it
 *   History      what a Type 2 table kept that the source overwrote
 *   Replication  which of those tables is copied somewhere, and whether it is
 *                actually moving
 *   Platform     this platform's own donuts, which this panel hides while it
 *                is showing one of its own tabs
 *
 * That last tab is why the patch tags upstream's two sections with a class
 * (deploy/odd-platform-dq-panel.mjs) instead of deleting them: the page used
 * to say "281 tests, 271 passing" twice, in two visual languages, and the
 * question anyone opens it with -- which check failed, against what -- was
 * below both. Their summary is still a click away and still theirs.
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

const TABS = ['Checks', 'Contracts', 'History', 'Replication', 'Platform overview'];

export const Contracts: React.FC = () => {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ruleTypes, setRuleTypes] = useState<RuleType[]>([]);
  // A check or contract in the URL is a link someone was sent; open the tab
  // that shows it rather than the default one.
  const [tab, setTab] = useState(() => {
    if (readParam('check')) return 0;
    if (readParam('contract')) return 1;
    if (readParam('key')) return 2;
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

  // Upstream's own sections live outside this component, further down their
  // page; this is the only thing that touches them. They come back when the
  // contract service is unreachable too -- hiding this platform's own working
  // dashboard behind our error message would make our outage look like theirs.
  useEffect(() => {
    showDashboard(tab === TABS.length - 1 || !overview || !!error);
  }, [tab, overview, error]);

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
    <S.Shell>
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
      {tab === 2 && <HistoryTab />}
      {tab === 3 && <Replication />}
      {tab === 4 && (
        <Typography variant='subtitle2' color='texts.secondary'>
          This platform&apos;s own dashboard, below — table health, the test
          results breakdown and the category table, counted from everything it
          has ingested rather than from the contracts.
        </Typography>
      )}
    </S.Shell>
  );
};

export default Contracts;
