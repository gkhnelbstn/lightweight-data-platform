import React, { useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import { AppTabs, EmptyContentPlaceholder, Table } from 'components/shared/elements';
import * as Layout from 'components/shared/styled-components/layout';
import type { Arrival, FlowJob, Hub, IntegrationFlow, IntegrationState } from './api';
import { getIntegration } from './api';
import { Code, tr, useT, when } from './shared';
import * as S from './Contracts.styles';

/**
 * The two-way integration, on a tab of its own in ODD's menu (#78, ADR 0022).
 *
 * SeaTunnel's own console names jobs by id and draws their vertices, which
 * answers nothing a person here asks. This names each job by the flow it runs
 * and says which systems it connects, and puts beside it what only the hub
 * knows: whether changes are arriving and how late, which values lost a
 * conflict, what is waiting for a person, and what was deleted. A rule whose
 * losses nobody can see is the muted channel core/alerts.py exists to avoid.
 *
 * Read-only: what connects to what is the flow files' to say (ADR 0019).
 */
const TABS = ['Flows', 'Conflicts', 'Waiting for a decision', 'Deleted'];
const REFRESH_MS = 10_000;

const Integration: React.FC = () => {
  const t = useT();
  const [state, setState] = useState<IntegrationState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      getIntegration()
        .then(s => {
          setState(s);
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <Layout.LayoutContainer>
      <Layout.Content>
        {/* The whole page, not a column beside a filter sidebar. */}
        <S.Shell style={{ width: '100%' }}>
          <Typography variant='h1'>{t('Integration')}</Typography>
          <Typography variant='subtitle2' color='texts.secondary'>
            {t(
              'Systems that edit the same records meet in a hub, and the latest commit wins. SeaTunnel carries every change into the hub and the hub’s record back out to every system.'
            )}
          </Typography>
          {error && (
            <Typography variant='body1' color='error.main'>
              {t('Contract service unreachable: {{error}}', { error })}
            </Typography>
          )}
          {!state && !error && (
            <Typography variant='body2' color='texts.secondary'>
              {t('Loading…')}
            </Typography>
          )}
          {state?.seatunnel_error && (
            <Typography variant='body2' color='error.main'>
              {t('SeaTunnel unreachable: {{error}}', { error: state.seatunnel_error })}
            </Typography>
          )}
          {state?.problems.map(p => (
            <Typography key={p} variant='caption' color='error.main'>
              {p}
            </Typography>
          ))}
          {state && (
            <EmptyContentPlaceholder
              isContentEmpty={state.hubs.length === 0}
              fullPage={false}
              text={t('No hub contract with flows yet.')}
            />
          )}
          {state?.hubs.map(hub => <HubCard key={hub.id} hub={hub} />)}
        </S.Shell>
      </Layout.Content>
    </Layout.LayoutContainer>
  );
};

const HubCard: React.FC<{ hub: Hub }> = ({ hub }) => {
  const t = useT();
  const [tab, setTab] = useState(0);
  const counts = [null, hub.conflicts?.length, hub.held?.length, hub.deleted?.length];
  const codes = Object.values(hub.codes);
  return (
    <S.Panel>
      <div>
        <Typography variant='h3'>{hub.title}</Typography>
        <Typography variant='caption' color='texts.secondary'>
          {t('{{n}} records · the first sync goes to {{system}}', {
            n: hub.records ?? '—',
            system: hub.authority,
          })}
          {codes.length > 0 &&
            ` · ${t('each system keeps its own code: {{columns}}', { columns: codes.join(', ') })}`}
        </Typography>
      </div>
      {hub.hub_error && (
        <Typography variant='body2' color='error.main'>
          {t('Hub database unreachable: {{error}}', { error: hub.hub_error })}
        </Typography>
      )}
      <AppTabs
        type='primary'
        selectedTab={tab}
        handleTabChange={setTab}
        items={TABS.map((name, i) => ({
          name: tr(name),
          hint: counts[i] || undefined,
          // Held rows wait for a person; the other counts are history.
          hintType: i === 2 ? 'alert' : undefined,
        }))}
      />
      {tab === 0 && <Flows hub={hub} />}
      {tab === 1 && <Conflicts hub={hub} />}
      {tab === 2 && <Waiting hub={hub} />}
      {tab === 3 && <Deleted hub={hub} />}
    </S.Panel>
  );
};

const Flows: React.FC<{ hub: Hub }> = ({ hub }) => {
  const t = useT();
  return (
    <div>
      <Table.HeaderContainer>
        <Table.Cell $flex={1.4}>
          <Typography variant='caption'>{t('System')}</Typography>
        </Table.Cell>
        <Table.Cell $flex={1.6}>
          <Typography variant='caption'>{t('Into the hub')}</Typography>
        </Table.Cell>
        <Table.Cell $flex={1.6}>
          <Typography variant='caption'>{t('Back out')}</Typography>
        </Table.Cell>
        <Table.Cell $flex={1.4}>
          <Typography variant='caption'>{t('Last change in')}</Typography>
        </Table.Cell>
      </Table.HeaderContainer>
      {hub.systems.map(s => (
        <Table.RowContainer key={s.table}>
          <Table.Cell $flex={1.4}>
            <div>
              <Typography variant='body1'>{s.title}</Typography>
              <Typography variant='caption' color='texts.secondary'>
                {s.table}
              </Typography>
            </div>
          </Table.Cell>
          <Table.Cell $flex={1.6}>
            <div>{s.in.map(f => <FlowLine key={f.flow} flow={f} count='read' />)}</div>
          </Table.Cell>
          <Table.Cell $flex={1.6}>
            <div>{s.out.map(f => <FlowLine key={f.flow} flow={f} count='written' />)}</div>
          </Table.Cell>
          <Table.Cell $flex={1.4}>
            <Arrived arrival={hub.arriving?.[s.table]} />
          </Table.Cell>
        </Table.RowContainer>
      ))}
    </div>
  );
};

/** A flow by its own name, and whether its job runs. A stopped job and a
 * running idle one differ here; whether anything arrives is the next column. */
const FlowLine: React.FC<{ flow: IntegrationFlow; count: 'read' | 'written' }> = ({
  flow,
  count,
}) => {
  const t = useT();
  const job = flow.job;
  const match = Object.entries(flow.match).map(([c, v]) => `${c} = ${v}`).join(', ');
  return (
    <div>
      <Typography variant='body2'>
        {flow.flow}
        {match && (
          <Typography component='span' variant='caption' color='texts.secondary'>
            {` · ${match}`}
          </Typography>
        )}
      </Typography>
      <Typography variant='caption' color={jobColor(job)} component='div'>
        {jobState(job)}
        {job && job.status === 'RUNNING' &&
          ` · ${count === 'read'
            ? t('{{n}} changes read', { n: job.read })
            : t('{{n}} rows written', { n: job.written })}`}
      </Typography>
    </div>
  );
};

const jobColor = (job: FlowJob | null) => {
  if (job?.status === 'RUNNING') return 'success.main';
  if (job?.status === 'FAILED') return 'error.main';
  return 'texts.secondary';
};

const jobState = (job: FlowJob | null) => {
  if (!job) return tr('not submitted');
  if (job.status === 'RUNNING') return tr('running');
  if (job.status === 'FAILED') return tr('failed: {{error}}', { error: job.error ?? '' });
  if (job.status === 'CANCELED') return tr('stopped {{when}}', { when: job.finished ?? '' });
  return job.status ?? '';
};

const Arrived: React.FC<{ arrival?: Arrival }> = ({ arrival }) => {
  const t = useT();
  if (!arrival?.committed_at) {
    return (
      <Typography variant='body2' color='texts.secondary'>
        {t('nothing yet')}
      </Typography>
    );
  }
  const late = arrival.landed_at
    ? Math.max(0, (Date.parse(arrival.landed_at) - Date.parse(arrival.committed_at)) / 1000)
    : null;
  return (
    <div>
      <Typography variant='body2'>{when(arrival.committed_at)}</Typography>
      <Typography variant='caption' color='texts.secondary' component='div'>
        {t('in the hub {{s}} s after its commit · {{n}} in the last hour', {
          s: late === null ? '—' : late.toFixed(1),
          n: arrival.last_hour,
        })}
      </Typography>
    </div>
  );
};

/** A record as a person knows it: by its systems' codes, not the hub's number. */
const record = (key: Record<string, unknown>, codes?: Record<string, unknown> | null) => {
  const known = Object.entries(codes ?? {}).filter(([, v]) => v !== null && v !== undefined);
  if (known.length) return known.map(([s, v]) => `${s} ${v}`).join(' · ');
  return Object.values(key).join(', ');
};

const value = (v: unknown) => {
  if (v === null || v === undefined) return '—';
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
};

/** A held row as what a person would compare: its values, empty ones left out. */
const fields = (row: Record<string, unknown>) =>
  Object.entries(row)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}: ${value(v)}`)
    .join(' · ');

const Conflicts: React.FC<{ hub: Hub }> = ({ hub }) => {
  const t = useT();
  const rows = hub.conflicts ?? [];
  if (!rows.length) return <Quiet text={t('No value has lost a conflict.')} />;
  return (
    <S.Scroll>
      <S.Cells>
        <thead>
          <tr>
            <th>{t('When')}</th>
            <th>{t('Record')}</th>
            <th>{t('Field')}</th>
            <th>{t('Kept')}</th>
            <th>{t('Lost')}</th>
            <th>{t('Why')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(c => (
            <tr key={`${c.at}-${c.field}-${JSON.stringify(c.key)}`}>
              <td>{when(c.at)}</td>
              <td>{record(c.key, c.codes)}</td>
              <td>{c.field === '*' ? t('the whole record') : c.field}</td>
              <td>
                {value(c.kept)}
                <Side system={c.kept_by} at={c.kept_at} />
              </td>
              <td>
                {value(c.lost)}
                <Side system={c.lost_by} at={c.lost_at} />
              </td>
              <td>
                {c.reason === 'seed'
                  ? t('first sync: the authority stands')
                  : t('both edited it: the later commit won')}
              </td>
            </tr>
          ))}
        </tbody>
      </S.Cells>
    </S.Scroll>
  );
};

const Side: React.FC<{ system: string | null; at: string | null }> = ({ system, at }) => (
  <Typography variant='caption' color='texts.secondary' component='div'>
    {[system, at && new Date(at).toLocaleString()].filter(Boolean).join(', ')}
  </Typography>
);

const Waiting: React.FC<{ hub: Hub }> = ({ hub }) => {
  const t = useT();
  const rows = hub.held ?? [];
  if (!rows.length) return <Quiet text={t('Every row found its record.')} />;
  return (
    <>
      <Typography variant='body2' color='texts.secondary'>
        {t(
          'The hub could not tell which record these rows belong to, so it made none: a duplicate made quietly is worse than a wait. A person decides, and what was held follows.'
        )}
      </Typography>
      <S.Scroll>
        <S.Cells>
          <thead>
            <tr>
              <th>{t('Since')}</th>
              <th>{t('System')}</th>
              <th>{t('Code')}</th>
              <th>{t('Why')}</th>
              <th>{t('Row')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(h => (
              <tr key={`${h.system}-${JSON.stringify(h.local)}`}>
                <td>{when(h.at)}</td>
                <td>{h.system}</td>
                <td>{Object.values(h.local).join(', ')}</td>
                <td>{held(h.reason)}</td>
                <td>{fields(h.row)}</td>
              </tr>
            ))}
          </tbody>
        </S.Cells>
      </S.Scroll>
      <Typography variant='caption' color='texts.secondary'>
        <Code k='A person links a code to a record on the hub database with <c>hub.link(entity, system, code, record)</c>; without a record, the code becomes a record of its own.' />
      </Typography>
    </>
  );
};

const held = (reason: string) => {
  if (reason === 'ambiguous') return tr('several records match');
  if (reason === 'taken') return tr('the match already has another code from this system');
  if (reason === 'unmatchable') return tr('nothing to match by');
  if (reason === 'part') return tr('waits for the row that owns the record');
  return reason;
};

const Deleted: React.FC<{ hub: Hub }> = ({ hub }) => {
  const t = useT();
  const rows = hub.deleted ?? [];
  if (!rows.length) return <Quiet text={t('Nothing deleted.')} />;
  return (
    <S.Scroll>
      <S.Cells>
        <thead>
          <tr>
            <th>{t('When')}</th>
            <th>{t('Record')}</th>
            <th>{t('Deleted by')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(d => (
            <tr key={JSON.stringify(d.key)}>
              <td>{d.deleted_at ? when(d.deleted_at) : '—'}</td>
              <td>{record(d.key, d.codes)}</td>
              <td>{d.deleted_by}</td>
            </tr>
          ))}
        </tbody>
      </S.Cells>
    </S.Scroll>
  );
};

const Quiet: React.FC<{ text: string }> = ({ text }) => (
  <Typography variant='body2' color='texts.secondary'>
    {text}
  </Typography>
);

export default Integration;
