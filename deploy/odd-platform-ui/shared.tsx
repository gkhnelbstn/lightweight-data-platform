import React from 'react';
import { Typography } from '@mui/material';
import { DataEntityRunStatus } from 'generated-sources';
import type { Sample } from './api';
import * as S from './Contracts.styles';

/** The few things every tab in this panel needs. */

export const fmt = (v: unknown) =>
  v === null || v === undefined ? '—' : Number(v).toFixed(3);

/** A timestamp as something a person reads at a glance. The API returns UTC
 * ISO strings; a run that happened today should not make anyone do date
 * arithmetic to notice that. */
export const when = (iso: string | null | undefined) => {
  if (!iso) return 'never';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  const hours = (Date.now() - at.getTime()) / 3_600_000;
  if (hours < 24) return `${Math.max(1, Math.round(hours))}h ago`;
  if (hours < 24 * 7) return `${Math.round(hours / 24)}d ago`;
  return at.toLocaleDateString();
};

/** Our three check outcomes onto the four this platform already has icons,
 * colors and a legend for. 'error' (could not run) reads as BROKEN, not
 * FAILED -- CLAUDE.md invariant 5: a check that could not run is not one
 * that failed. */
export const runStatus = (status: string): DataEntityRunStatus => {
  if (status === 'pass') return DataEntityRunStatus.SUCCESS;
  if (status === 'error') return DataEntityRunStatus.BROKEN;
  return DataEntityRunStatus.FAILED;
};

/**
 * Panel state in the URL, so a check someone is looking at can be sent to
 * someone else and the browser's Back button means something.
 *
 * `history.replaceState` rather than react-router's `useSearchParams`: this
 * panel is injected into upstream's page (ADR 0009) and a router hook would
 * tie the fork to their react-router version, where a break shows up as a
 * silent no-op instead of a `tsc` error. Only `dq_*` keys are touched --
 * upstream's own params on the same URL are copied through untouched.
 */
/** Read at module load, not at mount: this platform's own Data Quality route
 * rewrites the query string for its filters while it is coming up, and it
 * drops keys it does not know -- so by the time this panel renders, the
 * `dq_*` a link carried is already gone. Module evaluation happens before
 * any of that, and these values are only ever used to seed initial state. */
const OPENED_WITH = new URLSearchParams(window.location.search);

export const readParam = (key: string) => OPENED_WITH.get(`dq_${key}`);

export const writeParams = (values: Record<string, string | null>) => {
  const params = new URLSearchParams(window.location.search);
  Object.entries(values).forEach(([key, value]) => {
    if (value) params.set(`dq_${key}`, value);
    else params.delete(`dq_${key}`);
  });
  const search = params.toString();
  window.history.replaceState(
    null,
    '',
    `${window.location.pathname}${search ? `?${search}` : ''}`
  );
};

/** The rows a check failed on. */
export const Rows: React.FC<{ sample: Sample }> = ({ sample }) => (
  <div>
    <Typography variant='caption' color='texts.secondary'>
      {sample.run_at} · scope {sample.scope} · {sample.failed_rows} failing rows
      {sample.masked.length > 0 && ` · masked: ${sample.masked.join(', ')}`}
    </Typography>
    {sample.note ? (
      <Typography variant='body2'>{sample.note}</Typography>
    ) : sample.rows.length === 0 ? (
      <Typography variant='body2'>No rows in this window.</Typography>
    ) : (
      <S.Scroll>
        <S.Cells>
          <thead>
            <tr>
              {sample.columns.map((c, i) => (
                // eslint-disable-next-line react/no-array-index-key
                <th key={`${c}-${i}`}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sample.rows.map((row, ri) => (
              // eslint-disable-next-line react/no-array-index-key
              <tr key={ri}>
                {row.map((v, ci) => (
                  // eslint-disable-next-line react/no-array-index-key
                  <td key={ci}>{v === null ? 'null' : String(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </S.Cells>
      </S.Scroll>
    )}
  </div>
);
