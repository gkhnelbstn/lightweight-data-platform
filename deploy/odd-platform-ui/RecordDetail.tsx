import React, { useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import { Button } from 'components/shared/elements';
import type { HeldDetail, HistoryEntry, RecordDetail } from './api';
import { getHeld, getRecord, linkHeld } from './api';
import { tr, useT, when } from './shared';
import * as S from './Contracts.styles';

/**
 * A line of the Integration tab, opened: what the record is now, and how it
 * got there. A conflict line says a value lost; this says who set every
 * field, what each system sent, and what the hub made of it -- above all
 * which of those "updates" were the hub's own deliveries coming back through
 * a system's CDC, which change nothing and read like edits otherwise.
 */

export const show = (v: unknown) => {
  if (v === null || v === undefined) return '—';
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
};

export const codesLine = (codes: Record<string, unknown>) =>
  Object.entries(codes)
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([s, v]) => `${s} ${v}`)
    .join(' · ');

const outcomeColor = (outcome: string | null) => {
  if (outcome === 'lost' || outcome === 'held') return 'error.main';
  if (outcome === 'applied' || outcome === 'created' || outcome === 'deleted') return 'success.main';
  return 'texts.secondary';
};

const outcomeText = (outcome: string | null) => {
  if (outcome === 'applied') return tr('applied');
  if (outcome === 'created') return tr('created the record');
  if (outcome === 'deleted') return tr('deleted the record');
  if (outcome === 'echo') return tr('echo of the hub’s own write');
  if (outcome === 'lost') return tr('lost to a later edit');
  if (outcome === 'held') return tr('held for a decision');
  if (outcome === 'unchanged') return tr('no change');
  return '—';
};

const what = (h: HistoryEntry) => {
  if (h.kind === 'delete') return tr('deleted');
  const parts = h.changes.map(c =>
    h.kind === 'insert' ? `${c.field} = ${show(c.to)}` : `${c.field}: ${show(c.from)} → ${show(c.to)}`
  );
  if (h.kind === 'insert') return `${tr('new row')}: ${parts.join(', ')}`;
  return parts.length ? parts.join(', ') : tr('nothing this flow carries changed');
};

const Loading: React.FC<{ error: string | null }> = ({ error }) => (
  <Typography variant='body2' color={error ? 'error.main' : 'texts.secondary'}>
    {error ?? tr('Loading…')}
  </Typography>
);

export const RecordPanel: React.FC<{ hub: string; recordKey: Record<string, unknown> }> = ({
  hub,
  recordKey,
}) => {
  const t = useT();
  const [detail, setDetail] = useState<RecordDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getRecord(hub, recordKey).then(setDetail).catch((e: Error) => setError(e.message));
  }, [hub, JSON.stringify(recordKey)]);
  if (!detail) return <Loading error={error} />;

  const fields = Object.entries(detail.fields);
  return (
    <S.Panel>
      <div>
        <Typography variant='h4'>{codesLine(detail.codes) || show(detail.key)}</Typography>
        {detail.deleted && (
          <Typography variant='caption' color='error.main'>
            {t('deleted {{when}} by {{system}}', {
              when: when(detail.deleted.at),
              system: detail.deleted.by,
            })}
          </Typography>
        )}
      </div>
      {fields.length > 0 && (
        <S.Scroll>
          <S.Cells>
            <thead>
              <tr>
                <th>{t('Field')}</th>
                <th>{t('Value now')}</th>
                <th>{t('Set by')}</th>
                <th>{t('When')}</th>
              </tr>
            </thead>
            <tbody>
              {fields.map(([f, v]) => (
                <tr key={f}>
                  <td>{f}</td>
                  <td>{show(v.value)}</td>
                  <td>{v.by ?? '—'}</td>
                  <td>{v.at ? new Date(v.at).toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </S.Cells>
        </S.Scroll>
      )}
      <Typography variant='subtitle1'>{t('What reached the hub')}</Typography>
      <Typography variant='caption' color='texts.secondary'>
        {t(
          'Every change a system sent for this record, oldest first, and what the hub made of it. An echo is the hub’s own delivery coming back through that system’s CDC: it changes nothing, and it is how the hub knows the delivery landed.'
        )}
      </Typography>
      {detail.history.length === 0 ? (
        <Typography variant='body2' color='texts.secondary'>
          {t('Nothing recorded for it.')}
        </Typography>
      ) : (
        <S.Scroll>
          <S.Cells>
            <thead>
              <tr>
                <th>{t('Committed')}</th>
                <th>{t('System')}</th>
                <th>{t('Change')}</th>
                <th>{t('Outcome')}</th>
              </tr>
            </thead>
            <tbody>
              {detail.history.map(h => (
                <tr key={`${h.at}-${h.system}`}>
                  <td>{new Date(h.committed_at ?? h.at).toLocaleString()}</td>
                  <td>{h.system}</td>
                  <td style={{ whiteSpace: 'normal' }}>{what(h)}</td>
                  <td>
                    <Typography variant='caption' color={outcomeColor(h.outcome)}>
                      {outcomeText(h.outcome)}
                    </Typography>
                  </td>
                </tr>
              ))}
            </tbody>
          </S.Cells>
        </S.Scroll>
      )}
    </S.Panel>
  );
};

const heldWhy = (reason: string) => {
  if (reason === 'ambiguous')
    return tr('Several records share the value this system is matched by, so the hub cannot tell which one this row is. Link it to one of them below.');
  if (reason === 'taken')
    return tr('The one record that matches already has another code from this system. Either this is a second copy of that customer in the system, or a different customer with the same value: link it, or make it a record of its own.');
  if (reason === 'unmatchable')
    return tr('The row has no value to be matched by, so the hub would have had to guess. Fill the value in the system, or decide here.');
  if (reason === 'part')
    return tr('This row is part of a record, and the row that owns the record has not placed it yet. It follows that row as soon as it arrives.');
  return reason;
};

export const HeldPanel: React.FC<{
  hub: string;
  system: string;
  local: Record<string, unknown>;
}> = ({ hub, system, local }) => {
  const t = useT();
  const [detail, setDetail] = useState<HeldDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    getHeld(hub, system, local).then(setDetail).catch((e: Error) => setError(e.message));
  }, [hub, system, JSON.stringify(local)]);

  /* The decision, taken here rather than printed as SQL for psql (#111). The
     hub's own refusals come back as the message: a record that has another
     code from this system already is a refusal, not a silent overwrite. */
  const settle = async (record: Record<string, unknown> | null) => {
    setBusy(true);
    setSaid(null);
    setRefused(null);
    try {
      const got = await linkHeld(hub, system, local, record);
      setSaid(
        record
          ? tr('Linked. The row follows that record from its next change.')
          : tr('It is a record of its own now: {{record}}', { record: show(got.record) })
      );
    } catch (e) {
      setRefused((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!detail) return <Loading error={error} />;

  const rule = Object.entries(detail.rule);
  return (
    <S.Panel>
      <Typography variant='body2'>{heldWhy(detail.reason)}</Typography>
      <Typography variant='caption' color='texts.secondary'>
        {Object.entries(detail.row)
          .filter(([, v]) => v !== null && v !== undefined)
          .map(([f, v]) => `${f}: ${show(v)}`)
          .join(' · ')}
      </Typography>
      {rule.length > 0 && (
        <Typography variant='caption' color='texts.secondary'>
          {t('matched by {{rule}}', {
            rule: rule.map(([c, v]) => `${c} = ${show(v)}`).join(', '),
          })}
        </Typography>
      )}
      {detail.candidates.length > 0 && (
        <S.Scroll>
          <S.Cells>
            <thead>
              <tr>
                <th>{t('Candidate record')}</th>
                <th>{t('Now')}</th>
                <th>{t('Decide')}</th>
              </tr>
            </thead>
            <tbody>
              {detail.candidates.map(c => (
                <tr key={JSON.stringify(c.key)}>
                  <td>{codesLine(c.codes) || show(c.key)}</td>
                  <td style={{ whiteSpace: 'normal' }}>
                    {Object.entries(c.fields)
                      .filter(([, v]) => v !== null && v !== undefined)
                      .map(([f, v]) => `${f}: ${show(v)}`)
                      .join(' · ')}
                  </td>
                  <td>
                    <Button
                      buttonType='secondary-m'
                      text={t('Link it here')}
                      isLoading={busy}
                      onClick={() => settle(c.key)}
                    />
                    <Typography variant='caption' color='texts.secondary' component='div'>
                      <code>{c.link}</code>
                    </Typography>
                  </td>
                </tr>
              ))}
            </tbody>
          </S.Cells>
        </S.Scroll>
      )}
      <S.Actions>
        <Button
          buttonType='tertiary-m'
          text={t('Make it a record of its own')}
          isLoading={busy}
          onClick={() => settle(null)}
        />
        <Typography variant='caption' color='texts.secondary'>
          <code>{detail.link_new}</code>
        </Typography>
      </S.Actions>
      {said && (
        <Typography variant='body2' color='success.main'>
          {said}
        </Typography>
      )}
      {refused && (
        <Typography variant='body2' color='error.main'>
          {refused}
        </Typography>
      )}
    </S.Panel>
  );
};
