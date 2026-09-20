import React, { useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import { AppTabs, Button, EmptyContentPlaceholder, Input, Table } from 'components/shared/elements';
import * as Layout from 'components/shared/styled-components/layout';
import type { Arrival, FlowJob, Hub, IntegrationFlow, IntegrationState, Totals } from './api';
import { getIntegration, runJobs, seatunnelUrl } from './api';
import { Code, tr, useT, when } from './shared';
import { HeldPanel, RecordPanel } from './RecordDetail';
import { FlowPanel } from './FlowEditor';
import { HubHealth } from './Health';
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
 * What connects to what is the flow files' to say (ADR 0019) -- and since
 * #109 a flow is edited here too: opening one shows its map, its value maps
 * and its two SeaTunnel settings, and saving rewrites its file. The refusals
 * are the same ones `--check` makes, because it is the same code.
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
          {state && <Everything />}
          {state?.hubs.map(hub => <HubCard key={hub.id} hub={hub} />)}
          {!!state?.totals?.length && <TotalsCard rows={state.totals} />}
        </S.Shell>
      </Layout.Content>
    </Layout.LayoutContainer>
  );
};

/** Everything at once: start the flows that are not running -- which is what
 * `--apply` does, resuming each from its checkpoint (ADR 0023) -- and the way
 * to SeaTunnel's own console, where the engine's detail lives. */
const Everything: React.FC = () => {
  const t = useT();
  const [said, setSaid] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    try {
      const result = await runJobs(null, 'apply');
      setSaid([...result.said, ...result.refused.map(r => `${tr('refused')}: ${r}`)]);
    } catch (e) {
      setSaid([(e as Error).message]);
    } finally {
      setBusy(false);
    }
  };
  return (
    <S.Actions>
      <Button
        buttonType='secondary-m'
        text={t('Start every flow that is not running')}
        isLoading={busy}
        onClick={start}
      />
      <Typography variant='body2'>
        <a href={seatunnelUrl()} target='_blank' rel='noreferrer'>
          {t('SeaTunnel console')}
        </a>
      </Typography>
      {said.map(line => (
        <Typography key={line} variant='caption' color='texts.secondary' component='div'>
          {line}
        </Typography>
      ))}
    </S.Actions>
  );
};

/** One-way aggregates (#81): many rows of one system become one row per
 * group in another. No hub, no conflicts -- a total has no inverse -- so
 * the question is only whether both of its jobs run and what they hold. */
const TotalsCard: React.FC<{ rows: Totals[] }> = ({ rows }) => {
  const t = useT();
  return (
    <S.Panel>
      <div>
        <Typography variant='h3'>{t('One-way totals')}</Typography>
        <Typography variant='caption' color='texts.secondary'>
          {t('Many rows of one system summed into one row per group in another. A total cannot come back as rows, so these only go one way.')}
        </Typography>
      </div>
      <Table.HeaderContainer>
        <Table.Cell $flex={1.6}>
          <Typography variant='caption'>{t('Flow')}</Typography>
        </Table.Cell>
        <Table.Cell $flex={1.6}>
          <Typography variant='caption'>{t('Rows in')}</Typography>
        </Table.Cell>
        <Table.Cell $flex={1.6}>
          <Typography variant='caption'>{t('Totals out')}</Typography>
        </Table.Cell>
      </Table.HeaderContainer>
      {rows.map(r => (
        <Table.RowContainer key={r.flow}>
          <Table.Cell $flex={1.6}>
            <div>
              <Typography variant='body1'>{r.flow}</Typography>
              <Typography variant='caption' color='texts.secondary' component='div'>
                {r.from} → {r.to}
              </Typography>
              <Typography variant='caption' color='texts.secondary' component='div'>
                {t('per {{group}}: {{aggregates}}', {
                  group: Object.values(r.group).join(', '),
                  aggregates: Object.entries(r.aggregates).map(([c, e]) => `${c} = ${e}`).join(', '),
                })}
              </Typography>
            </div>
          </Table.Cell>
          <Table.Cell $flex={1.6}>
            <div>
              <FlowLine flow={{ flow: r.flow, match: {}, job: r.jobs.in }} count='read' />
              <Typography variant='caption' color='texts.secondary' component='div'>
                {t('{{n}} lines held', { n: r.lines ?? '—' })}
                {r.landed_at ? ` · ${when(r.landed_at)}` : ''}
              </Typography>
            </div>
          </Table.Cell>
          <Table.Cell $flex={1.6}>
            <div>
              <FlowLine flow={{ flow: `${r.flow}_out`, match: {}, job: r.jobs.out }} count='written' />
              <Typography variant='caption' color='texts.secondary' component='div'>
                {t('{{n}} totals', { n: r.groups ?? '—' })}
              </Typography>
              {r.error && (
                <Typography variant='caption' color='error.main' component='div'>
                  {r.error}
                </Typography>
              )}
            </div>
          </Table.Cell>
        </Table.RowContainer>
      ))}
    </S.Panel>
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
      {!hub.hub_error && <HubHealth hub={hub} />}
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
  const [open, setOpen] = useState<string | null>(null);
  const toggle = (flow: string) => setOpen(open === flow ? null : flow);
  return (
    <div>
      <Typography variant='caption' color='texts.secondary'>
        {t('Open a flow to see and change what it carries: its map, its value maps, and how often SeaTunnel checkpoints it.')}
      </Typography>
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
        <React.Fragment key={s.table}>
          <Table.RowContainer>
            <Table.Cell $flex={1.4}>
              <div>
                <Typography variant='body1'>{s.title}</Typography>
                <Typography variant='caption' color='texts.secondary'>
                  {s.table}
                </Typography>
                {s.drift.map(d => (
                  <Typography key={d} variant='caption' color='error.main' component='div'>
                    {d}
                  </Typography>
                ))}
              </div>
            </Table.Cell>
            <Table.Cell $flex={1.6}>
              <div>
                {s.in.map(f => (
                  <FlowLine key={f.flow} flow={f} count='read' open={open} onOpen={toggle} />
                ))}
              </div>
            </Table.Cell>
            <Table.Cell $flex={1.6}>
              <div>
                {s.out.map(f => (
                  <FlowLine key={f.flow} flow={f} count='written' open={open} onOpen={toggle} />
                ))}
              </div>
            </Table.Cell>
            <Table.Cell $flex={1.4}>
              <Arrived arrival={hub.arriving?.[s.table]} />
            </Table.Cell>
          </Table.RowContainer>
          {open && [...s.in, ...s.out].some(f => f.flow === open) && (
            <FlowPanel id={open} />
          )}
        </React.Fragment>
      ))}
    </div>
  );
};

/** A flow by its own name, and whether its job runs. A stopped job and a
 * running idle one differ here; whether anything arrives is the next column.
 * Its name opens the flow's own settings (FlowEditor.tsx) when there is
 * somewhere to open them -- the totals card passes no handler. */
const FlowLine: React.FC<{
  flow: IntegrationFlow;
  count: 'read' | 'written';
  open?: string | null;
  onOpen?: (flow: string) => void;
}> = ({ flow, count, open, onOpen }) => {
  const t = useT();
  const job = flow.job;
  const match = Object.entries(flow.match).map(([c, v]) => `${c} = ${v}`).join(', ');
  return (
    <div>
      <Typography
        variant='body2'
        role={onOpen ? 'button' : undefined}
        tabIndex={onOpen ? 0 : undefined}
        aria-expanded={onOpen ? open === flow.flow : undefined}
        style={onOpen ? { cursor: 'pointer' } : undefined}
        onClick={onOpen ? () => onOpen(flow.flow) : undefined}
        onKeyDown={
          onOpen
            ? e => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onOpen(flow.flow);
                }
              }
            : undefined
        }
      >
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

/** A log line that opens into its record, or its held row, on a click or
 * Enter (RecordDetail.tsx): the line says what happened, the opened record
 * says what it is now and how it got there. */
const Line: React.FC<{
  id: string;
  open: string | null;
  setOpen: (id: string | null) => void;
  span: number;
  detail: () => React.ReactNode;
  children: React.ReactNode;
}> = ({ id, open, setOpen, span, detail, children }) => {
  const toggle = () => setOpen(open === id ? null : id);
  return (
    <>
      <tr
        role='button'
        tabIndex={0}
        aria-expanded={open === id}
        onClick={toggle}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggle();
          }
        }}
        style={{ cursor: 'pointer' }}
      >
        {children}
      </tr>
      {open === id && (
        <tr>
          <td colSpan={span} style={{ whiteSpace: 'normal' }}>
            {detail()}
          </td>
        </tr>
      )}
    </>
  );
};

const Opens: React.FC = () => {
  const t = useT();
  return (
    <Typography variant='caption' color='texts.secondary'>
      {t('Open a line to see its record: every field, who set it, and what each system sent.')}
    </Typography>
  );
};

/** A log grows; a person looking at one knows a code, a system or a field.
 * One box over the row's own words, rather than a filter per column (#111). */
const Search: React.FC<{ value: string; onChange: (v: string) => void; found: number }> = ({
  value,
  onChange,
  found,
}) => {
  const t = useT();
  return (
    <S.Actions>
      <Input
        variant='main-m'
        label={t('Search')}
        placeholder={t('a code, a system, a field')}
        value={value}
        onChange={e => onChange(e.target.value)}
      />
      {!!value && (
        <Typography variant='caption' color='texts.secondary'>
          {t('{{n}} of them match', { n: found })}
        </Typography>
      )}
    </S.Actions>
  );
};

/** Everything the row says, as one lower-case string to search in. */
const searchable = (...parts: unknown[]) =>
  parts
    .map(p => (p && typeof p === 'object' ? JSON.stringify(p) : String(p ?? '')))
    .join(' ')
    .toLowerCase();

const Conflicts: React.FC<{ hub: Hub }> = ({ hub }) => {
  const t = useT();
  const [open, setOpen] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const all = hub.conflicts ?? [];
  const rows = all.filter(c =>
    searchable(c.key, c.codes, c.field, c.kept, c.lost, c.kept_by, c.lost_by, c.reason)
      .includes(query.toLowerCase())
  );
  if (!all.length) return <Quiet text={t('No value has lost a conflict.')} />;
  return (
    <S.Scroll>
      <Opens />
      <Search value={query} onChange={setQuery} found={rows.length} />
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
            <Line
              key={`${c.at}-${c.field}-${JSON.stringify(c.key)}`}
              id={`${c.at}-${c.field}-${JSON.stringify(c.key)}`}
              open={open}
              setOpen={setOpen}
              span={6}
              detail={() => <RecordPanel hub={hub.id} recordKey={c.key} />}
            >
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
                  : c.reason === 'unmapped'
                  ? t('its value map does not know the value: the hub kept its own')
                  : t('both edited it: the later commit won')}
              </td>
            </Line>
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
  const [open, setOpen] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const all = hub.held ?? [];
  const rows = all.filter(h =>
    searchable(h.system, h.local, h.reason, h.row).includes(query.toLowerCase())
  );
  if (!all.length) return <Quiet text={t('Every row found its record.')} />;
  return (
    <>
      <Typography variant='body2' color='texts.secondary'>
        {t(
          'The hub could not tell which record these rows belong to, so it made none: a duplicate made quietly is worse than a wait. A person decides, and what was held follows.'
        )}
      </Typography>
      <Search value={query} onChange={setQuery} found={rows.length} />
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
              <Line
                key={`${h.system}-${JSON.stringify(h.local)}`}
                id={`${h.system}-${JSON.stringify(h.local)}`}
                open={open}
                setOpen={setOpen}
                span={5}
                detail={() => <HeldPanel hub={hub.id} system={h.system} local={h.local} />}
              >
                <td>{when(h.at)}</td>
                <td>{h.system}</td>
                <td>{Object.values(h.local).join(', ')}</td>
                <td>{held(h.reason)}</td>
                <td>{fields(h.row)}</td>
              </Line>
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
  const [open, setOpen] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const all = hub.deleted ?? [];
  const rows = all.filter(d =>
    searchable(d.key, d.codes, d.deleted_by).includes(query.toLowerCase())
  );
  if (!all.length) return <Quiet text={t('Nothing deleted.')} />;
  return (
    <S.Scroll>
      <Opens />
      <Search value={query} onChange={setQuery} found={rows.length} />
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
            <Line
              key={JSON.stringify(d.key)}
              id={JSON.stringify(d.key)}
              open={open}
              setOpen={setOpen}
              span={3}
              detail={() => <RecordPanel hub={hub.id} recordKey={d.key} />}
            >
              <td>{d.deleted_at ? when(d.deleted_at) : '—'}</td>
              <td>{record(d.key, d.codes)}</td>
              <td>{d.deleted_by}</td>
            </Line>
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
