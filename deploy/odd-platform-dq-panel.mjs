/**
 * Render the Contracts panel inside ODD Platform's own Data Quality page.
 *
 * Two lines against upstream, and they are a patch rather than a vendored copy
 * of the file so that an upstream change to the page is a build failure here
 * rather than a silent revert of everything they did to it. The panel itself
 * is ours and lives in `deploy/odd-platform-ui/`.
 *
 *   node deploy/odd-platform-dq-panel.mjs <odd-platform checkout>
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.argv[2];
if (!root) {
  console.error('usage: node odd-platform-dq-panel.mjs <odd-platform checkout>');
  process.exit(2);
}

const file = join(
  root,
  'odd-platform-ui/src/components/DataQuality/DataQualityContent.tsx'
);
let source = readFileSync(file, 'utf8');

const anchors = [
  {
    find: "import TestCategoryResults from './TestResults/TestCategoryResults';",
    add: "\nimport { Contracts } from './Contracts/Contracts';",
  },
  {
    find: `      <S.Section>
        <S.DashboardLegend>
          <S.DashboardLegendItem $status={DataEntityRunStatus.SUCCESS}>`,
    replace: `      <S.Section>
        <Contracts />
      </S.Section>
      <S.Section>
        <S.DashboardLegend>
          <S.DashboardLegendItem $status={DataEntityRunStatus.SUCCESS}>`,
  },
];

for (const anchor of anchors) {
  if (!source.includes(anchor.find)) {
    console.error(
      `FATAL: anchor not found in DataQualityContent.tsx:\n${anchor.find}\n\n` +
        'Upstream moved it. Re-read the file and update this patch rather ' +
        'than pinning an older ODD_VERSION and forgetting why.'
    );
    process.exit(1);
  }
  source = anchor.replace
    ? source.replace(anchor.find, anchor.replace)
    : source.replace(anchor.find, anchor.find + anchor.add);
}

writeFileSync(file, source);
console.log('DataQualityContent.tsx: Contracts panel added');
