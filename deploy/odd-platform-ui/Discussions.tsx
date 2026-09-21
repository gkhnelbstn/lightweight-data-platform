import React, { useCallback, useEffect, useState } from 'react';
import { MenuItem, Typography } from '@mui/material';
import { AppSelect, Button, Input } from 'components/shared/elements';
import { useDataEntityRouteParams } from 'routes';
import type { ChatSpace, DiscussionMessage } from './api';
import {
  addChatSpace,
  deleteChatSpace,
  getChatSpaces,
  getDiscussion,
  postDiscussion,
} from './api';
import { useT, when } from './shared';
import * as S from './Contracts.styles';

/**
 * An asset's Discussions tab, in Google Chat instead of Slack (ADR 0028).
 *
 * deploy/odd-platform-discussions.mjs points the tab's route here. A message
 * is kept by the contract service and posted to the chosen Google Chat space,
 * in the thread that belongs to this asset. Replies typed in Chat stay in
 * Chat: an incoming webhook can post and cannot read.
 *
 * The spaces are set up here too, below the thread. Adding one needs the API
 * token, because its webhook URL is a credential and a place the server will
 * POST to; the URL never comes back to this screen.
 */
export const Discussions: React.FC = () => {
  const t = useT();
  const { dataEntityId } = useDataEntityRouteParams();
  const [spaces, setSpaces] = useState<ChatSpace[]>([]);
  const [messages, setMessages] = useState<DiscussionMessage[]>([]);
  const [spaceId, setSpaceId] = useState<number | ''>('');
  const [author, setAuthor] = useState(() => window.localStorage.getItem('dq_author') ?? '');
  const [body, setBody] = useState('');
  const [entityName, setEntityName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getChatSpaces()
      .then(s => {
        setSpaces(s);
        setSpaceId(current => (current === '' && s.length ? s[0].id : current));
      })
      .catch((e: Error) => setError(e.message));
    getDiscussion(dataEntityId)
      .then(setMessages)
      .catch((e: Error) => setError(e.message));
  }, [dataEntityId]);

  useEffect(() => {
    load();
    // The asset's name goes into the Chat message; this platform's own API has it.
    fetch(`/api/dataentities/${dataEntityId}`)
      .then(r => r.json())
      .then(e => setEntityName(e.internal_name || e.external_name || ''))
      .catch(() => setEntityName(''));
  }, [load, dataEntityId]);

  const send = async () => {
    if (spaceId === '' || !author.trim() || !body.trim()) return;
    setBusy(true);
    setError(null);
    window.localStorage.setItem('dq_author', author.trim());
    try {
      const sent = await postDiscussion(dataEntityId, {
        space_id: spaceId,
        author: author.trim(),
        body: body.trim(),
        entity_name: entityName,
      });
      if (!sent.delivered) setError(t('Saved, but Google Chat refused it: {{e}}', { e: sent.error }));
      else setBody('');
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <S.Shell>
      <Typography variant='h4'>{t('Discussion')}</Typography>
      {spaces.length === 0 ? (
        <Typography variant='body2' color='texts.secondary'>
          {t('No Google Chat space yet. Add one below, with its incoming-webhook URL.')}
        </Typography>
      ) : (
        <S.Panel>
          <S.Actions>
            <AppSelect id='dq-space' label={t('Google Chat space')} value={spaceId}
                       onChange={e => setSpaceId(Number(e.target.value))}>
              {spaces.map(s => (
                <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
              ))}
            </AppSelect>
            <Input variant='main-m' label={t('Your name')} value={author}
                   onChange={e => setAuthor(e.target.value)} />
          </S.Actions>
          <S.Textarea value={body} placeholder={t('Write about this asset…')}
                      onChange={e => setBody(e.target.value)} maxLength={4000} />
          <S.Actions>
            <Button buttonType='main-m' text={t('Send to Google Chat')} isLoading={busy}
                    disabled={!author.trim() || !body.trim()} onClick={send} />
          </S.Actions>
        </S.Panel>
      )}
      {error && <Typography color='error.main' variant='body2'>{error}</Typography>}

      {messages.map(m => (
        <S.PropertyRow key={m.id}>
          <Typography variant='body2'>
            <b>{m.author}</b>
            <Typography component='span' variant='caption' color='texts.secondary'>
              {` · ${when(m.created_at)} · ${m.space ?? '—'}`}
              {m.delivered ? '' : ` · ${t('not delivered')}`}
            </Typography>
          </Typography>
          <Typography variant='body1' style={{ whiteSpace: 'pre-wrap' }}>{m.body}</Typography>
        </S.PropertyRow>
      ))}
      {messages.length === 0 && (
        <Typography variant='body2' color='texts.secondary'>
          {t('Nothing has been said about this asset yet.')}
        </Typography>
      )}

      <SpaceSettings spaces={spaces} onChanged={load} />
    </S.Shell>
  );
};

/** Add or remove a Google Chat space. Token-guarded on the server. */
const SpaceSettings: React.FC<{ spaces: ChatSpace[]; onChanged: () => void }> = ({
  spaces,
  onChanged,
}) => {
  const t = useT();
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');
  const [token, setToken] = useState(() => window.localStorage.getItem('dq_token') ?? '');
  const [error, setError] = useState<string | null>(null);

  const act = async (work: () => Promise<unknown>) => {
    setError(null);
    window.localStorage.setItem('dq_token', token);
    try {
      await work();
      setName('');
      setUrl('');
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <S.Panel>
      <Typography variant='h5'>{t('Google Chat spaces')}</Typography>
      {spaces.map(s => (
        <S.Actions key={s.id}>
          <Typography variant='body2'>
            {s.name}
            <Typography component='span' variant='caption' color='texts.secondary'>
              {` · ${s.target}`}
            </Typography>
          </Typography>
          <Button buttonType='secondary-sm' text={t('Remove')}
                  onClick={() => act(() => deleteChatSpace(s.id, token))} />
        </S.Actions>
      ))}
      <S.Actions>
        <Input variant='main-m' label={t('Name')} value={name}
               onChange={e => setName(e.target.value)} />
        <Input variant='main-m' type='password' label={t('Incoming-webhook URL')} value={url}
               onChange={e => setUrl(e.target.value)} />
        <Input variant='main-m' type='password' label={t('API token')} value={token}
               onChange={e => setToken(e.target.value)} />
        <Button buttonType='secondary-m' text={t('Add space')} disabled={!name || !url || !token}
                onClick={() => act(() => addChatSpace(name, url, token))} />
      </S.Actions>
      <Typography variant='caption' color='texts.secondary'>
        {t('In Google Chat: the space’s menu → Apps & integrations → Webhooks. The URL is kept on the server and shown here only masked.')}
      </Typography>
      {error && <Typography color='error.main' variant='body2'>{error}</Typography>}
    </S.Panel>
  );
};

export default Discussions;
