/**
 * Give every lineage node its own data source icon.
 *
 * `DatasourceLogo` renders an SVG-mode logo as an SVG filter and a rect that
 * references it:
 *
 *     <defs><filter id='logo'><feImage xlinkHref={src} /></filter></defs>
 *     <rect filter='url(#logo)' ... />
 *
 * The id is a constant, and a lineage graph draws every node into *one* SVG
 * document. So the second node's `<filter id='logo'>` is a duplicate id and
 * `url(#logo)` resolves to the first one in document order -- the root's --
 * which is why a graph of a SQL Server table and a Superset chart shows the
 * SQL Server icon twice, and the Superset icon twice when rooted the other
 * way round. The API is right either way: `data_source` is correct per node in
 * `GET /api/dataentities/{id}/lineage/upstream`, checked with curl.
 *
 * The fix is the id: one per image, so each rect points at its own filter.
 * Reported as opendatadiscovery/odd-platform#1898; this is a carried patch
 * (ADR 0011) and should be deleted when that closes.
 *
 *   node deploy/odd-platform-lineage-icon.mjs <odd-platform checkout>
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.argv[2];
if (!root) {
  console.error('usage: node odd-platform-lineage-icon.mjs <odd-platform checkout>');
  process.exit(2);
}

const file = join(
  root,
  'odd-platform-ui/src/components/shared/elements/DatasourceLogo/DatasourceLogo.tsx'
);
let source = readFileSync(file, 'utf8');

const anchors = [
  {
    find: "  const src = isNameExists ? `/imgs/${parsedName}.png` : `/imgs/default.png`;",
    replace:
      "  const src = isNameExists ? `/imgs/${parsedName}.png` : `/imgs/default.png`;\n" +
      '  // One filter id per image rather than the constant `logo`: a lineage\n' +
      '  // graph puts every node in one SVG, where a duplicate id means every\n' +
      "  // rect resolves to the first filter -- the root node's icon, on all of\n" +
      '  // them. See odd-platform#1898.\n' +
      "  const filterId = `logo-${isNameExists ? parsedName : 'default'}`;",
  },
  {
    find: "        <filter id='logo'>",
    replace: '        <filter id={filterId}>',
  },
  {
    find: "      <rect filter='url(#logo)' width={width} {...props} />",
    replace: '      <rect filter={`url(#${filterId})`} width={width} {...props} />',
  },
];

for (const anchor of anchors) {
  if (!source.includes(anchor.find)) {
    console.error(
      `FATAL: anchor not found in DatasourceLogo.tsx:\n${anchor.find}\n\n` +
        'Upstream moved or fixed it. If odd-platform#1898 has been fixed, ' +
        'delete this patch and the line that runs it rather than pinning an ' +
        'older ODD_VERSION and forgetting why.'
    );
    process.exit(1);
  }
  source = source.replace(anchor.find, anchor.replace);
}

writeFileSync(file, source);
console.log('DatasourceLogo.tsx: one filter id per data source icon');
