import React, { useEffect, useState } from 'react';
import { Typography, useTheme } from '@mui/material';
import type { QualityOverview as Data } from './api';
import { getQualityOverview } from './api';
import { useT, when } from './shared';
import * as S from './Contracts.styles';

/**
 * What the contracts say about quality today, above this platform's own
 * dashboard (#145). The donuts below count every test ingested; the questions
 * this answers are the ones the page is opened with -- which contracts break
 * their SLA, is it getting better, which kind of wrong, and for how long.
 *
 * Drawn with inline SVG like the hub's arrivals chart, not a chart library:
 * two small shapes do not justify a dependency in a fork (ADR 0009).
 */

const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);

export const QualityOverview: React.FC = () => {
  const t = useT();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getQualityOverview()
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <Typography color='error.main'>{error}</Typography>;
  if (!data) return <Typography color='texts.secondary'>{t('Loading…')}</Typography>;

  const k = data.kpis;
  return (
    <>
      <Typography variant='h5'>{t('Quality at a glance')}</Typography>
      <Typography variant='caption' color='texts.secondary'>
        {t('Latest run {{when}}. Scores are the contracts’ own; checks that could not run are counted apart.', {
          when: data.as_of ? when(data.as_of) : '—',
        })}
      </Typography>
      <S.Kpis>
        <Kpi label={t('Contracts at SLA')} value={`${k.at_sla} / ${k.contracts}`}
             tone={k.at_sla === k.contracts ? 'good' : 'bad'} />
        <Kpi label={t('Failing checks')} value={`${k.failing} / ${k.checks}`}
             tone={k.failing ? 'bad' : 'good'} />
        <Kpi label={t('Newly failing')} value={k.newly_failing} tone={k.newly_failing ? 'bad' : 'good'} />
        <Kpi label={t('Could not run')} value={k.errored} tone={k.errored ? 'bad' : 'plain'} />
        <Kpi label={t('Accepted')} value={k.accepted} />
      </S.Kpis>

      <S.Panel>
          <Typography variant='h5'>{t('Contracts, worst first')}</Typography>
          <S.Scroll>
            <S.Cells>
              <thead>
                <tr>
                  <th>{t('Contract')}</th>
                  <th>{t('Score')}</th>
                  <th>{t('Change')}</th>
                  <th>{t('Failing')}</th>
                </tr>
              </thead>
              <tbody>
                {data.contracts.map(c => (
                  <tr key={c.id}>
                    <td>
                      <a href={`?dq_tab=Contracts&dq_contract=${encodeURIComponent(c.id)}`}>
                        {c.title}
                      </a>
                      <Typography variant='caption' color='texts.secondary' component='div'>
                        {c.domain ?? '—'}
                      </Typography>
                    </td>
                    <td><ScoreBar score={c.score} sla={c.sla_min} /></td>
                    <td><Delta now={c.score} before={c.previous} /></td>
                    <td>{`${c.checks_failed} / ${c.checks_total}`}</td>
                  </tr>
                ))}
              </tbody>
            </S.Cells>
          </S.Scroll>
      </S.Panel>

      <S.Split>
        <S.Panel>
          <Typography variant='h5'>{t('Failing by dimension')}</Typography>
          {data.dimensions.map(d => (
            <div key={d.dimension}>
              <Typography variant='body2'>
                {t(d.dimension)}
                <Typography component='span' variant='caption' color='texts.secondary'>
                  {` · ${t('weight {{w}}', { w: d.weight })} · ${d.failing} / ${d.total}`}
                </Typography>
              </Typography>
              <Bar part={d.failing} whole={d.total} />
            </div>
          ))}
          <Typography variant='h5'>{t('How long it has been failing')}</Typography>
          <S.Kpis>
            <Kpi label={t('Since the last run')} value={data.aging.new} />
            <Kpi label={t('Up to a week')} value={data.aging.week} />
            <Kpi label={t('Up to a month')} value={data.aging.month} />
            <Kpi label={t('Longer')} value={data.aging.older} tone={data.aging.older ? 'bad' : 'plain'} />
          </S.Kpis>
        </S.Panel>
      </S.Split>

      <S.Split>
        <S.Panel>
          <Typography variant='h5'>{t('Score by domain')}</Typography>
          <DomainTrend domains={data.domains} />
        </S.Panel>
        <S.Panel>
          <Typography variant='h5'>{t('Most rows affected')}</Typography>
          {data.worst.map(w => (
            <S.PropertyRow key={w.check_id}>
              <Typography variant='body2'>
                <a href={`?dq_tab=Checks&dq_check=${encodeURIComponent(w.check_id)}`}>
                  {w.name ?? w.check_id}
                </a>
              </Typography>
              <Typography variant='caption' color='texts.secondary'>
                {t('{{rows}} of {{total}} rows · {{contract}} · failing since {{since}}', {
                  rows: w.failed_rows,
                  total: w.total_rows,
                  contract: w.contract_id,
                  since: w.since ? when(w.since) : '—',
                })}
              </Typography>
            </S.PropertyRow>
          ))}
        </S.Panel>
      </S.Split>
    </>
  );
};

const Kpi: React.FC<{ label: string; value: React.ReactNode; tone?: 'good' | 'bad' | 'plain' }> = ({
  label,
  value,
  tone = 'plain',
}) => (
  <S.Kpi $tone={tone}>
    <Typography variant='caption' color='texts.secondary'>{label}</Typography>
    <Typography variant='h2'>{value}</Typography>
  </S.Kpi>
);

/** The score as a bar, with the SLA floor as a tick: below the tick is red. */
const ScoreBar: React.FC<{ score: number | null; sla: number | null }> = ({ score, sla }) => {
  const theme = useTheme();
  const w = 120;
  const below = score != null && sla != null && score < sla;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <svg width={w} height={10} aria-hidden='true'>
        <rect width={w} height={10} rx={2} fill='currentColor' opacity={0.08} />
        {score != null && (
          <rect width={w * score} height={10} rx={2}
                fill={below ? theme.palette.error.main : theme.palette.success.main} />
        )}
        {sla != null && <rect x={w * sla - 1} width={2} height={10} fill='currentColor' />}
      </svg>
      {pct(score)}
    </span>
  );
};

const Delta: React.FC<{ now: number | null; before: number | null }> = ({ now, before }) => {
  if (now == null || before == null) return <span>—</span>;
  const d = (now - before) * 100;
  if (Math.abs(d) < 0.05) return <span>0.0</span>;
  return (
    <Typography component='span' variant='body2' color={d > 0 ? 'success.main' : 'error.main'}>
      {`${d > 0 ? '▲' : '▼'} ${Math.abs(d).toFixed(1)}`}
    </Typography>
  );
};

const Bar: React.FC<{ part: number; whole: number }> = ({ part, whole }) => {
  const theme = useTheme();
  return (
  <svg width='100%' height={8} preserveAspectRatio='none' viewBox='0 0 100 8' aria-hidden='true'>
    <rect width={100} height={8} rx={2} fill='currentColor' opacity={0.08} />
    <rect width={whole ? (100 * part) / whole : 0} height={8} rx={2}
          fill={theme.palette.error.main} />
  </svg>
  );
};

/** One line per domain over the last thirty days. A single day is a dot and a
 * sentence rather than a line: a trend of one point is not a trend. */
const DomainTrend: React.FC<{ domains: Data['domains'] }> = ({ domains }) => {
  const t = useT();
  const days = Array.from(new Set(domains.flatMap(d => d.points.map(p => p.run_at)))).sort();
  if (days.length < 2) {
    return (
      <>
        {domains.map(d => (
          <Typography key={d.domain} variant='body2'>
            {`${d.domain}: ${pct(d.points[d.points.length - 1]?.score)}`}
          </Typography>
        ))}
        <Typography variant='caption' color='texts.secondary'>
          {t('The trend line starts with the second daily run.')}
        </Typography>
      </>
    );
  }
  const w = 420;
  const h = 120;
  const lo = Math.min(...domains.flatMap(d => d.points.map(p => p.score)), 0.8);
  const x = (day: string) => (days.indexOf(day) / (days.length - 1)) * w;
  const y = (s: number) => h - ((s - lo) / (1 - lo || 1)) * h;
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} width='100%' height={h} aria-hidden='true'>
        {domains.map((d, i) => (
          <polyline key={d.domain} fill='none' strokeWidth={2}
                    stroke={`hsl(${(i * 67) % 360} 55% 45%)`}
                    points={d.points.map(p => `${x(p.run_at)},${y(p.score)}`).join(' ')} />
        ))}
      </svg>
      {domains.map((d, i) => (
        <Typography key={d.domain} variant='caption' component='span'
                    style={{ marginRight: 12, color: `hsl(${(i * 67) % 360} 55% 45%)` }}>
          {`■ ${d.domain} ${pct(d.points[d.points.length - 1]?.score)}`}
        </Typography>
      ))}
    </div>
  );
};

export default QualityOverview;
