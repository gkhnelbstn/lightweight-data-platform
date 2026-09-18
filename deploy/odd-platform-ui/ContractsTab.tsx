import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Typography } from '@mui/material';
import { Input, Table, TestRunStatusItem } from 'components/shared/elements';
import { DataEntityRunStatus } from 'generated-sources';
import type { ContractDetail, Overview, RuleType } from './api';
import { getContract } from './api';
import { ContractPanel } from './ContractPanel';
import { fmt, readParam, useT, writeParams } from './shared';
import * as S from './Contracts.styles';

/**
 * The contracts themselves: the score each one carries, and what is inside it.
 *
 * The score is the one number this platform has nowhere to put -- its run
 * model counts tests and has no numeric field -- so the trend line below is
 * ours and is the reason the standalone page existed until ADR 0009.
 */

interface Props {
  overview: Overview;
  ruleTypes: RuleType[];
  onSaved: () => void;
}

type SortKey = 'title' | 'score' | 'tests';

export const ContractsTab: React.FC<Props> = ({ overview, ruleTypes, onSaved }) => {
  const t = useT();
  const [selected, setSelected] = useState<string | null>(() => readParam('contract'));
  const [detail, setDetail] = useState<ContractDetail | null>(null);
  const [filterText, setFilterText] = useState('');
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' } | null>(null);
  const [reloads, setReloads] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);

  // Also runs for a contract restored from the URL on mount, which is what
  // makes a link to one open it rather than just highlight the row.
  // `reloads` is how a save re-reads it: the id has not changed, but what is
  // behind it has.
  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    getContract(selected).then(setDetail).catch(() => setDetail(null));
  }, [selected, reloads]);

  const saved = useCallback(() => {
    onSaved();
    setReloads(n => n + 1);
  }, [onSaved]);

  const select = useCallback((id: string) => {
    setSelected(prev => {
      const next = prev === id ? null : id;
      writeParams({ contract: next });
      return next;
    });
  }, []);

  // Client-side: eleven contracts today, and the backend keeps none of ODD's
  // own tsvector search machinery for our own YAML files. Revisit if the
  // count ever grows past what scanning in the browser can do instantly.
  const visibleContracts = useMemo(() => {
    const needle = filterText.trim().toLowerCase();
    let rows = !needle
      ? overview.contracts
      : overview.contracts.filter(
          c =>
            c.title.toLowerCase().includes(needle) ||
            c.id.toLowerCase().includes(needle) ||
            c.source_table?.toLowerCase().includes(needle)
        );
    if (sort) {
      rows = [...rows].sort((a, b) => {
        const va =
          sort.key === 'title'
            ? a.title
            : sort.key === 'score'
              ? (Number(a.score) ?? -1)
              : (a.checks_total ?? -1);
        const vb =
          sort.key === 'title'
            ? b.title
            : sort.key === 'score'
              ? (Number(b.score) ?? -1)
              : (b.checks_total ?? -1);
        const cmp = va < vb ? -1 : va > vb ? 1 : 0;
        return sort.dir === 'asc' ? cmp : -cmp;
      });
    }
    return rows;
  }, [overview.contracts, filterText, sort]);

  const toggleSort = useCallback((key: SortKey) => {
    setSort(prev =>
      prev?.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }
    );
  }, []);

  // A click opens the panel below a long list; without this, "select a
  // contract" and "see what you selected" can be two screens apart.
  useEffect(() => {
    if (selected && detail) panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [selected, detail]);

  const arrow = (key: SortKey) =>
    sort?.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : '';

  return (
    <>
      <Trend points={overview.trend} />

      <S.Actions>
        <Input
          variant='search-lg'
          placeholder={t('Filter by name, id or source table')}
          value={filterText}
          onChange={e => setFilterText(e.target.value)}
          handleCleanUp={() => setFilterText('')}
        />
      </S.Actions>
      <Typography variant='subtitle2' color='texts.secondary'>
        {t('Select a contract for its schema, its checks, and the forms that add a rule to it.')}
        {filterText &&
          ` ${t('Showing {{visible}} of {{total}}.', {
            visible: visibleContracts.length,
            total: overview.contracts.length,
          })}`}
      </Typography>

      <div>
        <Table.HeaderContainer>
          <Table.Cell $flex={2.2}>
            <S.SortableHeader onClick={() => toggleSort('title')}>
              {t('Contract')}
              {arrow('title')}
            </S.SortableHeader>
          </Table.Cell>
          <Table.Cell $flex={1.6}>
            <Typography variant='caption'>{t('Source')}</Typography>
          </Table.Cell>
          <Table.Cell $flex={0.8} $justifyContent='flex-end'>
            <S.SortableHeader onClick={() => toggleSort('score')}>
              {t('Score')}
              {arrow('score')}
            </S.SortableHeader>
          </Table.Cell>
          <Table.Cell $flex={0.8} $justifyContent='flex-end'>
            <Typography variant='caption'>SLA</Typography>
          </Table.Cell>
          <Table.Cell $flex={1.8}>
            <S.SortableHeader onClick={() => toggleSort('tests')}>
              {t('Tests')}
              {arrow('tests')}
            </S.SortableHeader>
          </Table.Cell>
        </Table.HeaderContainer>
        {visibleContracts.length === 0 && (
          <Typography variant='body2' color='texts.secondary' sx={{ py: 2 }}>
            {t('No contract matches "{{text}}".', { text: filterText })}
          </Typography>
        )}
        {visibleContracts.map(c => (
          <Table.RowContainer
            key={c.id}
            role='button'
            tabIndex={0}
            aria-expanded={selected === c.id}
            onClick={() => select(c.id)}
            onKeyDown={e => {
              // A row is a toggle, not a link -- Space and Enter both open it,
              // the way a real <button> would; Space's default (page scroll)
              // is the one thing worth preventing.
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                select(c.id);
              }
            }}
            sx={{
              cursor: 'pointer',
              backgroundColor: theme =>
                selected === c.id ? theme.palette.backgrounds.secondary : 'transparent',
              '&:focus-visible': { outline: '2px solid currentColor', outlineOffset: '-2px' },
            }}
          >
            <Table.Cell $flex={2.2}>
              <div>
                <Typography variant='body1'>{c.title}</Typography>
                <Typography variant='caption' color='texts.secondary'>
                  {c.id}
                </Typography>
              </div>
            </Table.Cell>
            <Table.Cell $flex={1.6}>
              <Typography variant='body2'>
                {c.source_table} ({c.server_type})
              </Typography>
            </Table.Cell>
            <Table.Cell $flex={0.8} $justifyContent='flex-end'>
              <Typography
                variant='h4'
                color={c.sla_met === false ? 'error.main' : 'success.main'}
              >
                {fmt(c.score)}
              </Typography>
            </Table.Cell>
            <Table.Cell $flex={0.8} $justifyContent='flex-end'>
              <Typography variant='body2' color='texts.secondary'>
                ≥ {fmt(c.sla_min)}
              </Typography>
            </Table.Cell>
            <Table.Cell $flex={1.8}>
              {c.checks_total == null ? (
                <Typography variant='body2' color='texts.secondary'>
                  —
                </Typography>
              ) : (
                <S.Actions>
                  <TestRunStatusItem
                    size='small'
                    typeName={DataEntityRunStatus.SUCCESS}
                    count={c.checks_total - (c.checks_failed ?? 0) - (c.checks_errored ?? 0)}
                  />
                  {!!c.checks_failed && (
                    <TestRunStatusItem
                      size='small'
                      typeName={DataEntityRunStatus.FAILED}
                      count={c.checks_failed}
                    />
                  )}
                  {!!c.checks_errored && (
                    <TestRunStatusItem
                      size='small'
                      typeName={DataEntityRunStatus.BROKEN}
                      count={c.checks_errored}
                    />
                  )}
                </S.Actions>
              )}
            </Table.Cell>
          </Table.RowContainer>
        ))}
      </div>

      {selected && detail && (
        <div ref={panelRef}>
          {/* key remounts the panel on contract change -- RuleBuilder,
              SyncRuleForm and friends seed a control from `detail` in
              useState's initialiser, which only runs once per mount and
              would otherwise carry the previous contract's picks over. */}
          <ContractPanel
            key={detail.contract.id}
            detail={detail}
            dimensions={overview.dimensions}
            ruleTypes={ruleTypes}
            onSaved={saved}
          />
        </div>
      )}
    </>
  );
};

/**
 * The daily score, as a line.
 *
 * ODD's own Data Quality page counts tests; it has nowhere to put a weighted
 * score over time, because its run model has no numeric field. This is the
 * one chart that was only on the standalone page, and the reason that page
 * could not simply be deleted until now.
 */
const Trend: React.FC<{ points: { run_at: string; score: string | number }[] }> = ({
  points,
}) => {
  const t = useT();
  if (points.length < 2) return null;
  const w = 420;
  const h = 72;
  const values = points.map(p => Number(p.score));
  const lo = Math.min(...values) - 0.01;
  const hi = 1;
  const x = (i: number) => 2 + (i * (w - 4)) / (points.length - 1);
  const y = (v: number) => h - 4 - ((v - lo) / Math.max(0.0001, hi - lo)) * (h - 12);
  const path = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const last = values[values.length - 1] ?? 0;

  return (
    <S.Actions>
      <div>
        <Typography variant='h1'>{last.toFixed(3)}</Typography>
        <Typography variant='caption' color='texts.secondary'>
          {t('{{n}} days · dimension-weighted', { n: points.length })}
        </Typography>
      </div>
      {/* Decorative: the number and day count beside it already say what this
          shows. Per-point detail lives in the title tooltips, mouse-only --
          the concise summary text is the accessible fallback, not the SVG. */}
      <svg
        viewBox={`0 0 ${w} ${h}`}
        width='100%'
        height={h}
        style={{ maxWidth: w }}
        aria-hidden='true'
      >
        <path d={path} fill='none' stroke='currentColor' strokeWidth='1.6' opacity={0.7} />
        {values.map((v, i) =>
          v < 0.95 ? (
            // eslint-disable-next-line react/no-array-index-key
            <circle key={i} cx={x(i)} cy={y(v)} r='2.4' fill='currentColor'>
              <title>{`${points[i]?.run_at}: ${v.toFixed(3)}`}</title>
            </circle>
          ) : null
        )}
      </svg>
    </S.Actions>
  );
};
