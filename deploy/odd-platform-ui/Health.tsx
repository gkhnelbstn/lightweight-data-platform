import React from 'react';
import { Typography } from '@mui/material';
import { LabeledInfoItem } from 'components/shared/elements';
import type { Activity, Hub } from './api';
import { useT } from './shared';
import * as S from './Contracts.styles';

/**
 * Is this hub healthy, without opening a tab (#111).
 *
 * The card used to open on a table of flows, and every number a person asks
 * for first -- how many records, how many flows actually run, how much lost a
 * conflict, how much waits for a person -- was one tab-click away each. They
 * are counts the tab already had; what was missing was putting them where the
 * question is asked.
 *
 * The bars are changes that reached the hub per hour over the last day. A
 * count says "33 in the last hour" and cannot say whether that is normal; a
 * shape can.
 */

const HOURS = 24;

export const HubHealth: React.FC<{ hub: Hub }> = ({ hub }) => {
  const t = useT();
  const flows = hub.systems.flatMap(s => [...s.in, ...s.out]);
  const running = flows.filter(f => f.job?.status === 'RUNNING').length;
  const failed = flows.filter(f => f.job?.status === 'FAILED').length;
  const drift = hub.systems.flatMap(s => s.drift).length;
  const lag = Object.values(hub.arriving ?? {})
    .map(a =>
      a.landed_at && a.committed_at
        ? Math.max(0, (Date.parse(a.landed_at) - Date.parse(a.committed_at)) / 1000)
        : null
    )
    .filter((s): s is number => s !== null);

  return (
    <div>
      <S.Facts>
        <LabeledInfoItem label={t('Records')} labelWidth={4}>
          {hub.records ?? '—'}
        </LabeledInfoItem>
        <LabeledInfoItem label={t('Flows running')} labelWidth={4}>
          <Typography
            component='span'
            variant='body1'
            color={running === flows.length ? 'success.main' : 'error.main'}
          >
            {t('{{running}} of {{total}}', { running, total: flows.length })}
          </Typography>
          {failed > 0 && ` · ${t('{{n}} failed', { n: failed })}`}
          {drift > 0 && ` · ${t('{{n}} tables changed underneath', { n: drift })}`}
        </LabeledInfoItem>
        <LabeledInfoItem label={t('Slowest arrival')} labelWidth={4}>
          {lag.length ? t('{{s}} s after its commit', { s: Math.max(...lag).toFixed(1) }) : '—'}
        </LabeledInfoItem>
        <LabeledInfoItem label={t('Waiting for a decision')} labelWidth={4}>
          <Typography
            component='span'
            variant='body1'
            color={hub.held?.length ? 'error.main' : 'texts.primary'}
          >
            {hub.held?.length ?? 0}
          </Typography>
          {` · ${t('{{n}} lost a conflict', { n: hub.conflicts?.length ?? 0 })}`}
          {` · ${t('{{n}} deleted', { n: hub.deleted?.length ?? 0 })}`}
        </LabeledInfoItem>
      </S.Facts>
      <Arrivals activity={hub.activity ?? []} />
    </div>
  );
};

/** One bar per hour, last day, every system together: the question the shape
 * answers is "is anything arriving at all", and which system it came from is
 * the row below. Decorative -- the sentence under it is the fallback. */
const Arrivals: React.FC<{ activity: Activity[] }> = ({ activity }) => {
  const t = useT();
  if (!activity.length) {
    return (
      <Typography variant='caption' color='texts.secondary'>
        {t('Nothing has arrived in the last 24 hours.')}
      </Typography>
    );
  }
  const now = new Date();
  now.setMinutes(0, 0, 0);
  const hours: number[] = [];
  for (let i = HOURS - 1; i >= 0; i -= 1) {
    const at = new Date(now.getTime() - i * 3_600_000).toISOString().slice(0, 13);
    hours.push(
      activity.filter(a => a.hour.slice(0, 13) === at).reduce((sum, a) => sum + a.n, 0)
    );
  }
  const top = Math.max(...hours, 1);
  const w = 24 * 10;
  const h = 40;
  const total = hours.reduce((sum, n) => sum + n, 0);
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h} aria-hidden='true'>
        {hours.map((n, i) => (
          <rect
            // eslint-disable-next-line react/no-array-index-key
            key={i}
            x={i * 10}
            y={h - Math.max(1, (n / top) * (h - 2))}
            width={8}
            height={Math.max(1, (n / top) * (h - 2))}
            fill={n ? 'currentColor' : '#d5d5d5'}
            opacity={n ? 0.7 : 1}
          >
            <title>{`${n}`}</title>
          </rect>
        ))}
      </svg>
      <Typography variant='caption' color='texts.secondary' component='div'>
        {t('{{n}} changes reached the hub in the last 24 hours · busiest hour {{top}}', {
          n: total,
          top,
        })}
      </Typography>
    </div>
  );
};

export default HubHealth;
