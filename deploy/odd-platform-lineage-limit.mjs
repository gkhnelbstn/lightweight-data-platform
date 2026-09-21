/**
 * Do not draw a group's lineage that would freeze the browser.
 *
 * ODD lays out a data entity group's lineage with ELK on the main thread,
 * after rendering every node to measure it. For a schema of an ERP -- Siber's
 * `dbo`: 3 740 nodes, 9 391 edges -- the tab never recovers. Two anchored
 * lines in DEGLineage.tsx: an import, and a guard placed after the last hook
 * that renders deploy/odd-platform-ui/LineageTooLarge.tsx instead of the graph
 * past LINEAGE_LIMIT nodes. Fails the build when either anchor moves.
 *
 *   node deploy/odd-platform-lineage-limit.mjs <odd-platform checkout>
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.argv[2];
if (!root) {
  console.error('usage: node odd-platform-lineage-limit.mjs <odd-platform checkout>');
  process.exit(2);
}

const file = join(
  root,
  'odd-platform-ui/src/components/DataEntityDetails/Lineage/DEGLineage/DEGLineage.tsx'
);
const anchors = [
  {
    find: "import * as S from './DEGLineage.styles';",
    add: "\nimport { LINEAGE_LIMIT, LineageTooLarge } from '../../../DataQuality/Contracts/LineageTooLarge';",
  },
  {
    find: '  const [isLayouted] = useAtom(isLayoutedAtom);',
    add: '\n\n  if (isSuccess && new Set(rawNodes.map(n => n.id)).size > LINEAGE_LIMIT) {\n    return <LineageTooLarge nodes={rawNodes} edges={rawEdges} />;\n  }',
  },
];

let source = readFileSync(file, 'utf8');
for (const { find, add } of anchors) {
  if (source.split(find).length !== 2) {
    console.error(`odd-platform-lineage-limit: anchor not found exactly once in ${file}:\n${find}`);
    process.exit(1);
  }
  source = source.replace(find, find + add);
}
writeFileSync(file, source);
console.log('odd-platform-lineage-limit: a group lineage past the limit is listed, not drawn');
