import React, { useEffect, useState } from 'react';
import { Typography } from '@mui/material';
import { Input } from 'components/shared/elements';
import type { CatalogueNode, ContractDetail, ForeignKey } from './api';
import { findEntityId, getNeighbours } from './api';
import { useT } from './shared';
import * as S from './Contracts.styles';

/**
 * What sits next to the contract's table: the tables its foreign keys join,
 * both ways, from the contract (#144), and whatever ODD's catalogue has one
 * step up or down its lineage -- views reading it, jobs and flows writing it,
 * charts on it. The second half is ODD's own API from the browser, since the
 * panel is part of ODD's page: nothing server-side holds a session for it.
 * A table an ERP builds hundreds of views on is listed by search, not drawn.
 */

const SHOWN = 30;
const KINDS: CatalogueNode['kind'][] = ['table', 'view', 'job', 'consumer', 'input', 'other'];

export const ContractRelated: React.FC<{
  detail: ContractDetail;
  onOpenContract?: (id: string) => void;
}> = ({ detail, onOpenContract }) => {
  const t = useT();
  const { references, referenced_by: incoming } = detail.agreement;
  const [entity, setEntity] = useState<number | null | undefined>(undefined);
  const [nodes, setNodes] = useState<{ up: CatalogueNode[]; down: CatalogueNode[] } | null>(null);
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (!detail.oddrn) {
      setEntity(null);
      return;
    }
    let live = true;
    findEntityId(detail.oddrn)
      .then(async id => {
        if (!live) return;
        setEntity(id);
        if (id == null) return;
        const [up, down] = await Promise.all([
          getNeighbours(id, 'upstream'),
          getNeighbours(id, 'downstream'),
        ]);
        if (live) setNodes({ up, down });
      })
      .catch(() => live && setEntity(null));
    return () => {
      live = false;
    };
  }, [detail.oddrn]);

  const label: Record<CatalogueNode['kind'], string> = {
    table: t('Tables'),
    view: t('Views'),
    job: t('Jobs and flows'),
    consumer: t('Dashboards and charts'),
    input: t('Inputs'),
    other: t('Other'),
  };

  return (
    <S.Panel>
      <Typography variant='h5'>{t('Related')}</Typography>
      {entity != null && (
        <Typography variant='body2'>
          <a href={`/dataentities/${entity}/overview`}>{t('Open the table in the catalogue')}</a>
          {' · '}
          <a href={`/dataentities/${entity}/lineage`}>{t('Lineage')}</a>
        </Typography>
      )}

      <Keys title={t('Its foreign keys point at')} keys={references} arrow='→'
            onOpenContract={onOpenContract} />
      <Keys title={t('Tables pointing at it')} keys={incoming} arrow='←'
            onOpenContract={onOpenContract} />

      {entity === null && (
        <Typography variant='caption' color='texts.secondary'>
          {t('The table is not in the catalogue yet, so its lineage is not known here.')}
        </Typography>
      )}
      {nodes && (
        <Neighbours nodes={nodes} label={label} query={query} setQuery={setQuery} />
      )}
    </S.Panel>
  );
};

const Keys: React.FC<{
  title: string;
  keys: ForeignKey[];
  arrow: string;
  onOpenContract?: (id: string) => void;
}> = ({ title, keys, arrow, onOpenContract }) => {
  if (keys.length === 0) return null;
  return (
    <div>
      <Typography variant='subtitle2'>{`${title} (${keys.length})`}</Typography>
      {keys.map(k => (
        <S.PropertyRow key={`${k.table}.${k.column}.${k.to_column}`}>
          <Typography variant='body2'>
            {`${k.column} ${arrow} `}
            {k.contract && onOpenContract ? (
              <a
                href={`?dq_tab=Contracts&dq_contract=${encodeURIComponent(k.contract)}`}
                onClick={e => {
                  e.preventDefault();
                  onOpenContract(k.contract as string);
                }}
              >
                {k.table}
              </a>
            ) : (
              k.table
            )}
            {`.${k.to_column}`}
          </Typography>
        </S.PropertyRow>
      ))}
    </div>
  );
};

const Neighbours: React.FC<{
  nodes: { up: CatalogueNode[]; down: CatalogueNode[] };
  label: Record<CatalogueNode['kind'], string>;
  query: string;
  setQuery: (q: string) => void;
}> = ({ nodes, label, query, setQuery }) => {
  const t = useT();
  const all = [
    ...nodes.up.map(n => ({ ...n, side: t('upstream') })),
    ...nodes.down.map(n => ({ ...n, side: t('downstream') })),
  ];
  const counts = KINDS.map(k => [k, all.filter(n => n.kind === k).length] as const).filter(([, n]) => n);
  const q = query.trim().toLowerCase();
  const shown = all.filter(n => !q || n.name.toLowerCase().includes(q)).slice(0, SHOWN);
  if (all.length === 0) {
    return (
      <Typography variant='caption' color='texts.secondary'>
        {t('Nothing in the catalogue reads or writes this table.')}
      </Typography>
    );
  }
  return (
    <div>
      <Typography variant='subtitle2'>{t('In the catalogue, one step away')}</Typography>
      <Typography variant='caption' color='texts.secondary'>
        {counts.map(([k, n]) => `${label[k]}: ${n}`).join(' · ')}
      </Typography>
      {all.length > SHOWN && (
        <Input variant='main-m' placeholder={t('Search by name')} value={query}
               onChange={e => setQuery(e.target.value)} />
      )}
      {shown.map(n => (
        <S.PropertyRow key={`${n.side}-${n.id}`}>
          <Typography variant='body2'>
            <a href={`/dataentities/${n.id}/overview`}>{n.name}</a>
            <Typography component='span' variant='caption' color='texts.secondary'>
              {` · ${label[n.kind]} · ${n.side}${n.source ? ` · ${n.source}` : ''}`}
            </Typography>
          </Typography>
        </S.PropertyRow>
      ))}
      {all.length > shown.length && (
        <Typography variant='caption' color='texts.secondary'>
          {t('{{n}} more; search to narrow.', { n: all.length - shown.length })}
        </Typography>
      )}
    </div>
  );
};

export default ContractRelated;
