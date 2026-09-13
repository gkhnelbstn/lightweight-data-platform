import React, { useState } from 'react';
import { MenuItem, Typography } from '@mui/material';
import { AppSelect, Button, Input } from 'components/shared/elements';
import type { CheckRow } from './api';
import { setCheckStatus } from './api';
import * as S from './Contracts.styles';

/**
 * What someone said about a failing check. Issue #29.
 *
 * Four `order_id` uniqueness checks were red three days running and there was
 * no way to say "known, the join is the cause" -- so every morning the next
 * person rediscovered them. This is the smallest thing that stops that.
 *
 * Three states and no workflow: `open` is the absence of a note, an
 * `acknowledged` check is one somebody has looked at, and an `accepted` one is
 * a failure the team has decided to live with. No assignment and no due date,
 * because both need somebody to assign it *to* and there is no identity
 * provider (ADR 0010). For the same reason this records what and when, never
 * who.
 *
 * An accepted check still counts in the score. The list hides it by default
 * and that is all accepting does: a measurement anyone can silence from a form
 * stops being a measurement.
 */

const STATES: { value: CheckRow['state']; label: string; help: string }[] = [
  { value: 'open', label: 'Open', help: 'Nobody has looked at this yet.' },
  {
    value: 'acknowledged',
    label: 'Acknowledged',
    help: 'Somebody has seen it. It still shows in the list and still counts.',
  },
  {
    value: 'accepted',
    label: 'Accepted',
    help: 'A failure being lived with. Hidden from the list by default, and ' +
      'still counted in the score.',
  },
];

interface Props {
  check: CheckRow;
  onSaved: () => void;
}

export const CheckStatusForm: React.FC<Props> = ({ check, onSaved }) => {
  const [state, setState] = useState<CheckRow['state']>(check.state);
  const [note, setNote] = useState(check.note ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await setCheckStatus(check.check_id, {
        state,
        note,
        // The run that was on screen when this was written. It gates nothing;
        // it is what lets the list say "acknowledged three days ago, still
        // failing" rather than implying the note was about today.
        noted_run_at: check.run_at,
      });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const help = STATES.find(s => s.value === state)?.help;
  const dirty = state !== check.state || note !== (check.note ?? '');

  return (
    <div>
      <Typography variant='h4'>Status</Typography>
      {check.state !== 'open' && check.noted_at && (
        <Typography variant='caption' color='texts.secondary' component='div'>
          {check.state} on {new Date(check.noted_at).toLocaleString()}
          {check.noted_run_at && ` · about the ${check.noted_run_at} run`}
        </Typography>
      )}
      <S.Actions>
        <AppSelect
          id={`state-${check.check_id}`}
          label='State'
          value={state}
          onChange={e => setState(e.target.value as CheckRow['state'])}
        >
          {STATES.map(s => (
            <MenuItem key={s.value} value={s.value}>
              {s.label}
            </MenuItem>
          ))}
        </AppSelect>
        <Input
          variant='main-m'
          label='Note — what is known about it, for whoever opens this next'
          value={note}
          onChange={e => setNote(e.target.value)}
        />
        <Button
          buttonType='main-m'
          text='Save'
          disabled={!dirty}
          isLoading={busy}
          onClick={save}
        />
      </S.Actions>
      {help && (
        <Typography variant='caption' color='texts.secondary'>
          {help}
        </Typography>
      )}
      {error && (
        <Typography variant='body2' color='error.main'>
          {error}
        </Typography>
      )}
    </div>
  );
};
