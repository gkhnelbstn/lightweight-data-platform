import React from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { Typography } from '@mui/material';
import { DataEntityRunStatus } from 'generated-sources';
import i18n from 'locales/i18n';
import type { Sample } from './api';
import { LDP } from './nav';
import * as S from './Contracts.styles';

/** The few things every tab in this panel needs. */

/**
 * The panel's words, in ODD's own i18n instance and its own namespace.
 *
 * ODD's language picker switches this panel too, because it is the same
 * instance (issue #44). The namespace is ours so that a phrase both of us use,
 * such as 'All' or 'Result', never changes their wording or ours. Keys are the
 * English phrases, which is upstream's convention, so English needs no
 * catalogue: a key with no translation renders as itself. The catalogue is
 * registered in ./nav, which ODD's menu loads on every page.
 */
const NS = LDP;

type Vars = Record<string, unknown>;
// React escapes what it renders; i18next escaping as well would show a
// contract titled `A & B` as `A &amp; B`.
const RAW = { interpolation: { escapeValue: false } };

/** For a component: re-renders when the language changes. */
export const useT = () => {
  const { t } = useTranslation(NS);
  return (key: string, vars?: Vars) => t(key, { ...RAW, ...vars });
};

/** A sentence with markup in it: `<c>...</c>` in the key becomes `<code>`. */
export const Code: React.FC<{ k: string; vars?: Vars }> = ({ k, vars }) => (
  <Trans ns={NS} i18nKey={k} values={vars} components={{ c: <code /> }} />
);

/** For a helper that runs during a render the hook above already subscribed. */
export const tr = (key: string, vars?: Vars) =>
  i18n.t(key, { ns: NS, ...RAW, ...vars });

export const fmt = (v: unknown) =>
  v === null || v === undefined ? '—' : Number(v).toFixed(3);

/** A timestamp as something a person reads at a glance. The API returns UTC
 * ISO strings; a run that happened today should not make anyone do date
 * arithmetic to notice that. */
export const when = (iso: string | null | undefined) => {
  if (!iso) return tr('never');
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  const hours = (Date.now() - at.getTime()) / 3_600_000;
  if (hours < 24) return tr('{{n}}h ago', { n: Math.max(1, Math.round(hours)) });
  if (hours < 24 * 7) return tr('{{n}}d ago', { n: Math.round(hours / 24) });
  return at.toLocaleDateString(i18n.language);
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

/** What the panel currently has in the URL, seeded from the link it was opened
 * with. Kept here as well as in the address bar because the address bar is not
 * ours alone -- see `keepOurKeys` below. */
const OURS = new Map<string, string>(
  [...OPENED_WITH.entries()].filter(([key]) => key.startsWith('dq_'))
);

const render = (params: URLSearchParams) => {
  const search = params.toString();
  return `${window.location.pathname}${search ? `?${search}` : ''}`;
};

/**
 * Put our keys back into any URL the page writes.
 *
 * Reading them at module load is enough to *open* on the right check, and it
 * was all #21 needed. It is not enough afterwards: upstream's filter sidebar
 * writes the query string again on every change, from its own state, and our
 * keys are not in that state -- so the address bar stops matching the screen
 * and a person copying it shares the wrong thing (issue #24).
 *
 * So `history.pushState` and `replaceState` are wrapped once, and every URL
 * that goes through them gets the `dq_*` we currently hold merged back in.
 * This is a smaller intrusion than it looks and a much smaller one than the
 * alternatives: it never blocks or rewrites *their* keys, it does not care
 * which router they use, and if upstream ever stops dropping unknown keys it
 * becomes a no-op rather than a conflict. `useSearchParams` was the other
 * option and ADR 0009 argues against it -- a router hook breaks silently
 * where this breaks not at all.
 */
const keepOurKeys = () => {
  const history = window.history as History & { __dqPatched?: boolean };
  if (history.__dqPatched) return;
  history.__dqPatched = true;
  (['pushState', 'replaceState'] as const).forEach(name => {
    const original = history[name].bind(history);
    history[name] = ((state: unknown, title: string, url?: string | URL | null) => {
      if (url == null || OURS.size === 0) return original(state, title, url as never);
      const next = new URL(String(url), window.location.origin);
      OURS.forEach((value, key) => {
        if (!next.searchParams.has(key)) next.searchParams.set(key, value);
      });
      return original(state, title, `${next.pathname}${next.search}`);
    }) as typeof history.pushState;
  });
};

export const writeParams = (values: Record<string, string | null>) => {
  const params = new URLSearchParams(window.location.search);
  Object.entries(values).forEach(([key, value]) => {
    if (value) {
      params.set(`dq_${key}`, value);
      OURS.set(`dq_${key}`, value);
    } else {
      params.delete(`dq_${key}`);
      OURS.delete(`dq_${key}`);
    }
  });
  keepOurKeys();
  window.history.replaceState(null, '', render(params));
};

// Their route rewrites the query string while it comes up, before anything
// here has had a reason to write one, so the wrap has to be in place from the
// start rather than from the first `writeParams`.
keepOurKeys();

/** The class `deploy/odd-platform-dq-panel.mjs` puts on this platform's own
 * two dashboard sections, so the panel can show and hide them. Kept in sync
 * by hand with the same constant there -- two strings, one meaning, and the
 * build fails on the patch side if the anchor ever moves. */
const DASHBOARD = 'odd-dq-dashboard';

/**
 * Show or hide the platform's own Table Health / Test Results donuts.
 *
 * Done through the DOM rather than through props because the alternative is
 * JSX surgery on upstream's return statement, and a patch that rewrites an
 * expression breaks into a syntax error while a patch that adds a class
 * attribute cannot. `display` is set inline because their section is a
 * styled `div` with `display: flex`, which outranks the `hidden` attribute.
 */
export const showDashboard = (show: boolean) => {
  document.querySelectorAll<HTMLElement>(`.${DASHBOARD}`).forEach(el => {
    el.style.display = show ? '' : 'none';
  });
};

/** The rows a check failed on. */
export const Rows: React.FC<{ sample: Sample }> = ({ sample }) => {
  const t = useT();
  return (
    <div>
      <Typography variant='caption' color='texts.secondary'>
        {t('{{at}} · scope {{scope}} · {{n}} failing rows', {
          at: sample.run_at,
          scope: sample.scope,
          n: sample.failed_rows,
        })}
        {sample.masked.length > 0 &&
          ` · ${t('masked: {{columns}}', { columns: sample.masked.join(', ') })}`}
      </Typography>
      {sample.note ? (
        <Typography variant='body2'>{sample.note}</Typography>
      ) : sample.rows.length === 0 ? (
        <Typography variant='body2'>{t('No rows in this window.')}</Typography>
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
};
