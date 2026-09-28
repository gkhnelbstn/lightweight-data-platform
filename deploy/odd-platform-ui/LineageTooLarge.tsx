import React, { useMemo, useState } from 'react';
import { Typography } from '@mui/material';
import { Input } from 'components/shared/elements';
import { useT } from './shared';
import * as S from './Contracts.styles';

/**
 * A group's lineage that is too big to draw (deploy/odd-platform-lineage-limit.mjs).
 *
 * ODD draws a group's lineage by rendering every node to measure it and then
 * laying the whole graph out with ELK on the main thread. A schema of an ERP
 * is a group too: Siber's `dbo` answered with 1 120 subgraphs, 3 740 nodes and
 * 9 391 edges -- 3.4 MB that arrives in two seconds and then freezes the tab
 * for good. Past the limit this lists the group's entities by how connected
 * they are, and each one opens its own lineage, which ODD draws well.
 */
export const LINEAGE_LIMIT = 400;

type LineageNode = { id: string; data?: { externalName?: string; internalName?: string } };
type LineageEdge = { sources: string[]; targets: string[] };

export const LineageTooLarge: React.FC<{ nodes: LineageNode[]; edges: LineageEdge[] }> = ({
  nodes,
  edges,
}) => {
  const t = useT();
  const [query, setQuery] = useState('');

  const ranked = useMemo(() => {
    const degree = new Map<string, number>();
    edges.forEach(e =>
      [...e.sources, ...e.targets].forEach(id => degree.set(id, (degree.get(id) ?? 0) + 1))
    );
    const byId = new Map(nodes.map(n => [n.id, n]));
    return [...byId.values()]
      .map(n => ({
        id: n.id,
        name: n.data?.internalName || n.data?.externalName || n.id,
        links: degree.get(n.id) ?? 0,
      }))
      .sort((a, b) => b.links - a.links);
  }, [nodes, edges]);

  const shown = ranked
    .filter(n => n.name.toLowerCase().includes(query.trim().toLowerCase()))
    .slice(0, 50);

  return (
    <S.Shell>
      <Typography variant='h4'>{t('Too large to draw')}</Typography>
      <Typography variant='body2' color='texts.secondary'>
        {t(
          'This group’s lineage has {{nodes}} entities and {{edges}} links — drawing all of it at once freezes the browser. Open one of them to see its own lineage.',
          { nodes: ranked.length, edges: edges.length }
        )}
      </Typography>
      <Input variant='main-m' placeholder={t('Search by name')} value={query}
             onChange={e => setQuery(e.target.value)} />
      <div>
        {shown.map(n => (
          <S.PropertyRow key={n.id}>
            <Typography variant='body2'>
              <a href={`/dataentities/${n.id}/lineage`}>{n.name}</a>
              <Typography component='span' variant='caption' color='texts.secondary'>
                {` · ${t('{{n}} links', { n: n.links })}`}
              </Typography>
            </Typography>
          </S.PropertyRow>
        ))}
      </div>
    </S.Shell>
  );
};

export default LineageTooLarge;
