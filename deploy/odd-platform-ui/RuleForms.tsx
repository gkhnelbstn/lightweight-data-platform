import React, { useCallback, useState } from 'react';
import { MenuItem, Typography } from '@mui/material';
import { AppSelect, Button, Input } from 'components/shared/elements';
import type {
  ContractDetail,
  PreviewResult,
  RuleDraft,
  RuleType,
  StructuredRule,
  SyncRule,
  SyncRuleDraft,
} from './api';
import {
  previewRule,
  previewStructured,
  saveRule,
  saveStructured,
  saveSyncRule,
} from './api';
import * as S from './Contracts.styles';

/**
 * Authoring: the three forms that write back into the contract file.
 *
 * This platform reports quality and does not let anyone change it -- there is
 * no "create test" anywhere in its UI, because a test arrives through
 * ingestion. The contract behind those tests is editable, and this is where
 * that belongs. Saving writes an ODCS entry into `contracts/*.odcs.yaml` and
 * re-runs it: the contract stays the source of truth (invariant 1).
 */

/** A preview, whichever route produced it. */
export type PreviewShape = PreviewResult & { description?: string; query?: string };

export interface RuleFormProps {
  detail: ContractDetail;
  dimensions: string[];
  ruleTypes: RuleType[];
  onSaved: () => void;
}

/**
 * A rule chosen rather than written.
 *
 * The vocabulary comes from the service, not from here, so adding a rule kind
 * is a change in one place. The SQL is composed there too, which is why this
 * form needs no token: there is no statement for a caller to smuggle in.
 */
export const RuleBuilder: React.FC<RuleFormProps> = ({
  detail,
  dimensions,
  ruleTypes,
  onSaved,
}) => {
  const columns = detail.properties.map(p => p.name);
  const [column, setColumn] = useState(columns[0] ?? '');
  const [kind, setKind] = useState(ruleTypes[0]?.kind ?? '');
  const [params, setParams] = useState<Record<string, string>>({});
  const [dimension, setDimension] = useState('');
  const [preview, setPreview] = useState<PreviewShape | string | null>(null);
  const [busy, setBusy] = useState(false);

  const selected = ruleTypes.find(r => r.kind === kind);

  const rule = useCallback((): StructuredRule => {
    const values: Record<string, unknown> = {};
    (selected?.parameters ?? []).forEach(p => {
      const value = params[p.name] ?? '';
      if (p.type === 'list') {
        values[p.name] = value
          .split(',')
          .map(v => v.trim())
          .filter(Boolean);
      } else if (p.type === 'number') {
        values[p.name] = value === '' ? null : Number(value);
      } else {
        values[p.name] = value;
      }
    });
    return {
      contract_id: detail.contract.id,
      kind,
      column,
      params: values,
      ...(dimension ? { dimension } : {}),
    };
  }, [detail.contract.id, kind, column, params, dimension, selected]);

  const run = useCallback(
    async (save: boolean) => {
      setBusy(true);
      try {
        if (save) {
          await saveStructured(rule());
          setPreview(null);
          onSaved();
        } else {
          setPreview(await previewStructured(rule()));
        }
      } catch (e) {
        setPreview((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [rule, onSaved]
  );

  return (
    <>
      <S.Actions>
        <AppSelect id='rule-column' label='Column' value={column} onChange={e => setColumn(e.target.value as string)}>
          {columns.map(c => (
            <MenuItem key={c} value={c}>
              {c}
            </MenuItem>
          ))}
        </AppSelect>
        <AppSelect
          id='rule-kind'
          label='Rule'
          value={kind}
          onChange={e => {
            setKind(e.target.value as string);
            setParams({});
            setPreview(null);
          }}
        >
          {ruleTypes.map(r => (
            <MenuItem key={r.kind} value={r.kind}>
              {r.label}
            </MenuItem>
          ))}
        </AppSelect>
        <AppSelect
          id='rule-dimension'
          label='Dimension — weights the score'
          value={dimension}
          onChange={e => setDimension(e.target.value as string)}
        >
          <MenuItem value=''>default ({selected?.dimension ?? '—'})</MenuItem>
          {dimensions.map(d => (
            <MenuItem key={d} value={d}>
              {d}
            </MenuItem>
          ))}
        </AppSelect>
      </S.Actions>

      {(selected?.parameters ?? []).map(p => (
        <Input
          key={p.name}
          variant='main-m'
          type={p.type === 'number' ? 'number' : 'text'}
          label={p.label}
          value={params[p.name] ?? ''}
          onChange={e => setParams({ ...params, [p.name]: e.target.value })}
        />
      ))}

      <S.Actions>
        <Button
          buttonType='secondary-m'
          text='Preview'
          isLoading={busy}
          onClick={() => run(false)}
        />
        <Button
          buttonType='main-m'
          text='Save and run'
          isLoading={busy}
          onClick={() => run(true)}
        />
      </S.Actions>
      <PreviewResultView preview={preview} />
    </>
  );
};

/** The escape hatch: SQL someone wrote, so it carries the token. */
export const RawSqlRule: React.FC<Omit<RuleFormProps, 'ruleTypes'>> = ({
  detail,
  dimensions,
  onSaved,
}) => {
  const [draft, setDraft] = useState<RuleDraft>({
    contract_id: detail.contract.id,
    description: '',
    query: `select count(*) from ${detail.contract.source_table}\nwhere `,
    dimension: dimensions.includes('conformity')
      ? 'conformity'
      : (dimensions[0] ?? 'unknown'),
    must_be: 0,
  });
  const [token, setToken] = useState(
    () => window.localStorage.getItem('dq_token') ?? ''
  );
  const [preview, setPreview] = useState<PreviewShape | string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (save: boolean) => {
      setBusy(true);
      window.localStorage.setItem('dq_token', token);
      try {
        if (save) {
          await saveRule(draft, token);
          setPreview(null);
          onSaved();
        } else {
          setPreview(await previewRule(draft, token));
        }
      } catch (e) {
        setPreview((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [draft, token, onSaved]
  );

  return (
    <>
      <Input
        variant='main-m'
        label='Description — becomes the test name'
        value={draft.description}
        onChange={e => setDraft({ ...draft, description: e.target.value })}
      />
      <S.Actions>
        <AppSelect
          id='raw-dimension'
          label='Dimension — weights the score'
          value={draft.dimension}
          onChange={e => setDraft({ ...draft, dimension: e.target.value as string })}
        >
          {dimensions.map(d => (
            <MenuItem key={d} value={d}>
              {d}
            </MenuItem>
          ))}
        </AppSelect>
      </S.Actions>
      <div>
        <Typography variant='caption' color='texts.secondary'>
          SQL — must return one number, the count of bad rows. Do not pin the
          day&apos;s window yourself, and on Postgres do not qualify the schema:
          the runner points the query at the window view.
        </Typography>
        <S.Textarea
          value={draft.query}
          onChange={e => setDraft({ ...draft, query: e.target.value })}
        />
      </div>
      <Input
        variant='main-m'
        type='password'
        label='API token — only this route needs one, and the service prints it at startup'
        value={token}
        onChange={e => setToken(e.target.value)}
      />
      <S.Actions>
        <Button
          buttonType='secondary-m'
          text='Preview'
          isLoading={busy}
          onClick={() => run(false)}
        />
        <Button
          buttonType='main-m'
          text='Save and run'
          isLoading={busy}
          onClick={() => run(true)}
        />
      </S.Actions>
      <PreviewResultView preview={preview} />
    </>
  );
};

/**
 * Author this contract's own replication rule. The Replication tab only ever
 * lists a contract once it already has one -- this is the other half, see
 * issue #10: there was previously no way to add or change a syncTo rule
 * without hand-editing the contract's YAML.
 */
export const SyncRuleForm: React.FC<{ detail: ContractDetail; onSaved: () => void }> = ({
  detail,
  onSaved,
}) => {
  // The source and the daily-window schema are already excluded server-side
  // -- see GET /api/contracts/{id} -- neither is a sensible replication
  // target even though nothing here would catch it as unsound.
  const targets = detail.servers;
  const [server, setServer] = useState(targets[0]?.server ?? '');
  const [filter, setFilter] = useState('');
  const [columns, setColumns] = useState('');
  const [identity, setIdentity] = useState('');
  const [token, setToken] = useState(() => window.localStorage.getItem('dq_token') ?? '');
  const [result, setResult] = useState<string | { rule: SyncRule['rule'] } | null>(null);
  const [busy, setBusy] = useState(false);

  if (targets.length === 0) return null;

  const save = async () => {
    setBusy(true);
    window.localStorage.setItem('dq_token', token);
    const draft: SyncRuleDraft = {
      contract_id: detail.contract.id,
      server,
      filter: filter || undefined,
      columns: columns ? columns.split(',').map(c => c.trim()).filter(Boolean) : undefined,
      identity: identity ? identity.split(',').map(c => c.trim()).filter(Boolean) : undefined,
    };
    try {
      const saved = await saveSyncRule(draft, token);
      setResult({ rule: saved.rule });
      onSaved();
    } catch (e) {
      setResult((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Typography variant='subtitle2' color='texts.secondary'>
        Written as this contract&apos;s own <code>syncTo</code> custom property, then
        checked against ADR 0008&apos;s four preconditions before it is saved --
        rejected with the specific reason if it would not actually replicate.
      </Typography>
      <S.Actions>
        <AppSelect
          id='sync-target'
          label='Target server'
          value={server}
          onChange={e => setServer(e.target.value as string)}
        >
          {targets.map(t => (
            <MenuItem key={t.server} value={t.server}>
              {t.server} ({t.type})
            </MenuItem>
          ))}
        </AppSelect>
        <Input
          variant='main-m'
          label="Row filter — optional, e.g. country = 'TR'"
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
      </S.Actions>
      <S.Actions>
        <Input
          variant='main-m'
          label='Columns — optional, comma separated; empty replicates all'
          value={columns}
          onChange={e => setColumns(e.target.value)}
        />
        <Input
          variant='main-m'
          label='Widen identity — optional, only if the filter needs it'
          value={identity}
          onChange={e => setIdentity(e.target.value)}
        />
      </S.Actions>
      <Input
        variant='main-m'
        type='password'
        label='API token — the service prints it at startup'
        value={token}
        onChange={e => setToken(e.target.value)}
      />
      <Button buttonType='main-m' text='Save' isLoading={busy} onClick={save} />
      {result &&
        (typeof result === 'string' ? (
          <Typography variant='body2' color='error.main'>
            {result}
          </Typography>
        ) : (
          <Typography variant='body2' color='success.main'>
            Saved. {result.rule.filter ?? 'Replicates everything'} to {result.rule.server}.
          </Typography>
        ))}
    </>
  );
};

export const PreviewResultView: React.FC<{
  preview: PreviewShape | string | null;
}> = ({ preview }) => {
  if (!preview) return null;
  if (typeof preview === 'string') {
    return (
      <Typography variant='body2' color='error.main'>
        {preview}
      </Typography>
    );
  }
  return (
    <div>
      {preview.description && (
        <Typography variant='body1'>{preview.description}</Typography>
      )}
      <Typography
        variant='body2'
        color={preview.ok ? 'success.main' : 'error.main'}
      >
        {preview.ok
          ? `compiled · result ${preview.result} · failing rows ${preview.failed_rows ?? '—'}`
          : (preview.error ?? preview.reason ?? 'the rule did not compile')}
      </Typography>
      {(preview.compiled_sql ?? preview.query) && (
        <S.Sql>{preview.compiled_sql ?? preview.query}</S.Sql>
      )}
    </div>
  );
};
