import React from 'react';
import { Typography } from '@mui/material';
import { LabeledInfoItem, TestRunStatusItem } from 'components/shared/elements';
import type { ContractDetail, Measured, SlaPromise } from './api';
import { ContractRelated } from './ContractRelated';
import { runStatus, useT } from './shared';
import * as S from './Contracts.styles';

/**
 * A contract as an agreement (#144): who owns it, what it is for, which rules
 * it holds, how often they run, what was promised and what was measured, and
 * what sits next to its table. OpenMetadata's contract page answered these,
 * and here each answer is the contract's own ODCS field
 * (api/contract_agreement.py) -- a promise nothing measures says so rather
 * than showing a number from somewhere else.
 */

// datacontract derives these from the schema; the rest were written into the
// contract by someone, and those are the rules a person means by "its rules".
const STRUCTURAL = new Set([
  'field_is_present',
  'field_physical_type',
  'field_type',
  'field_required',
  'field_unique',
  'field_primary_key_required',
  'field_primary_key_unique',
]);

// ODCS names, as a person reads them. A name not listed here is shown as the
// contract spells it: slaProperties and customProperties are open lists.
const useNames = () => {
  const t = useT();
  const names: Record<string, string> = {
    latency: t('Latency'),
    frequency: t('Check frequency'),
    availability: t('Availability'),
    completeness: t('Completeness'),
    incidentResponse: t('Incident response'),
    minScore: t('Minimum score'),
    timeOfAvailability: t('Available by'),
    retention: t('Retention'),
    dataClassification: t('Classification'),
    privacy: t('Personal data'),
    breakingChangePolicy: t('Breaking changes'),
    deprecationPolicy: t('Deprecation'),
    owner: t('Owner'),
    steward: t('Steward'),
  };
  return (key: string | null | undefined) => (key ? names[key] ?? key : '—');
};

/** How often a promise says, as a person says it: "daily", not "1 d". */
const usePeriod = () => {
  const t = useT();
  return (p: SlaPromise | undefined) => {
    if (!p) return null;
    const unit = String(p.unit ?? '').toLowerCase().replace(/s$/, '');
    const n = Number(p.value);
    if (['d', 'day'].includes(unit) && n === 1) return t('daily');
    if ((['d', 'day'].includes(unit) && n === 7) || (['w', 'week'].includes(unit) && n === 1)) return t('weekly');
    if (['h', 'hour'].includes(unit) && n === 1) return t('hourly');
    return t('every {{n}} {{unit}}', { n: String(p.value), unit: p.unit ?? '' });
  };
};

export const ContractOverview: React.FC<{
  detail: ContractDetail;
  onOpenContract?: (id: string) => void;
}> = ({ detail, onOpenContract }) => {
  const t = useT();
  const period = usePeriod();
  const a = detail.agreement;
  const floor = a.sla.find(p => p.property === 'minScore');
  const every = a.sla.find(p => p.property === 'frequency');
  const failing = detail.checks.filter(c => c.status === 'fail').length;
  const errored = detail.checks.filter(c => c.status === 'error').length;
  const last = a.runs[a.runs.length - 1];

  return (
    <>
      <S.Kpis>
        <Kpi label={t('Status')} value={a.status ?? '—'} note={t('version {{v}}', { v: a.version ?? '—' })} />
        <Kpi
          label={t('Score / SLA floor')}
          value={floor?.measured?.kind === 'score' ? floor.measured.value.toFixed(3) : '—'}
          note={floor ? `≥ ${String(floor.value)}` : t('no floor stated')}
          tone={floor?.measured ? (floor.measured.met ? 'good' : 'bad') : 'plain'}
        />
        <Kpi
          label={t('Checks')}
          value={`${detail.checks.length - failing - errored} / ${detail.checks.length}`}
          note={t('{{failing}} failing · {{errored}} could not run', { failing, errored })}
          tone={detail.checks.length === 0 ? 'plain' : failing ? 'bad' : 'good'}
        />
        <Kpi
          label={t('Last checked')}
          value={last ? last.as_of : t('never')}
          note={every ? t('promised: {{every}}', { every: period(every) }) : t('no frequency stated')}
          tone={every?.measured ? (every.measured.met ? 'good' : 'bad') : 'plain'}
        />
        <Kpi label={t('Owner')} value={a.owner ?? '—'} note={a.team.name ?? ''} />
      </S.Kpis>

      <S.Split>
        <div>
          <About detail={detail} />
          <Rules detail={detail} />
          <ServiceLevels sla={a.sla} />
          <Terms detail={detail} />
        </div>
        <div>
          <Ownership detail={detail} />
          <Schedule detail={detail} />
          <ContractRelated detail={detail} onOpenContract={onOpenContract} />
        </div>
      </S.Split>
    </>
  );
};

const Kpi: React.FC<{
  label: string;
  value: React.ReactNode;
  note?: string;
  tone?: 'good' | 'bad' | 'plain';
}> = ({ label, value, note, tone = 'plain' }) => (
  <S.Kpi $tone={tone}>
    <Typography variant='caption' color='texts.secondary'>{label}</Typography>
    <Typography variant='h3' style={{ wordBreak: 'break-word' }}>{value}</Typography>
    {note && <Typography variant='caption' color='texts.secondary'>{note}</Typography>}
  </S.Kpi>
);

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <S.Panel>
    <Typography variant='h5'>{title}</Typography>
    {children}
  </S.Panel>
);

const Missing: React.FC<{ what: string }> = ({ what }) => {
  const t = useT();
  return (
    <Typography variant='body2' color='texts.secondary'>
      {t('The contract does not state {{what}}.', { what })}
    </Typography>
  );
};

const About: React.FC<{ detail: ContractDetail }> = ({ detail }) => {
  const t = useT();
  const { description: d, use_cases: uses, domain, tags } = detail.agreement;
  return (
    <Section title={t('What it is for')}>
      <S.Facts>
        <LabeledInfoItem label={t('Purpose')} labelWidth={3}>{d.purpose ?? '—'}</LabeledInfoItem>
        <LabeledInfoItem label={t('Usage')} labelWidth={3}>{d.usage ?? '—'}</LabeledInfoItem>
        <LabeledInfoItem label={t('Limitations')} labelWidth={3}>{d.limitations ?? '—'}</LabeledInfoItem>
        <LabeledInfoItem label={t('Domain')} labelWidth={3}>
          {[domain, ...tags].filter(Boolean).join(' · ') || '—'}
        </LabeledInfoItem>
      </S.Facts>
      {uses.length > 0 && (
        <div>
          <Typography variant='subtitle2'>{t('Use cases')}</Typography>
          {uses.map(u => (
            <Typography key={u} variant='body2'>• {u}</Typography>
          ))}
        </div>
      )}
    </Section>
  );
};

const Rules: React.FC<{ detail: ContractDetail }> = ({ detail }) => {
  const t = useT();
  const written = detail.checks.filter(c => !STRUCTURAL.has(c.check_type ?? ''));
  const derived = detail.checks.filter(c => STRUCTURAL.has(c.check_type ?? ''));
  const byDimension = new Map<string, { total: number; failing: number }>();
  written.forEach(c => {
    const d = byDimension.get(c.dimension) ?? { total: 0, failing: 0 };
    d.total += 1;
    d.failing += c.status === 'fail' ? 1 : 0;
    byDimension.set(c.dimension, d);
  });
  const semantics = detail.agreement.semantics;
  return (
    <Section title={t('Rules')}>
      <Typography variant='caption' color='texts.secondary'>
        {[...byDimension.entries()]
          .map(([dim, n]) => t('{{dimension}}: {{total}} ({{failing}} failing)', { dimension: t(dim), ...n }))
          .join(' · ') || t('No rule has run yet.')}
      </Typography>
      <div>
        {written.map(c => (
          <S.PropertyRow key={c.check_id}>
            <S.Actions>
              <TestRunStatusItem size='small' typeName={runStatus(c.status)} count={1} />
              <Typography variant='body2' style={{ flex: 1 }}>{c.name ?? c.check_id}</Typography>
              <Typography variant='caption' color='texts.secondary'>
                {[t(c.dimension), c.field].filter(Boolean).join(' · ')}
              </Typography>
            </S.Actions>
          </S.PropertyRow>
        ))}
      </div>
      {derived.length > 0 && (
        <Typography variant='caption' color='texts.secondary'>
          {t('Plus {{n}} checks derived from the schema (columns present, types, keys): {{passed}} pass.', {
            n: derived.length,
            passed: derived.filter(c => c.status === 'pass').length,
          })}
        </Typography>
      )}
      {semantics.length > 0 && (
        <div>
          <Typography variant='subtitle2'>{t('Business rules')}</Typography>
          {semantics.map(r => (
            <S.PropertyRow key={r.name}>
              <Typography variant='body2'>{r.description ?? r.name}</Typography>
              {r.rule && (
                <Typography variant='caption' color='texts.secondary'>{r.rule}</Typography>
              )}
            </S.PropertyRow>
          ))}
        </div>
      )}
    </Section>
  );
};

const MeasuredText: React.FC<{ m: Measured | null }> = ({ m }) => {
  const t = useT();
  if (!m) return <>{t('not measured here')}</>;
  switch (m.kind) {
    case 'score':
      return <>{t('score {{v}} on {{at}}', { v: m.value.toFixed(3), at: m.as_of })}</>;
    case 'last_run':
      return <>{t('last run {{at}} ({{n}} days ago), {{runs}} runs in 30 days', { at: m.as_of, n: m.days, runs: m.runs })}</>;
    case 'checks':
      return <>{t('{{passed}} of {{total}} completeness checks pass', m)}</>;
    case 'answered':
      return <>{t('the source answered {{ok}} of {{total}} runs', m)}</>;
    default:
      return null;
  }
};

const ServiceLevels: React.FC<{ sla: SlaPromise[] }> = ({ sla }) => {
  const t = useT();
  const name = useNames();
  return (
    <Section title={t('Service levels')}>
      {sla.length === 0 && <Missing what={t('any service level')} />}
      {sla.length > 0 && (
        <S.Scroll>
          <S.Cells style={{ width: '100%' }}>
            <thead>
              <tr>
                <th>{t('Promise')}</th>
                <th>{t('Target')}</th>
                <th>{t('Measured')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sla.map(p => (
                <tr key={p.property}>
                  <td title={p.description ?? ''}>
                    {name(p.property)}
                    {p.element ? ` · ${p.element}` : ''}
                    {p.description && (
                      <Typography variant='caption' color='texts.secondary' component='div'
                                  style={{ whiteSpace: 'normal', maxWidth: 360 }}>
                        {p.description}
                      </Typography>
                    )}
                  </td>
                  <td>
                    {String(p.value)}
                    {p.unit ? ` ${p.unit}` : ''}
                    {p.schedule ? ` (${p.scheduler ?? 'cron'} ${p.schedule})` : ''}
                  </td>
                  <td style={{ whiteSpace: 'normal' }}>
                    <MeasuredText m={p.measured} />
                  </td>
                  <td>
                    {p.measured?.met === true && <Typography color='success.main'>{t('met')}</Typography>}
                    {p.measured?.met === false && <Typography color='error.main'>{t('breached')}</Typography>}
                  </td>
                </tr>
              ))}
            </tbody>
          </S.Cells>
        </S.Scroll>
      )}
    </Section>
  );
};

const Terms: React.FC<{ detail: ContractDetail }> = ({ detail }) => {
  const t = useT();
  const name = useNames();
  const { roles, terms, support } = detail.agreement;
  return (
    <Section title={t('Terms of use')}>
      {roles.length + terms.length === 0 && <Missing what={t('terms of use')} />}
      <S.Facts>
        {roles.map(r => (
          <LabeledInfoItem key={r.role} label={t('Access: {{role}}', { role: r.role })} labelWidth={4}>
            {[r.access, r.description].filter(Boolean).join(' — ')}
          </LabeledInfoItem>
        ))}
        {terms.map(term => (
          <LabeledInfoItem key={term.key} label={name(term.key)} labelWidth={4}>
            {term.value}
          </LabeledInfoItem>
        ))}
        {support.map(s => (
          <LabeledInfoItem key={s.channel} label={t('Support')} labelWidth={4}>
            {s.url ? <a href={s.url}>{s.channel}</a> : s.channel}
            {s.description && s.description !== s.channel ? ` — ${s.description}` : ''}
          </LabeledInfoItem>
        ))}
      </S.Facts>
    </Section>
  );
};

const Ownership: React.FC<{ detail: ContractDetail }> = ({ detail }) => {
  const t = useT();
  const name = useNames();
  const { owner, team } = detail.agreement;
  // The owner is `tenant` (curate.py's convention); a team member saying the
  // same is not a second owner.
  const others = team.members.filter(m => !(m.role === 'owner' && m.username === owner));
  return (
    <Section title={t('Ownership')}>
      <S.Facts>
        <LabeledInfoItem label={t('Owner')} labelWidth={3}>{owner ?? '—'}</LabeledInfoItem>
        <LabeledInfoItem label={t('Team')} labelWidth={3}>{team.name ?? '—'}</LabeledInfoItem>
        {others.map(m => (
          <LabeledInfoItem key={`${m.username}-${m.role}`} label={name(m.role)} labelWidth={3}>
            {m.name ? `${m.name} (${m.username})` : m.username}
          </LabeledInfoItem>
        ))}
      </S.Facts>
    </Section>
  );
};

const Schedule: React.FC<{ detail: ContractDetail }> = ({ detail }) => {
  const t = useT();
  const period = usePeriod();
  const { runs, location: l } = detail.agreement;
  const every = detail.agreement.sla.find(p => p.property === 'frequency');
  return (
    <Section title={t('Where and how often')}>
      <S.Facts>
        <LabeledInfoItem label={t('Table')} labelWidth={3}>
          {[l.database, l.schema, l.table].filter(Boolean).join('.')}
        </LabeledInfoItem>
        <LabeledInfoItem label={t('Server')} labelWidth={3}>
          {`${l.type ?? '?'} ${l.host ?? '?'}${l.port ? `:${l.port}` : ''}`}
        </LabeledInfoItem>
        <LabeledInfoItem label={t('Checked')} labelWidth={3}>
          {every
            ? t('{{every}}, as the contract promises', { every: period(every) })
            : t('the contract states no frequency')}
        </LabeledInfoItem>
      </S.Facts>
      <Typography variant='caption' color='texts.secondary'>
        {t('{{n}} runs in the last 30 days', { n: runs.length })}
      </Typography>
      <S.Runs>
        {runs.map(r => (
          <S.Run
            key={r.as_of}
            $status={r.errored ? 'error' : r.met ? 'pass' : 'fail'}
            title={`${r.as_of}: ${r.errored ? t('could not run') : r.met ? t('met') : t('breached')}`}
          />
        ))}
      </S.Runs>
    </Section>
  );
};

export default ContractOverview;
